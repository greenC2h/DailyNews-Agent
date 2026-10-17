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

class GenerationTask(Base):
    """保存立即简报任务及其可恢复执行状态。"""
    __tablename__ = "generation_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(String(500)); limit: Mapped[int] = mapped_column(Integer, default=5)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    briefing_id: Mapped[str | None] = mapped_column(ForeignKey("briefings.id", ondelete="SET NULL"), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class ScheduledTask(Base):
    """保存每日或一次性定时任务及其时区配置。"""
    __tablename__ = "scheduled_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200)); frequency: Mapped[str] = mapped_column(String(16))
    timezone: Mapped[str] = mapped_column(String(64)); run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    query: Mapped[str] = mapped_column(String(500)); limit: Mapped[int] = mapped_column(Integer, default=5)
    recipient_email: Mapped[str] = mapped_column(String(320)); status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True)); last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class MailDelivery(Base):
    """记录简报邮件发送状态，保留不确定结果。"""
    __tablename__ = "mail_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    briefing_id: Mapped[str] = mapped_column(ForeignKey("briefings.id", ondelete="CASCADE"), index=True)
    recipient_email: Mapped[str] = mapped_column(String(320)); status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0); last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class TaskExecution(Base):
    """保存每次立即或定时任务执行的阶段和结果。"""
    __tablename__ = "task_executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    generation_task_id: Mapped[str] = mapped_column(ForeignKey("generation_tasks.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running"); phase: Mapped[str] = mapped_column(String(32), default="queued")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True); event_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)); finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class ConversationMessage(Base):
    """保存用户会话消息，按用户和线程隔离。"""
    __tablename__ = "conversation_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[str] = mapped_column(String(100), index=True); role: Mapped[str] = mapped_column(String(20)); content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
