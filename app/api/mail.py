"""简报邮件发送及待确认状态 API。"""
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.auth import current_user
from app.db.database import get_db
from app.db.models import User
from app.db.news_models import Briefing, MailDelivery
from app.services.mailer import send_html_email
from app.config import get_settings
from datetime import datetime, timezone
router = APIRouter(prefix="/api/v1", tags=["mail"]); settings = get_settings()

def _delivery(delivery_id: str, db: Session, user: User) -> MailDelivery:
    """读取当前用户邮件记录并执行归属校验。"""
    record = db.query(MailDelivery).filter(MailDelivery.id == delivery_id, MailDelivery.user_id == user.id).first()
    if not record: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"邮件记录不存在"})
    return record

@router.post("/briefings/{briefing_id}/send")
def send_briefing(briefing_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """使用账号邮箱发送已保存 HTML，并保存不可覆盖的发送记录。"""
    briefing = db.query(Briefing).filter(Briefing.id == briefing_id, Briefing.user_id == user.id).first()
    if not briefing: raise HTTPException(404, detail={"code":"NOT_FOUND","message":"简报不存在"})
    record = MailDelivery(user_id=user.id, briefing_id=briefing.id, recipient_email=user.email, status="sending", attempts=1); db.add(record); db.commit()
    try:
        send_html_email(user.email, briefing.title, Path(briefing.html_path).read_text(encoding="utf-8")); record.status="success"; record.sent_at=datetime.now(timezone.utc); db.commit()
    except TimeoutError:
        record.status="pending_confirmation"; record.last_error="SMTP_TIMEOUT"; db.commit(); raise HTTPException(202, detail={"code":"MAIL_PENDING_CONFIRMATION","message":"发送结果待确认","delivery_id":record.id})
    except Exception as exc:
        record.status="failed"; record.last_error=type(exc).__name__[:500]; db.commit(); raise HTTPException(502, detail={"code":"MAIL_FAILED","message":"邮件发送失败","delivery_id":record.id}) from exc
    return {"delivery_id":record.id,"status":record.status,"sent_at":record.sent_at}

@router.post("/deliveries/{delivery_id}/confirm")
def confirm_delivery(delivery_id: str, delivered: bool, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """处理待确认邮件：确认送达后禁止重发，确认未送达后允许重试。"""
    record = _delivery(delivery_id, db, user)
    if record.status != "pending_confirmation": raise HTTPException(409, detail={"code":"INVALID_STATUS","message":"只有待确认记录可以确认"})
    record.status = "success" if delivered else "failed"; record.last_error = None if delivered else "CONFIRMED_NOT_DELIVERED"; db.commit(); return {"delivery_id":record.id,"status":record.status,"allowed_actions":[] if delivered else ["retry"]}

@router.post("/deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """复用原简报 HTML 创建新的邮件发送记录，不覆盖原记录。"""
    original = _delivery(delivery_id, db, user)
    if original.status not in {"failed"}: raise HTTPException(409, detail={"code":"RETRY_NOT_ALLOWED","message":"当前邮件状态不可重试"})
    briefing = db.get(Briefing, original.briefing_id)
    record = MailDelivery(user_id=user.id, briefing_id=briefing.id, recipient_email=original.recipient_email, status="sending", attempts=original.attempts + 1); db.add(record); db.commit()
    try:
        send_html_email(record.recipient_email, briefing.title, Path(briefing.html_path).read_text(encoding="utf-8")); record.status="success"; record.sent_at=datetime.now(timezone.utc); db.commit()
    except Exception as exc:
        record.status="failed"; record.last_error=type(exc).__name__[:500]; db.commit(); raise HTTPException(502, detail={"code":"MAIL_FAILED","message":"邮件重试失败","delivery_id":record.id}) from exc
    return {"delivery_id":record.id,"status":record.status}
