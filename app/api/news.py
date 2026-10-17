"""即时新闻简报 API。"""
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.auth import current_user
from app.config import get_settings
from app.db.database import get_db
from app.db.models import User
from app.db.news_models import Briefing, NewsEvent, TaskExecution
from app.services.news import fetch_articles
from app.db.news_models import GenerationTask

router = APIRouter(prefix="/api/v1/news", tags=["news"])
settings = get_settings()

class BriefingRequest(BaseModel):
    """即时简报请求。"""
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)
    start: datetime | None = None
    end: datetime | None = None

@router.post("/briefings")
def create_briefing(payload: BriefingRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """持久化即时生成任务并立即返回任务标识。"""
    if (payload.start is None) != (payload.end is None): raise HTTPException(422, detail={"code":"INVALID_TIME_RANGE","message":"时间范围必须同时提供开始和结束时间"})
    if payload.start and payload.end and payload.start >= payload.end: raise HTTPException(422, detail={"code":"INVALID_TIME_RANGE","message":"开始时间必须早于结束时间"})
    if payload.start is None: raise HTTPException(422, detail={"code":"TIME_RANGE_REQUIRED","message":"即时任务需要明确新闻时间范围"})
    task = GenerationTask(user_id=user.id, query=payload.query, limit=payload.limit, window_start=payload.start, window_end=payload.end, status="queued")
    db.add(task); db.commit(); db.refresh(task)
    return {"id": task.id, "status": task.status, "allowed_actions": ["cancel"]}

@router.get("/tasks/{task_id}")
def task_status(task_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """返回当前用户任务的持久化状态。"""
    task = db.query(GenerationTask).filter(GenerationTask.id == task_id, GenerationTask.user_id == user.id).first()
    if not task: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"任务不存在"})
    return {"id":task.id,"status":task.status,"error_code":task.error_code,"briefing_id":task.briefing_id,"allowed_actions":["cancel"] if task.status == "queued" else []}

@router.get("/tasks/{task_id}/executions")
def task_executions(task_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """返回当前用户任务的执行阶段和结果记录。"""
    task = db.query(GenerationTask).filter(GenerationTask.id == task_id, GenerationTask.user_id == user.id).first()
    if not task: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"任务不存在"})
    rows = db.query(TaskExecution).filter(TaskExecution.generation_task_id == task.id, TaskExecution.user_id == user.id).order_by(TaskExecution.started_at.desc()).all()
    return [{"id":r.id,"status":r.status,"phase":r.phase,"error_code":r.error_code,"event_count":r.event_count,"started_at":r.started_at,"finished_at":r.finished_at} for r in rows]

@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """仅在简报提交前取消排队或执行中的即时任务。"""
    task = db.query(GenerationTask).filter(GenerationTask.id == task_id, GenerationTask.user_id == user.id).with_for_update().first()
    if not task: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"任务不存在"})
    if task.status == "queued": task.status = "cancelled"
    elif task.status == "running": task.cancel_requested = True
    else: raise HTTPException(409, detail={"code":"CANNOT_CANCEL","message":"任务已提交或完成，不能取消"})
    db.commit(); return {"id":task.id,"status":task.status,"cancel_requested":task.cancel_requested,"allowed_actions":[]}

@router.get("/briefings")
def list_briefings(page: int = Query(default=1, ge=1), db: Session = Depends(get_db), user: User = Depends(current_user)):
    """以每页 20 条返回当前用户简报。"""
    query = db.query(Briefing).filter(Briefing.user_id == user.id)
    total = query.count(); rows = query.order_by(Briefing.created_at.desc()).offset((page - 1) * 20).limit(20).all()
    return {"page":page,"page_size":20,"total":total,"total_pages":(total + 19) // 20,"items":[{"id": row.id, "title": row.title, "status": row.status, "event_count": row.event_count, "created_at": row.created_at} for row in rows]}

@router.get("/briefings/{briefing_id}")
def get_briefing(briefing_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """返回简报元数据和事件资料，并校验用户归属。"""
    briefing = db.query(Briefing).filter(Briefing.id == briefing_id, Briefing.user_id == user.id).first()
    if not briefing: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"简报不存在"})
    events = db.query(NewsEvent).filter(NewsEvent.briefing_id == briefing.id, NewsEvent.user_id == user.id).all()
    return {"id":briefing.id,"title":briefing.title,"query":briefing.query,"event_count":briefing.event_count,"created_at":briefing.created_at,"events":[{"title":event.title,"source":event.source,"url":event.url,"published_at":event.published_at,"summary":event.summary,"facts":event.facts} for event in events]}

@router.get("/briefings/{briefing_id}/html")
def view_briefing_html(briefing_id: str, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """以内联方式展示已授权简报 HTML，并通过 CSP 隔离内容。"""
    briefing = db.query(Briefing).filter(Briefing.id == briefing_id, Briefing.user_id == user.id).first()
    if not briefing: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"简报不存在"})
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return Path(briefing.html_path).read_text(encoding="utf-8")

@router.get("/briefings/{briefing_id}/download")
def download_briefing(briefing_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """下载当前用户已保存的原始简报 HTML。"""
    briefing = db.query(Briefing).filter(Briefing.id == briefing_id, Briefing.user_id == user.id).first()
    if not briefing: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"简报不存在"})
    return Response(Path(briefing.html_path).read_bytes(), media_type="text/html; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{briefing.id}.html"', "X-Content-Type-Options":"nosniff"})
