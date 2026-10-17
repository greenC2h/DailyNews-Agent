"""以业务数据库为权限边界的事件索引与混合检索。"""
from datetime import datetime, timezone
import json
import logging
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.news_models import Briefing, NewsEvent

logger = logging.getLogger(__name__)


class VectorMatches(list):
    """保留向量召回分数及依赖可用状态，兼容原有 ID 列表接口。"""

    def __init__(self, ids=(), *, scores=None, available=True):
        """初始化召回 ID、归一化分数和本次调用可用性。"""
        super().__init__(ids)
        self.scores = scores or {}
        self.available = available


class HistoryMatches(list):
    """承载已授权结果、统一相关性分数和内部召回方式。"""

    def __init__(self, rows=(), *, scores=None, mode="keyword_fallback"):
        """初始化检索结果及供审计使用的召回信息。"""
        super().__init__(rows)
        self.scores = scores or {}
        self.mode = mode


def normalize_text(text: str) -> str:
    """统一大小写、空白及常见标点，不分词或改写检索意图。"""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(" " if unicodedata.category(char).startswith("P") else char for char in text)
    return re.sub(r"\s+", " ", text).strip()


def build_search_text(event: NewsEvent) -> str:
    """从标题、摘要和关键事实生成检索文本，避免复制来源正文。"""
    facts = event.facts if isinstance(event.facts, dict) else {}
    values = [event.title, event.summary]
    values.extend(str(item) for item in facts.get("facts", []))
    return normalize_text(" ".join(values))


def _client():
    """创建具有有限连接等待时间的 Milvus 客户端。"""
    from pymilvus import MilvusClient
    settings = get_settings()
    return MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token, timeout=10)


def _embedder():
    """创建独立 Embedding 客户端，向兼容服务发送字符串而非 token 数组。"""
    from langchain_openai import OpenAIEmbeddings
    settings = get_settings()
    return OpenAIEmbeddings(
        model=settings.embedding_model, dimensions=settings.embedding_dimension,
        api_key=settings.embedding_api_key, base_url=settings.embedding_base_url,
        check_embedding_ctx_length=False, max_retries=1, request_timeout=25,
    )


def init_collection() -> dict:
    """显式幂等创建向量集合，绝不修改或删除已有集合。"""
    from pymilvus import DataType
    settings = get_settings()
    client = _client()
    try:
        if client.has_collection(settings.milvus_collection):
            return {"created": False, "collection": settings.milvus_collection}
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", datatype=DataType.VARCHAR, is_primary=True, max_length=36)
        schema.add_field("vector", datatype=DataType.FLOAT_VECTOR, dim=settings.embedding_dimension)
        schema.add_field("user_id", datatype=DataType.VARCHAR, max_length=36)
        schema.add_field("title", datatype=DataType.VARCHAR, max_length=500)
        schema.add_field("summary", datatype=DataType.VARCHAR, max_length=65535)
        index = client.prepare_index_params()
        index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        client.create_collection(collection_name=settings.milvus_collection, schema=schema, index_params=index)
        return {"created": True, "collection": settings.milvus_collection}
    finally:
        client.close()


def index_events(events: list[NewsEvent], db: Session | None = None) -> int:
    """分批幂等索引，失败只标记该批待重试；传入会话时提交状态。"""
    settings = get_settings()
    batch_size = getattr(settings, "rag_batch_size", 8)
    indexed = 0
    for offset in range(0, len(events), batch_size):
        batch = events[offset:offset + batch_size]
        client = None
        for event in batch:
            event.search_text = build_search_text(event)
        try:
            vectors = _embedder().embed_documents([event.search_text for event in batch])
            if len(vectors) != len(batch) or any(len(vector) != settings.embedding_dimension for vector in vectors):
                raise ValueError("Embedding 返回数量或维度不符")
            client = _client()
            result = client.upsert(settings.milvus_collection, [
                {"id": event.id, "vector": vector, "user_id": event.user_id,
                 "title": event.title, "summary": event.summary}
                for event, vector in zip(batch, vectors)
            ], timeout=15)
            if result.get("upsert_count") != len(batch):
                raise ValueError("Milvus 未确认完整批次写入")
        except Exception as exc:
            logger.warning("事件索引失败: %s", type(exc).__name__)
            for event in batch:
                event.index_status = "pending"
                event.index_error = type(exc).__name__[:100]
                event.index_retry_count = (event.index_retry_count or 0) + 1
        else:
            for event in batch:
                event.index_status = "indexed"
                event.index_error = None
            indexed += len(batch)
        finally:
            if client is not None:
                client.close()
        if db is not None:
            db.commit()
    return indexed


def index_pending_events(db: Session, limit: int = 20) -> int:
    """扫描有限数量的待索引事件，按失败次数上限重试并返回成功数。"""
    retry_limit = getattr(get_settings(), "rag_index_retry_limit", 3)
    rows = db.scalars(select(NewsEvent).join(Briefing).where(
        NewsEvent.index_status == "pending", NewsEvent.index_retry_count < retry_limit,
        Briefing.status == "succeeded",
    ).order_by(NewsEvent.index_retry_count, Briefing.created_at).limit(limit)).all()
    return index_events(rows, db=db)


def _scope(user_id: str, start=None, end=None):
    """构建用户、成功简报及保存时间的数据库过滤条件。"""
    conditions = [NewsEvent.user_id == user_id, Briefing.user_id == user_id, Briefing.status == "succeeded"]
    if start is not None:
        conditions.append(Briefing.created_at >= start)
    if end is not None:
        conditions.append(Briefing.created_at < end)
    return conditions


def _publication_match(event: NewsEvent, start, end) -> bool:
    """至少一条原文来源满足发布时间窗口时保留事件。"""
    dates = [event.published_at]
    for source in (event.facts or {}).get("sources", []):
        try:
            dates.append(datetime.fromisoformat(source["published_at"].replace("Z", "+00:00")))
        except (KeyError, TypeError, ValueError):
            continue
    for date in dates:
        aware = date if date.tzinfo else date.replace(tzinfo=timezone.utc)
        if (start is None or aware >= start) and (end is None or aware < end):
            return True
    return False


def search_events(db: Session, user_id: str, query: str, limit: int = 20,
                  start: datetime | None = None, end: datetime | None = None,
                  published_start=None, published_end=None, *, vector_lookup=None,
                  include_terms=(), exclude_terms=()) -> HistoryMatches:
    """先过滤权限和两类时间，再按统一分数合并关键词与向量召回。"""
    # 先得到合格 ID，再交给 Milvus，避免全局 top-k 后过滤漏掉范围内事件。
    statement = select(NewsEvent).join(Briefing).where(*_scope(user_id, start, end))
    scoped = db.scalars(statement).all()
    if published_start is not None or published_end is not None:
        scoped = [event for event in scoped if _publication_match(event, published_start, published_end)]
    scoped = [event for event in scoped if
              all(normalize_text(term) in build_search_text(event) for term in include_terms) and
              not any(normalize_text(term) in build_search_text(event) for term in exclude_terms)]
    eligible = {event.id: event for event in scoped}
    if not eligible:
        return HistoryMatches()
    normalized = normalize_text(query)
    if not normalized:
        return HistoryMatches()
    escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    keyword_rows = db.scalars(statement.where(
        NewsEvent.search_text.ilike(f"%{escaped}%", escape="\\") |
        NewsEvent.title.ilike(f"%{escaped}%", escape="\\") |
        NewsEvent.summary.ilike(f"%{escaped}%", escape="\\")
    )).all()
    # 旧资料可能尚未回填规范化文本，用同一规则补查；不改变用户查询。
    keywords = {event.id for event in keyword_rows if event.id in eligible}
    keywords.update(event.id for event in scoped if not event.search_text and normalized in build_search_text(event))
    lookup = vector_lookup or vector_event_ids
    vectors = lookup(user_id, query, 20, list(eligible))
    available = getattr(vectors, "available", True)
    vector_scores = getattr(vectors, "scores", {identifier: 1.0 for identifier in vectors})
    scores = {}
    for identifier in keywords | set(vectors):
        if identifier not in eligible:
            continue
        keyword_score = 1.0 if identifier in keywords else 0.0
        score = (keyword_score + vector_scores.get(identifier, 0.0)) / 2 if available else keyword_score
        if score >= getattr(get_settings(), "rag_similarity_threshold", 0.25):
            scores[identifier] = score
    ordered = sorted(scores, key=lambda identifier: (-scores[identifier], identifier))[:limit]
    return HistoryMatches([eligible[identifier] for identifier in ordered], scores=scores,
                          mode="hybrid" if available else "keyword_fallback")


def vector_event_ids(user_id: str, query: str, limit: int = 20,
                     eligible_ids: list[str] | None = None) -> VectorMatches:
    """按用户和合格 ID 分批向量检索；依赖失败保留明确的降级状态。"""
    if eligible_ids == []:
        return VectorMatches()
    settings = get_settings()
    client = None
    try:
        vector = _embedder().embed_query(query)
        client = _client()
        batch_size = getattr(settings, "rag_filter_batch_size", 200)
        batches = [None] if eligible_ids is None else [eligible_ids[i:i + batch_size] for i in range(0, len(eligible_ids), batch_size)]
        scores = {}
        for batch in batches:
            expression = f"user_id == {json.dumps(user_id)}"
            if batch is not None:
                expression += f" and id in {json.dumps(batch)}"
            result = client.search(settings.milvus_collection, data=[vector], filter=expression,
                                   limit=limit, output_fields=["id"], timeout=15)
            for hit in result[0] if result else []:
                identifier = str(hit["id"])
                # COSINE 的相关性越高越好；负相关归零，不把零相似度抬到 0.5。
                scores[identifier] = max(scores.get(identifier, 0.0), min(1.0, max(0.0, float(hit["distance"]))))
        ids = sorted(scores, key=scores.get, reverse=True)[:limit]
        return VectorMatches(ids, scores=scores)
    except Exception as exc:
        logger.warning("历史向量检索降级: %s", type(exc).__name__)
        return VectorMatches(available=False)
    finally:
        if client is not None:
            client.close()


def milvus_status() -> dict:
    """检查向量集合状态，不输出连接配置或凭据。"""
    client = None
    try:
        client = _client()
        return {"available": True, "collection": get_settings().milvus_collection,
                "exists": client.has_collection(get_settings().milvus_collection)}
    except Exception as exc:
        return {"available": False, "reason": type(exc).__name__}
    finally:
        if client is not None:
            client.close()
