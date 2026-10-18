"""邮件投递状态机的隔离回归测试。"""
from datetime import datetime, timedelta, timezone
import smtplib
import pytest

from app.db.database import get_db
from app.db.news_models import Briefing, MailDelivery
from app.services import delivery as delivery_service
from app.main import app


def _create_briefing(client, tmp_path):
    """创建已登录测试用户及其本地 HTML 简报。"""
    user = client.post(
        "/api/v1/auth/register",
        json={"email": "delivery@example.com", "password": "secure-pass-123"},
    ).json()
    path = tmp_path / "delivery.html"
    path.write_text("<!doctype html><h1>邮件状态测试</h1>", encoding="utf-8")
    dependency = app.dependency_overrides[get_db]()
    db = next(dependency)
    try:
        briefing = Briefing(
            user_id=user["id"], title="状态测试", query="状态测试",
            html_path=str(path), event_count=1,
        )
        db.add(briefing)
        db.commit()
        return briefing.id, user["email"]
    finally:
        dependency.close()


def _database_session():
    """取得当前测试数据库会话及其依赖清理句柄。"""
    dependency = app.dependency_overrides[get_db]()
    return next(dependency), dependency


def test_explicit_smtp_rejection_retries_once_and_manual_retry_is_new(client, tmp_path, monkeypatch):
    """明确 SMTP 收件人拒绝自动尝试两次，人工重试保留独立记录。"""
    briefing_id, recipient = _create_briefing(client, tmp_path)
    calls = []

    def reject_recipient(address, subject, html):
        """模拟 SMTP 明确拒绝收件人，不产生外部投递。"""
        calls.append(address)
        raise smtplib.SMTPRecipientsRefused({address: (550, b"rejected")})

    monkeypatch.setattr(delivery_service, "send_html_email", reject_recipient)
    db, dependency = _database_session()
    try:
        briefing = db.get(Briefing, briefing_id)
        original = delivery_service.deliver(db, briefing, recipient)
        assert original.status == "failed"
        assert original.attempts == 2
        assert calls == [recipient, recipient]
        original_id = original.id
    finally:
        dependency.close()

    response = client.post(f"/api/v1/deliveries/{original_id}/retry")
    retried = response.json()
    assert response.status_code == 200
    assert retried["status"] == "failed"
    assert retried["attempts"] == 2
    assert retried["id"] != original_id
    db, dependency = _database_session()
    try:
        assert db.get(MailDelivery, retried["id"]).original_id == original_id
    finally:
        dependency.close()
    assert calls == [recipient] * 4


def test_uncertain_smtp_result_waits_for_confirmation_without_retry(client, tmp_path, monkeypatch):
    """连接中断视为结果不确定，只允许确认而不自动重发。"""
    briefing_id, recipient = _create_briefing(client, tmp_path)
    calls = []

    def disconnect(address, subject, html):
        """模拟发送期间连接中断，避免访问真实 SMTP。"""
        calls.append(address)
        raise ConnectionError("connection interrupted")

    monkeypatch.setattr(delivery_service, "send_html_email", disconnect)
    db, dependency = _database_session()
    try:
        record = delivery_service.deliver(db, db.get(Briefing, briefing_id), recipient)
        assert record.status == "pending_confirmation"
        assert record.attempts == 1
        assert delivery_service.delivery_payload(record)["allowed_actions"] == ["confirm"]
        assert calls == [recipient]
    finally:
        dependency.close()


def test_expired_sending_lease_requires_user_confirmation(client, tmp_path):
    """恢复过期发送租约后，确认未送达才转为可重试失败。"""
    briefing_id, recipient = _create_briefing(client, tmp_path)
    now = datetime.now(timezone.utc)
    db, dependency = _database_session()
    try:
        briefing = db.get(Briefing, briefing_id)
        record = MailDelivery(
            user_id=briefing.user_id, briefing_id=briefing.id,
            recipient_email=recipient, status="sending", attempts=1,
            lease_expires_at=now - timedelta(seconds=1),
        )
        db.add(record)
        db.commit()
        record_id = record.id
        assert delivery_service.recover_mail(db, now) == 1
        assert db.get(MailDelivery, record_id).status == "pending_confirmation"
    finally:
        dependency.close()

    response = client.post(
        f"/api/v1/deliveries/{record_id}/confirm", params={"delivered": "false"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["allowed_actions"] == ["retry"]


def test_process_crash_after_smtp_acceptance_recovers_without_resend(client, tmp_path, monkeypatch):
    """模拟中继接受正文后进程在最终落库前退出，恢复只能进入待确认且不重发。"""
    briefing_id, recipient = _create_briefing(client, tmp_path)
    accepted = []

    def accepted_then_process_crash(address, subject, html):
        """记录模拟中继已接受 DATA 后，让调用进程立即退出。"""
        accepted.append(address)
        raise SystemExit("crash after remote acceptance")

    monkeypatch.setattr(delivery_service, "send_html_email", accepted_then_process_crash)
    db, dependency = _database_session()
    try:
        briefing = db.get(Briefing, briefing_id)
        original_commit = db.commit
        commits = 0

        def crash_on_final_commit():
            """保留发送中租约的已提交状态，模拟进程在成功落库前崩溃。"""
            nonlocal commits
            commits += 1
            if commits >= 3:
                raise SystemExit("crash before success commit")
            original_commit()

        db.commit = crash_on_final_commit
        with pytest.raises(SystemExit, match="crash after remote acceptance"):
            delivery_service.deliver(db, briefing, recipient)
        record_id = db.query(MailDelivery.id).filter(MailDelivery.briefing_id == briefing_id).scalar()
    finally:
        dependency.close()

    db, dependency = _database_session()
    try:
        recovered = db.get(MailDelivery, record_id)
        assert recovered.status == "sending"
        recovered.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert delivery_service.recover_mail(db, datetime.now(timezone.utc)) == 1
        recovered = db.get(MailDelivery, record_id)
        assert recovered.status == "pending_confirmation"
        assert recovered.last_error == "WORKER_LOST_DURING_SEND"
        assert recovered.attempts == 1
        assert accepted == [recipient]
    finally:
        dependency.close()
