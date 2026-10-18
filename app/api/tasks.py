"""每日和一次性任务管理。"""
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.auth import current_user
from app.db.database import get_db
from app.db.models import User
from app.db.news_models import ScheduledTask, GenerationTask
from app.services.scheduler import next_run

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

class TaskRequest(BaseModel):
    """完整任务配置，一次性必须显式指定新闻范围。"""
    name: str | None = Field(default=None, max_length=200)
    frequency: Literal["daily", "once"]
    run_at: datetime
    timezone: str
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=5, ge=1, le=100, description="最多整理出的新闻事件数")
    article_limit: int | None = Field(default=None, ge=1, le=200, description="原始文章采集篇数；省略或设为 null 时跟随事件数的两倍")
    window_start: datetime | None = None
    window_end: datetime | None = None
    overrides: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_times(self):
        """拒绝无效时区、时间窗口及低于事件数的文章采集上限。"""
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError("时区必须为有效 IANA 名称") from exc
        if not self.run_at.tzinfo:
            raise ValueError("执行时间必须带时区")
        if self.frequency == "once" and not (self.window_start and self.window_end):
            raise ValueError("一次性任务需要明确新闻开始和结束时间")
        if bool(self.window_start) != bool(self.window_end):
            raise ValueError("新闻窗口必须同时提供开始和结束")
        if self.window_start and (not self.window_start.tzinfo or not self.window_end.tzinfo or self.window_start >= self.window_end):
            raise ValueError("新闻窗口必须带时区且开始早于结束")
        if self.article_limit is not None and self.article_limit < self.limit:
            raise ValueError("原始文章采集上限不能低于事件数")
        return self


class ArticleLimitRequest(BaseModel):
    """更新定时任务的原文采集上限，null 表示跟随事件数两倍。"""
    article_limit: int | None = Field(ge=1, le=200)

def task_payload(task: ScheduledTask) -> dict:
    """转换配置并提供服务端允许操作。"""
    actions = []
    if task.status in {"active", "paused"}:
        actions = ["edit", "pause" if task.status == "active" else "resume"]
        if not task.is_default:
            actions.append("cancel")
    return {key: getattr(task, key) for key in ("id", "name", "frequency", "status", "timezone", "run_at", "next_run_at", "last_run_at", "query", "limit", "article_limit", "window_start", "window_end", "is_default", "overrides")} | {"allowed_actions": actions}

def owned_task(db: Session, user: User, task_id: str) -> ScheduledTask:
    """锁定所属任务，与领取事务使用相同锁边界。"""
    task = db.scalar(select(ScheduledTask).where(ScheduledTask.id == task_id, ScheduledTask.user_id == user.id).with_for_update())
    if not task:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
    return task

@router.post("")
def create_task(payload: TaskRequest, db: Session = Depends(get_db), user: User = Depends(current_user), idempotency_key: str | None = Header(default=None, max_length=200)):
    """创建任务，过去的一次性执行如实记为错过。"""
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    if idempotency_key:
        prior = db.scalar(select(ScheduledTask).where(ScheduledTask.user_id == user.id, ScheduledTask.idempotency_key == idempotency_key))
        if prior: return task_payload(prior)
    planned = next_run(payload.frequency, payload.run_at, payload.timezone)
    task = ScheduledTask(**payload.model_dump(exclude={"name"}), name=payload.name or f"{payload.query}-{payload.frequency}", user_id=user.id, recipient_email=user.email, next_run_at=planned)
    if payload.frequency == "once" and planned <= datetime.now(timezone.utc):
        task.status = "missed"
    task.idempotency_key = idempotency_key
    db.add(task); db.commit(); db.refresh(task)
    return task_payload(task)

@router.get("")
def list_tasks(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """列出未取消任务。"""
    return [task_payload(row) for row in db.scalars(select(ScheduledTask).where(ScheduledTask.user_id == user.id, ScheduledTask.status != "cancelled").order_by(ScheduledTask.next_run_at)).all()]

@router.get("/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """查看所属任务完整配置。"""
    return task_payload(owned_task(db, user, task_id))

@router.patch("/{task_id}/article-limit")
def update_article_limit(task_id: str, payload: ArticleLimitRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """仅更新所属任务的文章采集上限，默认每日任务按当前偏好事件数校验。"""
    task = owned_task(db, user, task_id)
    if task.status not in {"active", "paused"}:
        raise HTTPException(409, detail={"code": "EDIT_NOT_ALLOWED", "message": "终结任务不可编辑"})
    event_limit = user.style.get("max_events", 5) if task.is_default else task.limit
    if payload.article_limit is not None and payload.article_limit < event_limit:
        raise HTTPException(422, detail={"code": "ARTICLE_LIMIT_TOO_LOW", "message": "原始文章采集上限不能低于事件数"})
    task.article_limit = payload.article_limit
    db.commit()
    return task_payload(task)

@router.patch("/{task_id}")
def edit_task(task_id: str, payload: TaskRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """编辑后续执行；已领取执行使用原快照。"""
    task = owned_task(db, user, task_id)
    if task.is_default or task.status not in {"active", "paused"}:
        raise HTTPException(409, detail={"code": "EDIT_NOT_ALLOWED", "message": "默认任务请在订阅设置修改；终结任务需重新创建"})
    for key, value in payload.model_dump().items():
        setattr(task, key, value if key != "name" or value else f"{payload.query}-{payload.frequency}")
    task.next_run_at = next_run(task.frequency, task.run_at, task.timezone)
    if task.frequency == "once" and task.next_run_at <= datetime.now(timezone.utc):
        task.status = "missed"
    db.commit(); return task_payload(task)

@router.get("/{task_id}/executions")
def executions(task_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """列出历次执行，包含错过和忙碌跳过记录。"""
    owned_task(db, user, task_id)
    rows = db.scalars(select(GenerationTask).where(GenerationTask.scheduled_task_id == task_id, GenerationTask.user_id == user.id).order_by(GenerationTask.created_at.desc()).limit(100)).all()
    return [{"id": r.id, "status": r.status, "error_code": r.error_code, "planned_at": r.planned_at, "briefing_id": r.briefing_id, "created_at": r.created_at} for r in rows]

@router.post("/{task_id}/{action}")
def transition(task_id: str, action: Literal["pause", "resume", "cancel"], db: Session = Depends(get_db), user: User = Depends(current_user)):
    """执行允许的暂停恢复取消，已终结任务不可恢复。"""
    task = owned_task(db, user, task_id)
    if action not in task_payload(task)["allowed_actions"]:
        raise HTTPException(409, detail={"code": "INVALID_STATUS", "message": "当前任务不允许此操作"})
    task.status = {"pause": "paused", "resume": "active", "cancel": "cancelled"}[action]
    if action == "resume":
        task.next_run_at = next_run(task.frequency, task.run_at, task.timezone)
        if task.frequency == "once" and task.next_run_at <= datetime.now(timezone.utc):
            task.status = "missed"
    db.commit(); return task_payload(task)
