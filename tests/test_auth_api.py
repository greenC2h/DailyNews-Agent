"""认证与设置 API 的隔离集成测试。"""
def test_register_login_and_settings_flow(client):
    """验证账号创建、会话校验、设置更新及重复邮箱拒绝。"""
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

def test_settings_require_authentication(client):
    """验证未认证用户不能读取或修改订阅设置。"""
    assert client.get("/api/v1/me").status_code == 401
    assert client.patch("/api/v1/settings", json={"topics": ["private"]}).status_code == 401

def test_immediate_task_requires_explicit_window(client):
    """即时简报缺少时间范围时必须补问而不能静默扩大范围。"""
    client.post("/api/v1/auth/register", json={"email":"window@example.com","password":"secure-pass-123"})
    response = client.post("/api/v1/news/briefings", json={"query":"AI agents"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "TIME_RANGE_REQUIRED"
