"""生产资源路由及历史语义召回的回归测试。"""
import re
from datetime import datetime, timezone
from app.api import history as history_api
from app.db.database import get_db
from app.db.news_models import Briefing, NewsEvent
from app.main import app


def test_production_assets_are_served(client):
    """验证构建页面引用的脚本能由同一 FastAPI 服务返回。"""
    response = client.get("/")
    assert response.status_code == 200
    scripts = re.findall(r'src="(/assets/[^\"]+\.js)"', response.text)
    assert scripts, "请先运行 npm --prefix frontend run build"
    for script in scripts:
        resource = client.get(script)
        assert resource.status_code == 200
        assert "javascript" in resource.headers["content-type"]


def test_vector_only_hit_is_returned_and_tenant_checked(client, monkeypatch):
    """验证无关键词命中的向量事件可返回，跨用户向量 ID 被数据库鉴权排除。"""
    first = client.post("/api/v1/auth/register", json={"email":"rag-one@example.com","password":"secure-pass-123"}).json()
    client.post("/api/v1/auth/logout")
    second = client.post("/api/v1/auth/register", json={"email":"rag-two@example.com","password":"secure-pass-123"}).json()
    dependency = app.dependency_overrides[get_db]()
    db = next(dependency)
    ids = []
    try:
        for user in (first, second):
            brief = Briefing(user_id=user["id"], title="资料", query="资料", html_path="unused", event_count=1)
            db.add(brief); db.flush()
            event = NewsEvent(user_id=user["id"], briefing_id=brief.id, title="系统容错", summary="调用故障后的恢复机制", source="example", url="https://example.com", published_at=datetime.now(timezone.utc), facts={})
            db.add(event); db.flush(); ids.append(event.id)
        db.commit()
    finally:
        dependency.close()
    monkeypatch.setattr(history_api, "milvus_status", lambda: {"exists":True})
    monkeypatch.setattr(history_api, "vector_event_ids", lambda *args: ids)
    result = client.get("/api/v1/history", params={"query":"resilience"})
    assert result.status_code == 200
    assert [item["id"] for item in result.json()["items"]] == [ids[1]]


def test_preview_returns_html_with_script_blocking_policy(client, tmp_path):
    """验证预览返回 HTML 而非 JSON 字符串，并保留脚本隔离策略。"""
    user = client.post("/api/v1/auth/register", json={"email":"preview@example.com","password":"secure-pass-123"}).json()
    path = tmp_path / "brief.html"
    path.write_text("<!doctype html><h1>真实响应类型检查</h1>", encoding="utf-8")
    dependency = app.dependency_overrides[get_db]()
    db = next(dependency)
    try:
        brief = Briefing(user_id=user["id"], title="预览", query="预览", html_path=str(path), event_count=0)
        db.add(brief); db.commit(); identifier = brief.id
    finally:
        dependency.close()
    response = client.get(f"/api/v1/news/briefings/{identifier}/html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text.startswith("<!doctype html>")
    assert "default-src 'none'" in response.headers["content-security-policy"]
