"""账号和订阅设置数据模型。"""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base
class User(Base):
    """保存用户身份和订阅偏好。"""
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256)); nickname: Mapped[str] = mapped_column(String(80), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai"); topics: Mapped[list] = mapped_column(JSON, default=list); keywords: Mapped[list] = mapped_column(JSON, default=list); language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    style: Mapped[dict] = mapped_column(JSON, default=lambda: {"max_events":5,"summary_max_chars":400,"summary_min_chars":80,"layout":"list","sort":"latest"}); daily_push_time: Mapped[str | None] = mapped_column(String(5), nullable=True); settings_version: Mapped[int] = mapped_column(Integer, default=1); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)); sessions: Mapped[list["SessionToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")
class SessionToken(Base):
    """保存会话令牌摘要。"""
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True); user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True); expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True)); user: Mapped[User] = relationship(back_populates="sessions")
