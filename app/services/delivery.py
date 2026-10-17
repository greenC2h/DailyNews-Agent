"""具有崩溃边界和有限重试的邮件投递事务。"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import smtplib
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.news_models import Briefing, MailDelivery
from app.services.mailer import send_html_email

UNKNOWN_ERRORS = (TimeoutError, ConnectionError, smtplib.SMTPServerDisconnected)

def delivery_payload(record: MailDelivery) -> dict:
    """返回状态及允许操作，成功记录不能再次发送。"""
    return {"id": record.id, "delivery_id": record.id, "briefing_id": record.briefing_id, "status": record.status, "attempts": record.attempts, "last_error": record.last_error, "sent_at": record.sent_at, "recipient_email": record.recipient_email, "allowed_actions": ["retry"] if record.status == "failed" else (["confirm"] if record.status == "pending_confirmation" else [])}

def deliver(db: Session, briefing: Briefing, recipient: str, original: MailDelivery | None = None) -> MailDelivery:
    """保存发送中后调用 SMTP；明确 SMTP 拒绝重试一次，不确定结果永不重放。

    Args:
        db: 业务会话，发送前后均独立提交状态。
        briefing: 已提交且属于发送用户的简报。
        recipient: 创建时固定的账号邮箱快照。
        original: 人工重试的原记录，永不覆盖其结果。
    """
    # 锁简报序列化重复点击；有原记录的显式重试仍保留完整发送谱系。
    db.scalar(select(Briefing).where(Briefing.id == briefing.id).with_for_update())
    prior = db.scalar(select(MailDelivery).where(MailDelivery.briefing_id == briefing.id, MailDelivery.status.in_(["sending", "success", "pending_confirmation"])).order_by(MailDelivery.created_at.desc()))
    if prior:
        return prior
    record = MailDelivery(user_id=briefing.user_id, briefing_id=briefing.id, recipient_email=recipient, status="sending", attempts=0, original_id=original.id if original else None, lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=2))
    db.add(record); db.commit(); db.refresh(record)
    try:
        html = Path(briefing.html_path).read_text(encoding="utf-8")
    except OSError:
        record.status = "failed"; record.last_error = "HTML_UNAVAILABLE"; record.lease_expires_at = None; db.commit()
        return record
    for attempt in range(2):
        record.attempts += 1
        db.commit()
        try:
            send_html_email(record.recipient_email, briefing.title, html)
        except UNKNOWN_ERRORS:
            record.status = "pending_confirmation"; record.last_error = "SMTP_RESULT_UNKNOWN"
            break
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError, smtplib.SMTPAuthenticationError) as exc:
            record.status = "failed"; record.last_error = type(exc).__name__
            # 只有服务器明确拒绝才确定没有成功投递。
            if attempt == 0:
                continue
            break
        except Exception as exc:
            record.status = "failed"; record.last_error = type(exc).__name__
            break
        else:
            record.status = "success"; record.last_error = None; record.sent_at = datetime.now(timezone.utc)
            break
    record.lease_expires_at = None
    db.commit()
    return record

def recover_mail(db: Session, now: datetime) -> int:
    """将发送租约过期记录置为待确认，绝不自动重发。"""
    rows = db.scalars(select(MailDelivery).where(MailDelivery.status == "sending", MailDelivery.lease_expires_at <= now).with_for_update(skip_locked=True)).all()
    for row in rows:
        row.status = "pending_confirmation"; row.last_error = "WORKER_LOST_DURING_SEND"; row.lease_expires_at = None
    db.commit()
    return len(rows)
