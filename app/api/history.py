"""历史事件关键词检索 API。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.auth import current_user
from app.db.database import get_db
from app.db.models import User
from app.services.rag import search_events, milvus_status, vector_event_ids
router = APIRouter(prefix="/api/v1/history", tags=["history"])
@router.get("")
def history(query: str = Query(min_length=2, max_length=200), limit: int = Query(default=20, ge=1, le=20), db: Session = Depends(get_db), user: User = Depends(current_user)):
    """返回当前用户可访问的历史事件，Milvus 不可用时使用关键词回退。"""
    status = milvus_status(); ids = vector_event_ids(user.id, query, limit) if status.get("exists") else []
    rows = search_events(db, user.id, query, limit)
    by_id = {row.id: row for row in rows}; ordered = [by_id[event_id] for event_id in ids if event_id in by_id] + [row for row in rows if row.id not in ids]
    return {"query":query,"mode":"hybrid" if ids else "keyword_fallback","milvus":status,"items":[{"id":r.id,"title":r.title,"summary":r.summary,"source":r.source,"url":r.url,"published_at":r.published_at} for r in ordered[:limit]]}
