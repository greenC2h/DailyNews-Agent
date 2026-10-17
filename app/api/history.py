"""历史事件关键词检索 API。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.auth import current_user
from app.db.database import get_db
from app.db.models import User
from app.services.rag import search_events, milvus_status
router = APIRouter(prefix="/api/v1/history", tags=["history"])
@router.get("")
def history(query: str = Query(min_length=2, max_length=200), limit: int = Query(default=20, ge=1, le=20), db: Session = Depends(get_db), user: User = Depends(current_user)):
    """返回当前用户可访问的历史事件，Milvus 不可用时使用关键词回退。"""
    rows = search_events(db, user.id, query, limit); return {"query":query,"mode":"keyword_fallback","milvus":milvus_status(),"items":[{"id":r.id,"title":r.title,"summary":r.summary,"source":r.source,"url":r.url,"published_at":r.published_at} for r in rows]}
