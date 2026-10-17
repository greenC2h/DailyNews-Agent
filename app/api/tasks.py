"""每日和一次性定时任务 API。"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.auth import current_user
from app.db.database import get_db
from app.db.models import User
from app.db.news_models import ScheduledTask
from app.services.scheduler import next_run
router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])
class TaskRequest(BaseModel):
    """创建定时任务请求。"""
    name: str | None = Field(default=None, max_length=200); frequency: str; run_at: datetime; timezone: str; query: str = Field(min_length=2, max_length=500); limit: int = Field(default=5, ge=1, le=10)
@router.post("")
def create_task(payload: TaskRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """创建每日或一次性任务并固定账号邮箱为收件人。"""
    if payload.frequency not in {"daily", "once"}: raise HTTPException(422, detail={"code":"INVALID_FREQUENCY","message":"仅支持每日和一次性任务"})
    planned = next_run(payload.frequency, payload.run_at, payload.timezone)
    if payload.frequency == "once" and planned <= datetime.now(timezone.utc): raise HTTPException(422, detail={"code":"MISSED_RUN","message":"一次性任务时间必须在未来"})
    task = ScheduledTask(user_id=user.id, name=payload.name or f"{payload.query}-{payload.frequency}", frequency=payload.frequency, timezone=payload.timezone, run_at=payload.run_at, query=payload.query, limit=payload.limit, recipient_email=user.email, next_run_at=planned)
    db.add(task); db.commit(); db.refresh(task); return {"id":task.id,"name":task.name,"frequency":task.frequency,"status":task.status,"next_run_at":task.next_run_at}
@router.get("")
def list_tasks(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """列出当前用户未取消的定时任务。"""
    rows = db.query(ScheduledTask).filter(ScheduledTask.user_id == user.id, ScheduledTask.status != "cancelled").order_by(ScheduledTask.next_run_at).all()
    return [{"id":r.id,"name":r.name,"frequency":r.frequency,"status":r.status,"next_run_at":r.next_run_at,"query":r.query} for r in rows]
@router.post("/{task_id}/pause")
def pause_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """暂停任务并保留其配置和历史。"""
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id, ScheduledTask.user_id == user.id).first()
    if not task: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"任务不存在"})
    task.status = "paused"; db.commit(); return {"id":task.id,"status":task.status}
@router.post("/{task_id}/resume")
def resume_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """恢复暂停任务并重新计算下一次时间。"""
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id, ScheduledTask.user_id == user.id).first()
    if not task: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"任务不存在"})
    task.status = "active"; task.next_run_at = next_run(task.frequency, task.run_at, task.timezone); db.commit(); return {"id":task.id,"status":task.status,"next_run_at":task.next_run_at}
@router.post("/{task_id}/cancel")
def cancel_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """取消任务但保留数据库记录。"""
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id, ScheduledTask.user_id == user.id).first()
    if not task: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"任务不存在"})
    task.status = "cancelled"; db.commit(); return {"id":task.id,"status":task.status}
