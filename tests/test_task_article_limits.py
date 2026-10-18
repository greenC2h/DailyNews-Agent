"""验证事件上限与原始文章采集上限保持独立。"""
from datetime import datetime, timedelta, timezone


def _register(client):
    """创建测试账号并返回认证用户资料。"""
    response = client.post("/api/v1/auth/register", json={
        "email": "article-limit@example.com",
        "password": "password-123",
        "nickname": "采集上限测试",
        "timezone": "Asia/Shanghai",
    })
    assert response.status_code == 200
    return response.json()


def test_scheduled_task_distinguishes_event_and_article_limits(client):
    """定时任务允许独立配置文章上限且拒绝低于事件数的值。"""
    _register(client)
    run_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    response = client.post("/api/v1/tasks", json={
        "frequency": "daily", "run_at": run_at, "timezone": "Asia/Shanghai",
        "query": "人工智能", "limit": 5, "article_limit": 12,
    })
    assert response.status_code == 200
    assert response.json()["limit"] == 5
    assert response.json()["article_limit"] == 12

    invalid = client.post("/api/v1/tasks", json={
        "frequency": "daily", "run_at": run_at, "timezone": "Asia/Shanghai",
        "query": "人工智能", "limit": 5, "article_limit": 4,
    })
    assert invalid.status_code == 422


def test_default_daily_schedule_article_limit_and_event_count_guard(client):
    """默认每日任务可设文章上限，事件偏好不能越过手动上限。"""
    user = _register(client)
    assert client.patch("/api/v1/settings", json={"daily_push_time": "08:30", "version": 1}).status_code == 200
    task = next(row for row in client.get("/api/v1/tasks").json() if row["is_default"])
    changed = client.patch(f"/api/v1/tasks/{task['id']}/article-limit", json={"article_limit": 8})
    assert changed.status_code == 200
    assert changed.json()["article_limit"] == 8

    rejected = client.patch("/api/v1/settings", json={"style": {"max_events": 9}, "version": 2})
    assert rejected.status_code == 422
    assert client.get("/api/v1/me").json()["style"]["max_events"] == user["style"]["max_events"]

    followed = client.patch(f"/api/v1/tasks/{task['id']}/article-limit", json={"article_limit": None})
    assert followed.status_code == 200
    assert followed.json()["article_limit"] is None
