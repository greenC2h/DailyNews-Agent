"""邮件发送、确认和重试接口。"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.auth import current_user
from app.db.database import get_db
from app.db.models import User
from app.db.news_models import Briefing, MailDelivery
from app.services.delivery import deliver, delivery_payload

router = APIRouter(prefix="/api/v1", tags=["mail"])

def _delivery(delivery_id: str, db: Session, user: User) -> MailDelivery:
    """锁定所属邮件记录，阻止并发确认和重试。"""
    record = db.scalar(select(MailDelivery).where(MailDelivery.id == delivery_id, MailDelivery.user_id == user.id).with_for_update())
    if not record:
        raise HTTPException(404, detail={"code":"NOT_FOUND","message":"邮件记录不存在"})
    return record

@router.get("/briefings/{briefing_id}/deliveries")
def list_deliveries(briefing_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """返回当前用户简报的不可覆盖投递记录。"""
    return [delivery_payload(row) for row in db.scalars(select(MailDelivery).where(MailDelivery.user_id == user.id, MailDelivery.briefing_id == briefing_id).order_by(MailDelivery.created_at.desc())).all()]

@router.post("/briefings/{briefing_id}/send")
def send_briefing(briefing_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """显式要求时发送已保存 HTML，重复请求返回已有有效记录。"""
    briefing = db.scalar(select(Briefing).where(Briefing.id == briefing_id, Briefing.user_id == user.id))
    if not briefing:
        raise HTTPException(404, detail={"code":"NOT_FOUND","message":"简报不存在"})
    return delivery_payload(deliver(db, briefing, user.email))

@router.post("/deliveries/{delivery_id}/confirm")
def confirm_delivery(delivery_id: str, delivered: bool, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """由用户确认不确定结果，保留原记录并决定是否允许人工重试。"""
    record = _delivery(delivery_id, db, user)
    if record.status != "pending_confirmation":
        raise HTTPException(409, detail={"code":"INVALID_STATUS","message":"只有待确认记录可以确认"})
    record.status = "success" if delivered else "failed"
    record.last_error = "USER_CONFIRMED_DELIVERED" if delivered else "CONFIRMED_NOT_DELIVERED"
    if delivered:
        record.sent_at = datetime.now(timezone.utc)
    db.commit(); return delivery_payload(record)

@router.post("/deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """为明确失败创建新记录，使用原收件人和已保存 HTML。"""
    original = _delivery(delivery_id, db, user)
    if original.status != "failed":
        raise HTTPException(409, detail={"code":"RETRY_NOT_ALLOWED","message":"当前邮件状态不可重试"})
    briefing = db.scalar(select(Briefing).where(Briefing.id == original.briefing_id, Briefing.user_id == user.id))
    return delivery_payload(deliver(db, briefing, original.recipient_email, original))
