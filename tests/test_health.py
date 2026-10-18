"""就绪检查与统一请求追踪响应的回归测试。"""
from unittest.mock import MagicMock

from app import main


def test_health_requires_database(client, monkeypatch):
    """数据库查询正常时报告健康，连接失败时返回 503。"""
    connection = MagicMock()
    connection.__enter__.return_value = connection
    monkeypatch.setattr(main.engine, "connect", lambda: connection)
    healthy = client.get("/healthz")
    assert healthy.status_code == 200
    assert healthy.json() == {"status": "ok", "database": "ok"}
    connection.exec_driver_sql.assert_called_once_with("SELECT 1")

    def unavailable():
        """模拟数据库连接失败。"""
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(main.engine, "connect", unavailable)
    unhealthy = client.get("/healthz")
    assert unhealthy.status_code == 503
    assert unhealthy.json() == {"status": "unhealthy", "database": "unavailable"}


def test_http_and_validation_errors_include_request_id(client):
    """错误响应头回显请求追踪编号且错误详情保持既有结构。"""
    unauthorized = client.get("/api/v1/me", headers={"X-Request-ID": "acceptance-123"})
    assert unauthorized.status_code == 401
    assert unauthorized.headers["x-request-id"] == "acceptance-123"
    assert unauthorized.json()["detail"]["code"] == "AUTH_REQUIRED"

    invalid = client.post("/api/v1/auth/register", json={"email": "invalid", "password": "short"})
    assert invalid.status_code == 422
    assert invalid.headers.get("x-request-id")
    assert isinstance(invalid.json()["detail"], list)

    unsafe = client.get("/api/v1/me", headers={"X-Request-ID": "bad\r\nid"})
    assert unsafe.status_code == 401
    assert unsafe.headers["x-request-id"] != "bad\r\nid"
