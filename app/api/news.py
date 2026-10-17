"""即时新闻简报 API。"""
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.api.auth import current_user
from app.config import get_settings
from app.db.database import get_db
from app.db.models import User
from app.db.news_models import Briefing, NewsEvent
from app.services.news import fetch_articles

router = APIRouter(prefix="/api/v1/news", tags=["news"])
settings = get_settings()

class BriefingRequest(BaseModel):
    """即时简报请求。"""
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)

@router.post("/briefings")
def create_briefing(payload: BriefingRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """采集新闻、生成 HTML 并保存简报与事件来源。"""
    try: articles = fetch_articles(payload.query, limit=payload.limit)
    except Exception as exc: raise HTTPException(502, detail={"code":"NEWS_SOURCE_UNAVAILABLE","message":"新闻来源暂时不可用"}) from exc
    if not articles: raise HTTPException(422, detail={"code":"NO_EVENTS","message":"没有获取到正文可用的相关新闻"})
    briefing = Briefing(user_id=user.id, title=f"{payload.query} 新闻简报", query=payload.query, event_count=len(articles), html_path="")
    db.add(briefing); db.flush()
    cards = []
    for article in articles:
        summary = article.content[: min(user.style.get("summary_max_chars", 400), 400)]
        db.add(NewsEvent(briefing_id=briefing.id, user_id=user.id, title=article.title, source=article.source, url=article.url, published_at=article.published_at, summary=summary, facts={"published_at": article.published_at.isoformat()}))
        cards.append(f"<article><h2>{escape(article.title)}</h2><p>{escape(summary)}</p><p><a href=\"{escape(article.url)}\">来源：{escape(article.source)}</a></p></article>")
    html = f"<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>{escape(briefing.title)}</title><main><h1>{escape(briefing.title)}</h1><p>生成时间：{datetime.now(timezone.utc).isoformat()}</p>{''.join(cards)}</main>"
    root: Path = settings.html_root / user.id; root.mkdir(parents=True, exist_ok=True); path = root / f"{briefing.id}.html"; path.write_text(html, encoding="utf-8"); briefing.html_path = str(path); db.commit()
    return {"id": briefing.id, "status": briefing.status, "event_count": briefing.event_count, "html": html}

@router.get("/briefings")
def list_briefings(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """返回当前用户的简报列表。"""
    rows = db.query(Briefing).filter(Briefing.user_id == user.id).order_by(Briefing.created_at.desc()).all()
    return [{"id": row.id, "title": row.title, "status": row.status, "event_count": row.event_count, "created_at": row.created_at} for row in rows]
