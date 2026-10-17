"""认证与设置 API 的隔离集成测试。"""
from fastapi.testclient import TestClient
from app.main import app

def test_register_login_and_settings_flow():
    """验证账号创建、会话校验、设置更新及重复邮箱拒绝。"""
    with TestClient(app) as client:
        import uuid
        suffix = f"{uuid.uuid4().hex}@example.com"
        response = client.post("/api/v1/auth/register", json={"email": suffix, "password": "secure-pass-123", "nickname": "测试用户"})
        assert response.status_code == 200
        assert response.json()["email"] == suffix
        assert "httponly" in response.headers["set-cookie"].lower()
        assert client.get("/api/v1/me").status_code == 200
        updated = client.patch("/api/v1/settings", json={"topics": ["AI"], "keywords": ["agent"]})
        assert updated.status_code == 200
        assert updated.json()["topics"] == ["AI"]
        assert client.post("/api/v1/auth/register", json={"email": suffix, "password": "secure-pass-123"}).status_code == 409
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert client.get("/api/v1/me").status_code == 401

def test_settings_require_authentication():
    """验证未认证用户不能读取或修改订阅设置。"""
    with TestClient(app) as client:
        assert client.get("/api/v1/me").status_code == 401
        assert client.patch("/api/v1/settings", json={"topics": ["private"]}).status_code == 401
