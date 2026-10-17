"""任务发布与快照，统一 API、对话及调度的业务边界。"""
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.models import User
from app.db.news_models import GenerationTask

def user_snapshot(user: User, overrides: dict | None = None) -> dict:
    """复制执行时偏好，后续用户修改不改变当前执行。"""
    values = {"language": user.language, "timezone": user.timezone, "style": dict(user.style), "topics": list(user.topics), "keywords": list(user.keywords), "recipient_email": user.email}
    values.update(overrides or {})
    return values

def publish_immediate(db: Session, user: User, query: str, limit: int, start: datetime, end: datetime, key: str | None = None, send_email: bool = False, overrides: dict | None = None) -> GenerationTask:
    """串行检查幂等键及用户立即任务排他，然后持久化快照。"""
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    if not start.tzinfo or not end.tzinfo or start >= end:
        raise HTTPException(422, detail={"code": "INVALID_TIME_RANGE", "message": "时间范围必须带时区且开始早于结束"})
    if key:
        previous = db.scalar(select(GenerationTask).where(GenerationTask.user_id == user.id, GenerationTask.idempotency_key == key))
        if previous:
            return previous
    busy = db.scalar(select(GenerationTask.id).where(GenerationTask.user_id == user.id, GenerationTask.scheduled_task_id.is_(None), GenerationTask.status.in_(["queued", "running"])))
    if busy:
        raise HTTPException(409, detail={"code": "USER_BUSY", "message": "已有立即任务执行中", "task_id": busy})
    task = GenerationTask(user_id=user.id, query=query, limit=limit, window_start=start, window_end=end, snapshot=user_snapshot(user, overrides), idempotency_key=key, send_email=send_email)
    db.add(task); db.commit(); db.refresh(task)
    return task
