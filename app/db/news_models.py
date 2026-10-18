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
    html_path: Mapped[str] = mapped_column(String(500)); html_size: Mapped[int | None] = mapped_column(Integer, nullable=True); html_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True); event_count: Mapped[int] = mapped_column(Integer, default=0); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_ids: Mapped[list] = mapped_column(JSON, default=list)
    history_query: Mapped[dict] = mapped_column(JSON, default=dict)
class NewsEvent(Base):
    """保存简报中的来源事件资料。"""
    __tablename__ = "news_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4())); briefing_id: Mapped[str] = mapped_column(ForeignKey("briefings.id", ondelete="CASCADE"), index=True); user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500)); source: Mapped[str] = mapped_column(String(200)); url: Mapped[str] = mapped_column(String(2000)); published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True)); summary: Mapped[str] = mapped_column(Text); facts: Mapped[dict] = mapped_column(JSON, default=dict)
    index_status: Mapped[str] = mapped_column(String(20), default="pending")
    index_error: Mapped[str | None] = mapped_column(String(100), nullable=True)
    search_text: Mapped[str] = mapped_column(Text, default="")
    index_retry_count: Mapped[int] = mapped_column(Integer, default=0)

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
    scheduled_task_id: Mapped[str | None] = mapped_column(ForeignKey("scheduled_tasks.id"), nullable=True, index=True)
    planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    intermediate: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    intermediate_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    send_email: Mapped[bool] = mapped_column(default=False)
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
    is_default: Mapped[bool] = mapped_column(default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    overrides: Mapped[dict] = mapped_column(JSON, default=dict)

class MailDelivery(Base):
    """记录简报邮件发送状态，保留不确定结果。"""
    __tablename__ = "mail_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    briefing_id: Mapped[str] = mapped_column(ForeignKey("briefings.id", ondelete="CASCADE"), index=True)
    recipient_email: Mapped[str] = mapped_column(String(320)); status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0); last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    original_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

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
