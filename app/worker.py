"""独立的 PostgreSQL 简报任务 Worker。"""
import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from app.config import get_settings
from app.db.database import SessionLocal
from app.db.models import User
from app.db.news_models import Briefing, GenerationTask, NewsEvent, ScheduledTask
from app.services.scheduler import next_run
from app.services.generation import generate_briefing
from app.services.news import fetch_articles

logger = logging.getLogger("dailynews.worker")

def process_task(task_id: str) -> None:
    """处理指定任务并将简报文件与数据库结果提交。"""
    settings = get_settings()
    with SessionLocal() as db:
        task = db.get(GenerationTask, task_id)
        if not task or task.status != "queued": return
        # 行锁避免多个 Worker 同时领取同一任务；状态提交后其他 Worker 会跳过。
        task = db.scalar(select(GenerationTask).where(GenerationTask.id == task_id, GenerationTask.status == "queued").with_for_update(skip_locked=True))
        if not task: return
        task.status = "running"; db.commit()
        user = db.get(User, task.user_id)
        if not user:
            task.status = "system_failed"; task.error_code = "USER_MISSING"; db.commit(); return
        try:
            db.refresh(task)
            if task.cancel_requested:
                task.status = "cancelled"; db.commit(); return
            articles = fetch_articles(task.query, start=task.window_start, end=task.window_end, limit=task.limit)
            db.refresh(task)
            if task.cancel_requested:
                task.status = "cancelled"; db.commit(); return
            if not articles:
                task.status = "no_events"; task.error_code = "NO_EVENTS"; db.commit(); return
            events, html = generate_briefing(articles, task.query, user.language, user.style)
            db.refresh(task)
            if task.cancel_requested:
                task.status = "cancelled"; db.commit(); return
            if not events:
                task.status = "no_events"; task.error_code = "NO_EVENTS"; db.commit(); return
            briefing = Briefing(user_id=user.id, title=f"{task.query} 新闻简报", query=task.query, event_count=len(events), html_path="")
            db.add(briefing); db.flush()
            for event in events:
                source = event.sources[0]
                db.add(NewsEvent(briefing_id=briefing.id, user_id=user.id, title=event.title, source=source.source, url=source.url, published_at=datetime.fromisoformat(source.published_at), summary=event.summary, facts={"items":event.facts,"sources":[item.model_dump() for item in event.sources]}))
            directory = settings.html_root / user.id; directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{briefing.id}.html"; temp_path = path.with_suffix(".tmp")
            temp_path.write_text(html, encoding="utf-8"); temp_path.replace(path)
            briefing.html_path = str(path); task.briefing_id = briefing.id; task.status = "succeeded"; task.error_code = None; db.commit()
        except Exception as exc:
            db.rollback()
            task = db.get(GenerationTask, task_id)
            if task and task.status == "running":
                task.status = "generation_failed"; task.error_code = type(exc).__name__[:64]; db.commit()
            logger.exception("generation task failed id=%s", task_id)

def run_worker(poll_interval: float = 2.0) -> None:
    """持续扫描并领取持久化任务，直到进程收到中断。"""
    while True:
        with SessionLocal() as db:
            due = db.scalars(select(ScheduledTask).where(ScheduledTask.status == "active", ScheduledTask.next_run_at <= datetime.now(timezone.utc)).limit(10)).all()
            for scheduled in due:
                window_end = scheduled.next_run_at
                window_start = window_end - __import__("datetime").timedelta(days=1)
                db.add(GenerationTask(user_id=scheduled.user_id, query=scheduled.query, limit=scheduled.limit, window_start=window_start, window_end=window_end, status="queued"))
                scheduled.last_run_at = scheduled.next_run_at
                if scheduled.frequency == "daily": scheduled.next_run_at = next_run("daily", scheduled.run_at, scheduled.timezone, now=datetime.now(timezone.utc))
                else: scheduled.status = "completed"
            if due: db.commit()
            task_id = db.scalar(select(GenerationTask.id).where(GenerationTask.status == "queued").order_by(GenerationTask.created_at).limit(1))
        if task_id: process_task(task_id)
        else: time.sleep(poll_interval)

def main() -> None:
    """解析 Worker 启动参数并开始轮询。"""
    parser = argparse.ArgumentParser(description="DailyNews generation worker")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args(); logging.basicConfig(level=logging.INFO); run_worker(max(args.poll_interval, 0.2))

if __name__ == "__main__": main()
