"""按时间和权限检索历史事件，并持久化只引用原事件的专题简报。"""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.api.auth import current_user
from app.config import get_settings
from app.db.database import get_db
from app.db.models import User
from app.db.news_models import Briefing
from app.services.generation import EventRecord, EventSource, generate_html
from app.services.rag import HistoryMatches, VectorMatches, milvus_status, search_events, vector_event_ids

router = APIRouter(prefix="/api/v1/history", tags=["history"])


class HistoryBriefingRequest(BaseModel):
    """声明历史专题的关键词、保存时间和原文发布时间条件。"""
    query: str = Field(min_length=2, max_length=200)
    start: datetime | None = None
    end: datetime | None = None
    published_start: datetime | None = None
    published_end: datetime | None = None
    limit: int = Field(default=20, ge=1, le=20)
    include_terms: list[str] = Field(default_factory=list, max_length=20)
    exclude_terms: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_ranges(self):
        """要求所有指定时间带时区，且每组范围起点早于终点。"""
        for start_name, end_name in (("start", "end"), ("published_start", "published_end")):
            start, end = getattr(self, start_name), getattr(self, end_name)
            if start and not start.tzinfo or end and not end.tzinfo:
                raise ValueError("时间范围必须包含时区")
            if start and end and start >= end:
                raise ValueError("时间范围开始时间必须早于结束时间")
        return self


def _range_error(start, end, published_start, published_end) -> bool:
    """检查 HTTP 查询参数中的时区和时间范围顺序。"""
    for lower, upper in ((start, end), (published_start, published_end)):
        if lower and not lower.tzinfo or upper and not upper.tzinfo:
            return True
        if lower and upper and lower >= upper:
            return True
    return False


def _vector_lookup(status):
    """仅在集合可用时访问向量服务，故障时明确进入关键词回退。"""
    # 兼容旧状态响应：早期接口只返回 exists，未显式失败即允许查询。
    if status.get("available", True) and status.get("exists"):
        return vector_event_ids
    return lambda *args: VectorMatches(available=False)


def _source_fallback(row) -> list[EventSource]:
    """为旧格式事件补充其已保存来源记录，不联网获取新内容。"""
    raw_sources = (row.facts or {}).get("sources")
    if raw_sources:
        return [EventSource.model_validate(source) for source in raw_sources]
    return [EventSource(source=row.source, url=row.url, published_at=row.published_at.isoformat(), excerpt=row.summary)]


def _event_record(row) -> EventRecord:
    """从已授权事件记录构建模型可消费的专题资料。"""
    raw = row.facts if isinstance(row.facts, dict) else {}
    sources = _source_fallback(row)
    return EventRecord(
        title=row.title, summary=row.summary,
        facts=raw.get("facts", []) if isinstance(raw.get("facts", []), list) else [],
        sources=sources,
        differences=raw.get("differences", []), conflicts=raw.get("conflicts", []),
        uncertainties=raw.get("uncertainties", []), event_key=raw.get("event_key", ""),
    )


def _search(db: Session, user: User, query: str, limit: int, start, end,
            published_start, published_end, include_terms=(), exclude_terms=()):
    """集中执行带当前用户隔离的双时间范围混合检索。"""
    status = milvus_status()
    matches = search_events(
        db, user.id, query, limit, start, end, published_start, published_end,
        vector_lookup=_vector_lookup(status), include_terms=include_terms, exclude_terms=exclude_terms,
    )
    return matches


@router.get("")
def history(query: str = Query(min_length=2, max_length=200),
            limit: int = Query(default=20, ge=1, le=20),
            start: datetime | None = None, end: datetime | None = None,
            published_start: datetime | None = None, published_end: datetime | None = None,
            include_terms: list[str] = Query(default=[]), exclude_terms: list[str] = Query(default=[]),
            db: Session = Depends(get_db), user: User = Depends(current_user)):
    """返回当前用户在两个可选时间范围内的混合历史召回。"""
    if _range_error(start, end, published_start, published_end):
        raise HTTPException(422, detail={"code": "INVALID_TIME_RANGE", "message": "历史范围必须带时区且开始早于结束"})
    try:
        rows = _search(db, user, query, limit, start, end, published_start, published_end,
                       include_terms, exclude_terms)
    except Exception as exc:
        raise HTTPException(503, detail={"code": "HISTORY_SEARCH_UNAVAILABLE", "message": "暂时无法完成历史查询"}) from exc
    return {
        "query": query, "mode": rows.mode,
        "items": [
            {"id": row.id, "title": row.title, "summary": row.summary,
             "source": row.source, "url": row.url, "published_at": row.published_at,
             "sources": (row.facts or {}).get("sources", []), "score": rows.scores.get(row.id, 0.0)}
            for row in rows
        ],
    }


@router.post("/briefings")
def create_history_briefing(payload: HistoryBriefingRequest, db: Session = Depends(get_db),
                            user: User = Depends(current_user)):
    """只用用户现有事件生成 HTML，并保存专题查询和原事件引用。"""
    try:
        rows = _search(db, user, payload.query, payload.limit, payload.start, payload.end,
                       payload.published_start, payload.published_end,
                       payload.include_terms, payload.exclude_terms)
    except Exception as exc:
        raise HTTPException(503, detail={"code": "HISTORY_SEARCH_UNAVAILABLE", "message": "暂时无法完成历史查询"}) from exc

    query_record = payload.model_dump(mode="json")
    briefing = Briefing(
        user_id=user.id, title=f"历史专题：{payload.query}", query=payload.query,
        status="no_events" if not rows else "generating", html_path="", event_count=len(rows),
        event_ids=[row.id for row in rows], history_query=query_record,
    )
    db.add(briefing)
    db.commit()
    db.refresh(briefing)
    if not rows:
        return {"status": "no_events", "id": briefing.id, "query": payload.query, "event_count": 0, "items": []}

    events = [_event_record(row) for row in rows]
    style = dict(user.style or {})
    style["sort"] = "relevance"
    try:
        html = generate_html(
            events, payload.query, user.language, style,
            time_window={"start": payload.start.isoformat() if payload.start else None,
                         "end": payload.end.isoformat() if payload.end else None,
                         "published_start": payload.published_start.isoformat() if payload.published_start else None,
                         "published_end": payload.published_end.isoformat() if payload.published_end else None},
        )
        root = get_settings().html_root / user.id
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{briefing.id}.html"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(html, encoding="utf-8")
        temporary.replace(path)
        briefing.html_path = str(path.resolve())
        briefing.status = "succeeded"
        db.commit()
    except Exception as exc:
        db.rollback()
        failed = db.get(Briefing, briefing.id)
        if failed is not None:
            failed.status = "generation_failed"
            failed.history_query = query_record | {"error_code": type(exc).__name__[:64]}
            db.commit()
        raise HTTPException(503, detail={"code": "HISTORY_BRIEFING_FAILED", "message": "历史专题生成失败，可稍后重试", "briefing_id": briefing.id}) from exc
    return {"status": briefing.status, "id": briefing.id, "title": briefing.title,
            "event_count": briefing.event_count, "event_ids": briefing.event_ids}
