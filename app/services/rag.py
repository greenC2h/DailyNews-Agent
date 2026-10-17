"""历史事件关键词检索与 Milvus 可选索引适配。"""
from app.config import get_settings
from app.db.news_models import NewsEvent

def search_events(db, user_id: str, query: str, limit: int = 20) -> list[NewsEvent]:
    """优先使用 PostgreSQL 关键词回退检索，始终按用户归属过滤。"""
    pattern = f"%{query}%"
    return db.query(NewsEvent).filter(NewsEvent.user_id == user_id, (NewsEvent.title.ilike(pattern) | NewsEvent.summary.ilike(pattern))).order_by(NewsEvent.published_at.desc()).limit(limit).all()

def milvus_status() -> dict:
    """检查 Milvus 配置状态，不在 Web 启动时隐式建 collection。"""
    settings = get_settings()
    if not settings.milvus_uri: return {"available": False, "reason": "MILVUS_URI_MISSING"}
    try:
        from pymilvus import MilvusClient
        client = MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token)
        return {"available": True, "collection": settings.milvus_collection, "exists": client.has_collection(settings.milvus_collection)}
    except Exception as exc:
        return {"available": False, "reason": type(exc).__name__}
