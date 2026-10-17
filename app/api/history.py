"""按时间和权限检索历史事件并可生成关联专题简报。"""
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.auth import current_user
from app.config import get_settings
from app.db.database import get_db
from app.db.models import User
from app.db.news_models import NewsEvent, Briefing
from app.services.rag import search_events, milvus_status, vector_event_ids
from app.services.generation import EventBatch, generate_html

router = APIRouter(prefix="/api/v1/history", tags=["history"])
class HistoryBriefingRequest(BaseModel):
    """历史专题查询范围，不触发联网采集。"""
    query: str = Field(min_length=2, max_length=200)
    start: datetime | None = None
    end: datetime | None = None
    limit: int = Field(default=10, ge=1, le=20)

@router.get("")
def history(query: str = Query(min_length=2, max_length=200), limit: int = Query(default=20, ge=1, le=20), start: datetime | None = None, end: datetime | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """返回当前用户指定时间范围内的混合历史召回。"""
    if start and (not start.tzinfo or end and (not end.tzinfo or start >= end)):
        raise HTTPException(422, detail={"code":"INVALID_TIME_RANGE","message":"历史范围必须带时区且开始早于结束"})
    status = milvus_status(); ids = vector_event_ids(user.id, query, limit) if status.get("exists") else []
    rows = search_events(db, user.id, query, limit, start, end)
    vector_rows = db.scalars(select(NewsEvent).where(NewsEvent.user_id == user.id, NewsEvent.id.in_(ids), *([NewsEvent.published_at >= start] if start else []), *([NewsEvent.published_at < end] if end else []))).all() if ids else []
    by_id = {row.id: row for row in vector_rows}
    ordered = [by_id[event_id] for event_id in ids if event_id in by_id] + [row for row in rows if row.id not in by_id]
    return {"query":query,"mode":"hybrid" if ids else "keyword_fallback","milvus":status,"items":[{"id":r.id,"title":r.title,"summary":r.summary,"source":r.source,"url":r.url,"published_at":r.published_at} for r in ordered[:limit]]}

@router.post("/briefings")
def create_history_briefing(payload: HistoryBriefingRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """仅使用当前用户已存事件生成专题 HTML 并保留原事件关联。"""
    rows = search_events(db, user.id, payload.query, payload.limit, payload.start, payload.end)
    if not rows:
        record = Briefing(user_id=user.id, title=f"历史专题：{payload.query}", query=payload.query, status="no_events", html_path="", event_count=0, history_query=payload.model_dump(mode="json"))
        db.add(record); db.commit(); return {"status":"no_events","id":record.id,"query":payload.query,"items":[]}
    grouped = {}
    for row in rows:
        grouped.setdefault(row.briefing_id, []).append(row)
    events = []
    for group in grouped.values():
        for row in group:
            data = dict(row.facts or {})
            data.setdefault("title", row.title); data.setdefault("summary", row.summary)
            if "sources" not in data:
                data["sources"] = [{"source":row.source,"url":row.url,"published_at":row.published_at.isoformat(),"content_excerpt":row.summary}]
            events.append(data)
    try:
        html = generate_html(events, payload.query, user.language, user.style, time_window={"start":payload.start.isoformat() if payload.start else None,"end":payload.end.isoformat() if payload.end else None})
    except Exception as exc:
        record = Briefing(user_id=user.id, title=f"历史专题：{payload.query}", query=payload.query, status="generation_failed", html_path="", event_count=0, event_ids=[row.id for row in rows], history_query=payload.model_dump(mode="json") | {"error_code": type(exc).__name__})
        db.add(record); db.commit()
        raise HTTPException(503, detail={"code":"HISTORY_BRIEFING_FAILED","message":"历史专题生成失败，可稍后重试","briefing_id":record.id}) from exc
    root = get_settings().html_root / user.id; root.mkdir(parents=True, exist_ok=True)
    identifier = __import__('uuid').uuid4().hex
    path = root / f"{identifier}.html"; temp = path.with_suffix('.tmp'); temp.write_text(html, encoding='utf-8'); temp.replace(path)
    briefing = Briefing(user_id=user.id, title=f"历史专题：{payload.query}", query=payload.query, status="succeeded", html_path=str(path.resolve()), event_count=len(rows), event_ids=[row.id for row in rows], history_query=payload.model_dump(mode="json"))
    db.add(briefing); db.commit()
    return {"status":"succeeded","id":briefing.id,"title":briefing.title,"event_count":briefing.event_count,"event_ids":briefing.event_ids}
