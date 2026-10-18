"""在隔离验收库中演练三个计划日的每日任务窗口和单次入队。"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings


def configure_acceptance_database() -> None:
    """把当前进程绑定到验收数据库和 HTML 目录，避免使用业务库。"""
    settings = get_settings()
    os.environ["DATABASE_URL"] = make_url(settings.database_url).set(database="dailynews_acceptance_20261018").render_as_string(hide_password=False)
    os.environ["HTML_ROOT"] = "data/acceptance/html"
    get_settings.cache_clear()


def main() -> None:
    """创建临时每日规则，连续扫描三个计划日并输出可复核证据。"""
    configure_acceptance_database()
    from app.db.database import SessionLocal
    from app.db.models import User
    from app.db.news_models import GenerationTask, ScheduledTask
    from app.worker import scan_schedules

    user = None
    schedule = None
    schedule_id = None
    try:
        with SessionLocal() as db:
            user = User(email=f"schedule-probe-{uuid4().hex}@example.invalid", password_hash="probe")
            db.add(user)
            db.flush()
            planned = [
                datetime(2026, 10, 15, 12, tzinfo=timezone.utc),
                datetime(2026, 10, 16, 12, tzinfo=timezone.utc),
                datetime(2026, 10, 17, 12, tzinfo=timezone.utc),
            ]
            schedule = ScheduledTask(
                user_id=user.id, name="验收每日规则", frequency="daily", timezone="Asia/Shanghai",
                run_at=planned[0], query="验收窗口", limit=1, recipient_email=user.email,
                status="active", next_run_at=planned[0], is_default=False,
            )
            db.add(schedule)
            db.commit()
            schedule_id = schedule.id

        evidence = []
        for point in planned:
            with SessionLocal() as db:
                current = db.get(ScheduledTask, schedule_id)
                current.next_run_at = point
                db.commit()
            assert scan_schedules() == 1
            with SessionLocal() as db:
                current = db.get(ScheduledTask, schedule_id)
                task = db.scalar(select(GenerationTask).where(GenerationTask.scheduled_task_id == schedule_id).order_by(GenerationTask.created_at.desc()))
                assert task is not None and task.status == "queued"
                evidence.append((task.planned_at.isoformat(), task.window_start.isoformat(), task.window_end.isoformat()))
                task.status = "succeeded"
                db.commit()
        assert len({item[0] for item in evidence}) == 3
        assert all(item[1] < item[2] for item in evidence)
        print({"planned_runs": evidence, "idempotent_task_count": len(evidence)})
    finally:
        if user is not None and schedule_id is not None:
            with SessionLocal() as db:
                db.execute(delete(GenerationTask).where(GenerationTask.scheduled_task_id == schedule_id))
                db.execute(delete(ScheduledTask).where(ScheduledTask.id == schedule_id))
                db.execute(delete(User).where(User.id == user.id))
                db.commit()


if __name__ == "__main__":
    main()
