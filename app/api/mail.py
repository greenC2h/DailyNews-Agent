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
