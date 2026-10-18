"""Worker 提前结束路径与提交前取消的回归测试。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app import worker
from app.db.database import Base
from app.db.models import User
from app.db.news_models import Briefing, GenerationTask, NewsEvent, ScheduledTask, TaskExecution
from app.services.generation import EventRecord, EventSource


@pytest.mark.parametrize("cancel_during_model", [False, True])
def test_early_exit_finishes_execution(tmp_path, monkeypatch, cancel_during_model):
    """无内容和生成期间取消均结束执行记录，取消后不发布简报。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    with factory() as db:
        user = User(email="worker@example.com", password_hash="unused")
        db.add(user); db.flush()
        end = datetime.now(timezone.utc)
        task = GenerationTask(user_id=user.id, query="测试", window_start=end-timedelta(days=1), window_end=end)
        db.add(task); db.commit(); identifier = task.id

    def fake_generate(*args, **kwargs):
        """在模型结果返回前提交取消标记，模拟真实独立请求。"""
        with factory() as db:
            task = db.get(GenerationTask, identifier)
            task.cancel_requested = True
            db.commit()
        return [SimpleNamespace()], "<html>迟到结果</html>"

    fetch_limits = []
    def fake_fetch(*args, **kwargs):
        """记录 Worker 实际传给采集器的上限。"""
        fetch_limits.append(kwargs["limit"])
        return [object()] if cancel_during_model else []
    monkeypatch.setattr(worker, "fetch_articles", fake_fetch)
    monkeypatch.setattr(worker, "generate_briefing", fake_generate)
    worker.process_task(identifier)
    with factory() as db:
        task = db.get(GenerationTask, identifier)
        execution = db.scalar(select(TaskExecution).where(TaskExecution.generation_task_id == identifier))
        assert task.status == ("cancelled" if cancel_during_model else "no_events")
        assert task.limit == 5 and task.article_limit == 10
        assert fetch_limits == [10]
        assert execution.status == task.status
        assert execution.finished_at is not None
        assert db.scalar(select(Briefing.id)) is None
    engine.dispose()


def test_generated_briefing_persists_assigned_event_ids(tmp_path, monkeypatch):
    """Worker 在写入事件关联前先刷新 ORM ID，详情查询可稳定定位事件。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'worker-event-ids.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(worker.get_settings(), "html_root", tmp_path / "html")
    event = EventRecord(
        title="机器人进入汽车产线", summary="梅卡曼德展示汽车制造应用。",
        sources=[EventSource(
            title="原文标题", source="光明网", url="https://example.com/article",
            published_at="2026-10-18T00:00:00+00:00", excerpt="现场应用情况。",
        )],
    )
    with factory() as db:
        user = User(email="worker-event-ids@example.com", password_hash="unused")
        db.add(user); db.flush()
        end = datetime.now(timezone.utc)
        task = GenerationTask(
            user_id=user.id, query="具身智能", limit=1, article_limit=2,
            window_start=end-timedelta(days=1), window_end=end,
        )
        db.add(task); db.commit(); identifier = task.id

    monkeypatch.setattr(worker, "fetch_articles", lambda *args, **kwargs: [object()])
    monkeypatch.setattr(worker, "generate_briefing", lambda *args, **kwargs: ([event], "<!doctype html><html><body>brief</body></html>"))
    monkeypatch.setattr(worker, "index_events", lambda events, db=None: len(events))

    worker.process_task(identifier)

    with factory() as db:
        briefing = db.scalar(select(Briefing).where(Briefing.user_id == task.user_id))
        saved_events = db.query(NewsEvent).filter_by(briefing_id=briefing.id).all()
        assert len(saved_events) == 1
        assert briefing.event_ids == [saved_events[0].id]
        assert briefing.event_ids[0] is not None
    engine.dispose()


def test_schedule_scan_snapshots_following_and_manual_article_limits(tmp_path, monkeypatch):
    """调度入队时解析两倍默认值并固化手动文章上限。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'schedule-limits.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    now = datetime.now(timezone.utc)
    with factory() as db:
        user = User(email="schedule-limits@example.com", password_hash="unused", style={"max_events": 6})
        db.add(user); db.flush()
        for name, article_limit in (("following", None), ("manual", 15)):
            db.add(ScheduledTask(
                user_id=user.id, name=name, frequency="daily", timezone="Asia/Shanghai",
                run_at=now, query=name, limit=7, article_limit=article_limit,
                recipient_email=user.email, status="active", next_run_at=now - timedelta(seconds=1),
                is_default=(name == "following"),
            ))
        db.commit()

    worker.scan_schedules()
    with factory() as db:
        tasks = db.query(GenerationTask).order_by(GenerationTask.query).all()
        assert sorted((task.query, task.limit, task.article_limit) for task in tasks) == [
            ("manual", 7, 15), ("人工智能", 6, 12),
        ]
    engine.dispose()
