"""独立 Worker：带租约的生成、调度及发送恢复。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import logging
from threading import Event, Thread
import time
from uuid import uuid4
from sqlalchemy import select, update
from app.config import get_settings
from app.db.database import SessionLocal
from app.db.models import User
from app.db.news_models import Briefing, GenerationTask, NewsEvent, ScheduledTask, TaskExecution
from app.services.scheduler import next_run, previous_calendar_day
from app.services.generation import generate_briefing, EventRecord
from app.services.news import fetch_articles
from app.services.rag import index_events
from app.services.tasking import user_snapshot
from app.services.delivery import deliver, recover_mail
from app.services.html_storage import apply_stored_html, commit_stored_html, store_html
from app.services.briefing_metadata import extract_briefing_title

logger = logging.getLogger("dailynews.worker")


def utc(value: datetime) -> datetime:
    """兼容测试 SQLite 丢失的 UTC 时区，生产时间始终带时区。"""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@contextmanager
def heartbeat(task_id: str, owner: str):
    """在阻塞外部请求期间续租；所有写入都校验租约所有者。"""
    stop = Event()
    seconds = get_settings().worker_lease_seconds
    def renew():
        """只续期自己仍拥有的运行任务，异常留给过期恢复。"""
        while not stop.wait(max(seconds / 3, 1)):
            try:
                with SessionLocal() as db:
                    db.execute(update(GenerationTask).where(GenerationTask.id == task_id, GenerationTask.status == "running", GenerationTask.lease_owner == owner).values(lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=seconds)))
                    db.commit()
            except Exception:
                logger.error("lease renewal failed task=%s", task_id)
    thread = Thread(target=renew, daemon=True); thread.start()
    try:
        yield
    finally:
        stop.set(); thread.join(timeout=2)


def process_task(task_id: str) -> None:
    """领取并执行生成；迟到的旧租约不能提交简报或发送邮件。"""
    owner = str(uuid4()); settings = get_settings()
    with SessionLocal() as db:
        task = db.scalar(select(GenerationTask).where(GenerationTask.id == task_id, GenerationTask.status == "queued").with_for_update(skip_locked=True))
        if not task:
            return
        task.status = "running"; task.lease_owner = owner
        task.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.worker_lease_seconds)
        user = db.get(User, task.user_id)
        if not user:
            task.status = "system_failed"; task.error_code = "USER_MISSING"; db.commit(); return
        if not task.snapshot:
            task.snapshot = user_snapshot(user)
        snapshot = dict(task.snapshot)
        execution = TaskExecution(user_id=task.user_id, generation_task_id=task.id, status="running", phase="collecting")
        db.add(execution); db.commit(); execution_id = execution.id
        try:
            with heartbeat(task_id, owner):
                db.refresh(task)
                if task.cancel_requested:
                    task.status = "cancelled"; db.commit(); return
                cached = task.intermediate
                if cached and task.intermediate_expires_at and utc(task.intermediate_expires_at) > datetime.now(timezone.utc):
                    events = [EventRecord.model_validate(item) for item in cached["events"]]
                    articles = []
                else:
                    events = None
                    articles = fetch_articles(task.query, start=utc(task.window_start), end=utc(task.window_end), limit=task.article_limit, language=snapshot.get("language", "zh-CN"), timezone_name=snapshot.get("timezone", "Asia/Shanghai"), include_terms=snapshot.get("include_terms", []), exclude_terms=snapshot.get("exclude_terms", []))
                    if not articles:
                        task.status = "no_events"; task.error_code = "NO_EVENTS"; db.commit(); return
                execution.phase = "processing"; db.commit()
                def save_events(items):
                    """HTML 前保存成功资料，仅保留七天且不进入正式历史索引。"""
                    with SessionLocal() as temporary:
                        current = temporary.scalar(select(GenerationTask).where(GenerationTask.id == task_id, GenerationTask.lease_owner == owner, GenerationTask.status == "running").with_for_update())
                        if current and not current.cancel_requested:
                            current.intermediate = {"events": [item.model_dump() for item in items]}
                            current.intermediate_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
                            temporary.commit()
                events, html = generate_briefing(articles, task.query, snapshot["language"], snapshot["style"] | {"max_events": task.limit}, events=events, on_events=save_events, time_window={"start": task.window_start.isoformat(), "end": task.window_end.isoformat()})
                task = db.scalar(select(GenerationTask).where(GenerationTask.id == task_id).with_for_update().execution_options(populate_existing=True))
                # 取消与正式提交共用行锁；租约丢失的迟到结果必须丢弃。
                if task.lease_owner != owner or task.status != "running" or utc(task.lease_expires_at) <= datetime.now(timezone.utc):
                    return
                if task.cancel_requested:
                    task.status = "cancelled"; task.intermediate = None; task.intermediate_expires_at = None; db.commit(); return
                if not events:
                    task.status = "no_events"; task.error_code = "NO_EVENTS"; db.commit(); return
                briefing = Briefing(user_id=user.id, title=extract_briefing_title(html, f"{task.query} 新闻简报"), query=task.query, event_count=len(events), html_path="")
                db.add(briefing); db.flush()
                saved_events = []
                for event in events:
                    source = event.sources[0]
                    saved = NewsEvent(briefing_id=briefing.id, user_id=user.id, title=event.title, source=source.source, url=source.url, published_at=datetime.fromisoformat(source.published_at), summary=event.summary, facts=event.model_dump())
                    db.add(saved); saved_events.append(saved)
                stored_html = store_html(settings.html_root, user.id, briefing.id, html)
                apply_stored_html(briefing, stored_html); briefing.event_ids = [event.id for event in saved_events]
                task.briefing_id = briefing.id; task.status = "succeeded"; task.error_code = None
                task.intermediate = None; task.intermediate_expires_at = None
                execution.event_count = len(saved_events); commit_stored_html(db, briefing, stored_html, settings.html_root)
                try:
                    index_events(saved_events, db=db)
                except Exception as exc:
                    db.rollback()
                    for event in saved_events:
                        event.index_status = "pending"; event.index_error = type(exc).__name__[:100]
                    db.commit(); logger.error("index pending task=%s error=%s", task_id, type(exc).__name__)
                if task.send_email:
                    deliver(db, briefing, snapshot["recipient_email"])
        except Exception as exc:
            db.rollback()
            current = db.scalar(select(GenerationTask).where(GenerationTask.id == task_id).with_for_update())
            if current and current.status == "running" and current.lease_owner == owner:
                current.status = "cancelled" if current.cancel_requested else "generation_failed"
                current.error_code = type(exc).__name__[:64]
                if current.cancel_requested:
                    current.intermediate = None; current.intermediate_expires_at = None
                db.commit()
            logger.error("generation failed task=%s error=%s", task_id, type(exc).__name__)
        finally:
            db.rollback()
            latest = db.get(GenerationTask, task_id); record = db.get(TaskExecution, execution_id)
            if latest and record and latest.lease_owner == owner and latest.status not in {"queued", "running"}:
                record.status = latest.status; record.phase = "completed" if latest.status == "succeeded" else latest.status
                record.error_code = latest.error_code; record.finished_at = datetime.now(timezone.utc)
                latest.lease_owner = None; latest.lease_expires_at = None
                db.commit()


def recover_tasks(now: datetime) -> int:
    """恢复过期生成租约最多三次，取消优先，清理超过七天的暂存。"""
    with SessionLocal() as db:
        rows = db.scalars(select(GenerationTask).where(GenerationTask.status == "running", GenerationTask.lease_expires_at <= now).with_for_update(skip_locked=True)).all()
        for task in rows:
            records = db.scalars(select(TaskExecution).where(TaskExecution.generation_task_id == task.id, TaskExecution.status == "running")).all()
            for record in records:
                record.status = "system_failed"; record.phase = "lease_expired"; record.error_code = "LEASE_EXPIRED"; record.finished_at = now
            if task.cancel_requested:
                task.status = "cancelled"; task.intermediate = None; task.intermediate_expires_at = None
            elif task.recovery_count >= 3:
                task.status = "system_failed"; task.error_code = "RECOVERY_LIMIT"
            else:
                task.status = "queued"; task.recovery_count += 1
            task.lease_owner = None; task.lease_expires_at = None
        db.execute(update(GenerationTask).where(GenerationTask.intermediate_expires_at <= now).values(intermediate=None, intermediate_expires_at=None))
        db.commit(); recover_mail(db, now)
        return len(rows)


def scan_schedules(recovery_boundary: datetime | None = None) -> int:
    """锁定到期规则并生成不可变执行快照；恢复扫描不补发停机错过任务。"""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        rows = db.scalars(select(ScheduledTask).where(ScheduledTask.status == "active", ScheduledTask.next_run_at <= now).with_for_update(skip_locked=True).limit(100)).all()
        for scheduled in rows:
            user = db.scalar(select(User).where(User.id == scheduled.user_id).with_for_update())
            planned = utc(scheduled.next_run_at)
            start, end = (utc(scheduled.window_start), utc(scheduled.window_end)) if scheduled.window_start and scheduled.window_end else previous_calendar_day(planned, scheduled.timezone)
            status = "queued"; error = None
            if recovery_boundary and planned < recovery_boundary:
                status = "missed"; error = "MISSED_DURING_DOWNTIME"
            elif scheduled.frequency == "once" and not scheduled.window_start:
                status = "system_failed"; error = "TIME_RANGE_REQUIRED"
            elif db.scalar(select(GenerationTask.id).where(GenerationTask.user_id == user.id, GenerationTask.status.in_(["queued", "running"]))):
                status = "skipped_busy"; error = "USER_BUSY"
            query = (" ".join(user.topics + user.keywords) or "人工智能") if scheduled.is_default else scheduled.query
            event_limit = user.style.get("max_events", 5) if scheduled.is_default else scheduled.limit
            task = GenerationTask(user_id=user.id, query=query, limit=event_limit, article_limit=scheduled.article_limit if scheduled.article_limit is not None else event_limit * 2, window_start=start, window_end=end, status=status, error_code=error, scheduled_task_id=scheduled.id, planned_at=planned, snapshot=user_snapshot(user, scheduled.overrides), send_email=True)
            db.add(task); db.flush()
            if status != "queued":
                db.add(TaskExecution(user_id=user.id, generation_task_id=task.id, status=status, phase=status, error_code=error, finished_at=now))
            scheduled.last_run_at = planned
            if scheduled.frequency == "daily":
                scheduled.next_run_at = next_run("daily", scheduled.run_at, scheduled.timezone, now=now)
            else:
                scheduled.status = "missed" if status == "missed" else "completed"
        db.commit(); return len(rows)


def run_worker(poll_interval: float = 2.0) -> None:
    """使用配置的执行槽位轮询持久任务，调度与生成互不阻塞。"""
    boundary = datetime.now(timezone.utc); recover_tasks(boundary); scan_schedules(boundary)
    concurrency = max(1, get_settings().worker_concurrency)
    futures = {}
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        while True:
            recover_tasks(datetime.now(timezone.utc)); scan_schedules()
            futures = {key: future for key, future in futures.items() if not future.done()}
            if len(futures) < concurrency:
                with SessionLocal() as db:
                    ids = db.scalars(select(GenerationTask.id).where(GenerationTask.status == "queued").order_by(GenerationTask.created_at).limit(concurrency * 2)).all()
                for task_id in ids:
                    if task_id not in futures and len(futures) < concurrency:
                        futures[task_id] = pool.submit(process_task, task_id)
            time.sleep(poll_interval)


def main() -> None:
    """解析轮询参数并运行独立 Worker。"""
    parser = argparse.ArgumentParser(description="DailyNews worker")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args(); logging.basicConfig(level=logging.INFO)
    run_worker(max(args.poll_interval, 0.2))

if __name__ == "__main__":
    main()
