"""历史事件关键词检索与 Milvus 可选索引适配。"""
from app.config import get_settings
from app.db.news_models import NewsEvent

def _client():
    """创建 Milvus 客户端。"""
    from pymilvus import MilvusClient
    settings = get_settings()
    return MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token)

def init_collection() -> dict:
    """显式创建新闻事件 collection，重复执行保持幂等。"""
    settings = get_settings(); client = _client()
    if client.has_collection(settings.milvus_collection): return {"created": False, "collection": settings.milvus_collection}
    from pymilvus import DataType
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", datatype=DataType.VARCHAR, is_primary=True, max_length=36)
    schema.add_field("vector", datatype=DataType.FLOAT_VECTOR, dim=settings.embedding_dimension)
    schema.add_field("user_id", datatype=DataType.VARCHAR, max_length=36); schema.add_field("title", datatype=DataType.VARCHAR, max_length=500); schema.add_field("summary", datatype=DataType.VARCHAR, max_length=65535)
    index = client.prepare_index_params(); index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(collection_name=settings.milvus_collection, schema=schema, index_params=index)
    return {"created": True, "collection": settings.milvus_collection}

def index_events(events: list[NewsEvent]) -> int:
    """调用配置的 Embedding 服务并 upsert 事件向量。"""
    settings = get_settings()
    if not events: return 0
    if not settings.embedding_api_key or not settings.embedding_base_url or not settings.embedding_model: raise RuntimeError("Embedding 配置缺失")
    from langchain_openai import OpenAIEmbeddings
    embedder = OpenAIEmbeddings(model=settings.embedding_model, dimensions=settings.embedding_dimension, api_key=settings.embedding_api_key, base_url=settings.embedding_base_url)
    vectors = embedder.embed_documents([f"{e.title}\n{e.summary}" for e in events]); client = _client()
    client.upsert(settings.milvus_collection, [{"id":e.id,"vector":v,"user_id":e.user_id,"title":e.title,"summary":e.summary} for e,v in zip(events,vectors)])
    return len(events)

def search_events(db, user_id: str, query: str, limit: int = 20) -> list[NewsEvent]:
    """优先使用 PostgreSQL 关键词回退检索，始终按用户归属过滤。"""
    pattern = f"%{query}%"
    return db.query(NewsEvent).filter(NewsEvent.user_id == user_id, (NewsEvent.title.ilike(pattern) | NewsEvent.summary.ilike(pattern))).order_by(NewsEvent.published_at.desc()).limit(limit).all()

def vector_event_ids(user_id: str, query: str, limit: int = 20) -> list[str]:
    """使用 Embedding 和 Milvus 召回当前用户事件 ID，失败时返回空列表。"""
    settings = get_settings()
    try:
        from langchain_openai import OpenAIEmbeddings
        embedder = OpenAIEmbeddings(model=settings.embedding_model, dimensions=settings.embedding_dimension, api_key=settings.embedding_api_key, base_url=settings.embedding_base_url)
        vector = embedder.embed_query(query); result = _client().search(settings.milvus_collection, data=[vector], filter=f'user_id == "{user_id}"', limit=limit, output_fields=["id"])
        return [str(item["id"]) for item in result[0]]
    except Exception:
        return []

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
