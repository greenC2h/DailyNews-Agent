"""简报和事件的持久化模型。"""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base
class Briefing(Base):
    """保存用户简报 HTML 和生成状态。"""
    __tablename__ = "briefings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200)); query: Mapped[str] = mapped_column(String(500)); status: Mapped[str] = mapped_column(String(32), default="succeeded")
    html_path: Mapped[str] = mapped_column(String(500)); event_count: Mapped[int] = mapped_column(Integer, default=0); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
class NewsEvent(Base):
    """保存简报中的来源事件资料。"""
    __tablename__ = "news_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4())); briefing_id: Mapped[str] = mapped_column(ForeignKey("briefings.id", ondelete="CASCADE"), index=True); user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500)); source: Mapped[str] = mapped_column(String(200)); url: Mapped[str] = mapped_column(String(2000)); published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True)); summary: Mapped[str] = mapped_column(Text); facts: Mapped[dict] = mapped_column(JSON, default=dict)
