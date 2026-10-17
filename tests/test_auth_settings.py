"""认证、字段校验和设置乐观锁的隔离回归测试。"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import SessionToken, User
from app.main import app
from app.services.security import (
    SettingsPatch, SettingsVersionConflict, hash_password, token_digest,
    update_user_settings, verify_password,
)


def register(client, email="settings@example.com"):
    """在当前测试数据库创建用户并返回公开资料。"""
    response = client.post("/api/v1/auth/register", json={
        "email": email, "password": "old-password-123", "nickname": "测试昵称",
        "timezone": "Europe/London",
    })
    assert response.status_code == 200
    return response


def test_session_lifetime_and_storage(client):
    """注册使用七天安全 Cookie，数据库只保存令牌摘要。"""
    response = register(client, "Settings@Example.com")
    assert response.json()["email"] == "settings@example.com"
    assert response.json()["timezone"] == "Europe/London"
    cookie = response.headers["set-cookie"]
    assert "Max-Age=604800" in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "password_hash" not in response.json()
    with next(app.dependency_overrides[get_db]()) as db:
        record = db.scalar(select(SessionToken))
        assert record.token_hash == token_digest(client.cookies.get("session"))
        expires = record.expires_at.replace(tzinfo=timezone.utc)
        assert timedelta(days=6, hours=23) < expires - datetime.now(timezone.utc) <= timedelta(days=7)
    assert client.get("/api/v1/auth/me").json()["id"] == response.json()["id"]


def test_change_password_revokes_other_sessions_only(client):
    """修改密码后旧密码失效，当前会话保留而其他设备会话撤销。"""
    first = register(client)
    first_token = client.cookies.get("session")
    client.post("/api/v1/auth/login", json={"email": first.json()["email"], "password": "old-password-123"})
    current_token = client.cookies.get("session")
    response = client.post("/api/v1/auth/password", json={
        "current_password": "old-password-123", "new_password": "new-password-456",
    })
    assert response.status_code == 200
    assert client.get("/api/v1/me").status_code == 200
    with next(app.dependency_overrides[get_db]()) as db:
        assert db.get(SessionToken, token_digest(first_token)) is None
        assert db.get(SessionToken, token_digest(current_token)) is not None
    client.cookies.clear()
    client.cookies.set("session", first_token)
    assert client.get("/api/v1/me").status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": first.json()["email"], "password": "old-password-123"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": first.json()["email"], "password": "new-password-456"}).status_code == 200


def test_password_failures_leave_session_and_password_unchanged(client):
    """错误旧密码和过短新密码不会更改账号。"""
    register(client)
    assert client.post("/api/v1/auth/password", json={"current_password": "wrong", "new_password": "new-password-456"}).status_code == 401
    assert client.post("/api/v1/auth/password", json={"current_password": "old-password-123", "new_password": "short"}).status_code == 422
    assert client.get("/api/v1/me").status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "settings@example.com", "password": "old-password-123"}).status_code == 200


def test_expired_session_is_removed(client):
    """过期会话拒绝访问并在数据库清理。"""
    register(client)
    digest = token_digest(client.cookies.get("session"))
    with next(app.dependency_overrides[get_db]()) as db:
        db.get(SessionToken, digest).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/v1/me").status_code == 401
    with next(app.dependency_overrides[get_db]()) as db:
        assert db.get(SessionToken, digest) is None


def test_settings_patch_preserves_omitted_fields_and_merges_style(client):
    """局部保存风格不能丢失其他风格配置或用户资料。"""
    initial = register(client).json()
    response = client.patch("/api/v1/settings", json={
        "topics": ["  AI  "], "style": {"summary_max_chars": 120}, "version": 1,
    })
    assert response.status_code == 200
    value = response.json()
    assert value["topics"] == ["AI"]
    assert value["nickname"] == initial["nickname"]
    assert value["style"] == {**initial["style"], "summary_max_chars": 120}
    assert value["settings_version"] == 2
    assert client.get("/api/v1/me").json() == value


def test_daily_time_can_be_explicitly_cleared(client):
    """每日时间显式 null 被持久化，省略字段不会清空它。"""
    register(client)
    assert client.patch("/api/v1/settings", json={"daily_push_time": "08:30"}).status_code == 200
    assert client.patch("/api/v1/settings", json={"nickname": "新昵称"}).json()["daily_push_time"] == "08:30"
    response = client.patch("/api/v1/settings", json={"daily_push_time": None})
    assert response.status_code == 200
    assert response.json()["daily_push_time"] is None
    assert client.get("/api/v1/me").json()["daily_push_time"] is None


@pytest.mark.parametrize("patch", [
    {"timezone": "Invalid/Zone"}, {"timezone": "../UTC"},
    {"language": "fr"}, {"daily_push_time": "24:00"}, {"daily_push_time": "8:30"},
    {"nickname": "x" * 81}, {"topics": [" "]}, {"keywords": ["x" * 201]},
    {"style": {"summary_max_chars": 79}}, {"style": {"summary_min_chars": 20}},
    {"style": {"max_events": 0}}, {"style": {"max_events": True}},
    {"style": {"layout": "unknown"}}, {"style": {"max_events": None}},
    {"email": "other@example.com"}, {"timezone": None}, {"style": None},
    {"version": 0}, {"version": None},
])
def test_invalid_settings_do_not_mutate_database(client, patch):
    """拒绝格式、范围、空值与未知字段，失败时不递增版本。"""
    initial = register(client).json()
    assert client.patch("/api/v1/settings", json=patch).status_code == 422
    assert client.get("/api/v1/me").json() == initial


def test_stale_version_does_not_overwrite_settings(client):
    """过期页面提交返回冲突并保留更新后的偏好。"""
    register(client)
    assert client.patch("/api/v1/settings", json={"nickname": "赢家", "version": 1}).status_code == 200
    response = client.patch("/api/v1/settings", json={"nickname": "迟到", "version": 1})
    assert response.status_code == 409
    assert client.get("/api/v1/me").json()["nickname"] == "赢家"


def test_stale_loaded_user_is_rejected_atomically(client):
    """两个数据库会话读取同一版本时，迟到更新不能覆盖已提交值。"""
    user_id = register(client).json()["id"]
    with next(app.dependency_overrides[get_db]()) as first:
        stale = first.get(User, user_id)
        with Session(first.bind, expire_on_commit=False) as second:
            fresh = second.get(User, user_id)
            update_user_settings(second, fresh, SettingsPatch(nickname="赢家"))
        with pytest.raises(SettingsVersionConflict):
            update_user_settings(first, stale, SettingsPatch(nickname="迟到"))
        first.refresh(stale)
        assert stale.nickname == "赢家"
        assert stale.settings_version == 2


def test_password_hash_rejects_invalid_storage_format():
    """损坏哈希与未知算法不能绕过密码验证。"""
    encoded = hash_password("password-123")
    assert verify_password("password-123", encoded)
    assert not verify_password("wrong", encoded)
    assert not verify_password("password-123", encoded.replace("scrypt", "other", 1))
    assert not verify_password("password-123", "broken")
