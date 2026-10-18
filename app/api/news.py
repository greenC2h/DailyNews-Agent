"""即时新闻简报 API。"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Response, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.auth import current_user
from app.config import get_settings
from app.db.database import get_db
from app.db.models import User
from app.db.news_models import Briefing, NewsEvent, TaskExecution
from app.services.news import fetch_articles
from app.db.news_models import GenerationTask
from app.services.tasking import publish_immediate
from app.db.news_models import MailDelivery
from app.services.html_storage import HtmlStorageError, read_stored_html
from app.services.briefing_metadata import extract_briefing_title

router = APIRouter(prefix="/api/v1/news", tags=["news"])
settings = get_settings()


def briefing_display_title(briefing: Briefing) -> str:
    """从保存的 HTML 读取 AI 标题，文件不可用时保留数据库标题。"""
    try:
        html = read_stored_html(briefing, settings.html_root).decode("utf-8", errors="replace")
    except HtmlStorageError:
        return briefing.title
    return extract_briefing_title(html, briefing.title)

class BriefingRequest(BaseModel):
    """即时简报请求。"""
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=5, ge=1, le=100, description="最多整理出的新闻事件数；原始文章采集上限固定为此值的两倍")
    send_email: bool = False
    overrides: dict = Field(default_factory=dict)
    start: datetime | None = None
    end: datetime | None = None

@router.post("/briefings")
def create_briefing(payload: BriefingRequest, db: Session = Depends(get_db), user: User = Depends(current_user), idempotency_key: str | None = Header(default=None, max_length=200)):
    """持久化即时生成任务并立即返回任务标识。"""
    if (payload.start is None) != (payload.end is None): raise HTTPException(422, detail={"code":"INVALID_TIME_RANGE","message":"时间范围必须同时提供开始和结束时间"})
    if payload.start and payload.end and payload.start >= payload.end: raise HTTPException(422, detail={"code":"INVALID_TIME_RANGE","message":"开始时间必须早于结束时间"})
    if payload.start is None: raise HTTPException(422, detail={"code":"TIME_RANGE_REQUIRED","message":"即时任务需要明确新闻时间范围"})
    task = publish_immediate(db, user, payload.query, payload.limit, payload.start, payload.end, idempotency_key, payload.send_email, payload.overrides)
    return {"id": task.id, "status": task.status, "allowed_actions": ["cancel"]}

@router.get("/tasks/{task_id}")
def task_status(task_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """返回当前用户任务的持久化状态。"""
    task = db.query(GenerationTask).filter(GenerationTask.id == task_id, GenerationTask.user_id == user.id).first()
    if not task: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"任务不存在"})
    actions = ["cancel"] if task.status in {"queued", "running"} and not task.cancel_requested and not task.scheduled_task_id else []
    if task.status in {"generation_failed", "system_failed"}: actions.append("retry")
    return {"id":task.id,"status":task.status,"error_code":task.error_code,"briefing_id":task.briefing_id,"retry_count":task.retry_count,"allowed_actions":actions}


@router.post("/tasks/{task_id}/retry")
def retry_generation(task_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """重试原任务及原窗口，七天内复用事件资料，不创建重复简报。"""
    db.query(User).filter(User.id == user.id).with_for_update().first()
    task = db.query(GenerationTask).filter(GenerationTask.id == task_id, GenerationTask.user_id == user.id).with_for_update().first()
    if not task: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"任务不存在"})
    if task.status not in {"generation_failed", "system_failed"}: raise HTTPException(409, detail={"code":"RETRY_NOT_ALLOWED","message":"当前任务不可重试"})
    busy = db.query(GenerationTask).filter(GenerationTask.user_id == user.id, GenerationTask.status.in_(["queued", "running"])).first()
    if busy: raise HTTPException(409, detail={"code":"USER_BUSY","message":"已有任务执行中"})
    task.status = "queued"; task.error_code = None; task.cancel_requested = False; task.retry_count += 1
    task.lease_owner = None; task.lease_expires_at = None
    db.commit(); return {"id":task.id,"status":task.status,"allowed_actions":["cancel"] if not task.scheduled_task_id else []}

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
    if task.scheduled_task_id: raise HTTPException(409, detail={"code":"CANNOT_CANCEL","message":"定时任务的已开始执行不受取消影响"})
    if task.status == "queued": task.status = "cancelled"; task.intermediate = None; task.intermediate_expires_at = None
    elif task.status == "running": task.cancel_requested = True
    else: raise HTTPException(409, detail={"code":"CANNOT_CANCEL","message":"任务已提交或完成，不能取消"})
    db.commit(); return {"id":task.id,"status":task.status,"cancel_requested":task.cancel_requested,"allowed_actions":[]}

@router.get("/briefings")
def list_briefings(page: int = Query(default=1, ge=1), db: Session = Depends(get_db), user: User = Depends(current_user)):
    """以每页 20 条返回当前用户简报及 AI 生成的标题。"""
    query = db.query(Briefing).filter(Briefing.user_id == user.id)
    total = query.count(); rows = query.order_by(Briefing.created_at.desc()).offset((page - 1) * 20).limit(20).all()
    items = []
    for row in rows:
        title = briefing_display_title(row)
        items.append({"id": row.id, "title": title, "status": row.status, "event_count": row.event_count, "created_at": row.created_at})
    return {"page":page,"page_size":20,"total":total,"total_pages":(total + 19) // 20,"items":items}

@router.get("/briefings/{briefing_id}")
def get_briefing(briefing_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """返回简报元数据和事件资料，并校验用户归属。"""
    briefing = db.query(Briefing).filter(Briefing.id == briefing_id, Briefing.user_id == user.id).first()
    if not briefing: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"简报不存在"})
    title = briefing_display_title(briefing)
    events = db.query(NewsEvent).filter(NewsEvent.briefing_id == briefing.id, NewsEvent.user_id == user.id).all()
    briefing.read_at = datetime.now(timezone.utc); db.commit()
    valid_event_ids = [event_id for event_id in (briefing.event_ids or []) if event_id]
    if valid_event_ids:
        linked_events = db.query(NewsEvent).filter(NewsEvent.id.in_(valid_event_ids), NewsEvent.user_id == user.id).all()
        if linked_events:
            events = linked_events
    deliveries = db.query(MailDelivery).filter(MailDelivery.briefing_id == briefing.id, MailDelivery.user_id == user.id).all()
    actions = ["send"] if not any(item.status in {"sending", "success", "pending_confirmation"} for item in deliveries) else []
    return {"id":briefing.id,"title":title,"query":briefing.query,"event_count":briefing.event_count,"created_at":briefing.created_at,"allowed_actions":actions,"events":[{"id":event.id,"title":event.title,"source":event.source,"url":event.url,"published_at":event.published_at,"summary":event.summary,"facts":event.facts} for event in events]}

@router.get("/briefings/{briefing_id}/html")
def view_briefing_html(briefing_id: str, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """以内联方式展示已授权简报 HTML，并通过 CSP 隔离内容。"""
    briefing = db.query(Briefing).filter(Briefing.id == briefing_id, Briefing.user_id == user.id).first()
    if not briefing: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"简报不存在"})
    try:
        content = read_stored_html(briefing, settings.html_root)
    except HtmlStorageError as exc:
        raise HTTPException(404, detail={"code":"HTML_UNAVAILABLE","message":"该历史查询没有 HTML 简报"}) from exc
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox allow-popups allow-popups-to-escape-sandbox; style-src 'unsafe-inline'; img-src https: data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return Response(content, media_type="text/html; charset=utf-8", headers=dict(response.headers))

@router.get("/briefings/{briefing_id}/download")
def download_briefing(briefing_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """下载当前用户已保存的原始简报 HTML。"""
    briefing = db.query(Briefing).filter(Briefing.id == briefing_id, Briefing.user_id == user.id).first()
    if not briefing: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"简报不存在"})
    try:
        content = read_stored_html(briefing, settings.html_root)
    except HtmlStorageError as exc:
        raise HTTPException(404, detail={"code":"HTML_UNAVAILABLE","message":"该历史查询没有 HTML 简报"}) from exc
    return Response(content, media_type="text/html; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{briefing.id}.html"', "X-Content-Type-Options":"nosniff"})
