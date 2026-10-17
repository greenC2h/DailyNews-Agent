"""为 API 测试提供独立临时数据库和文件目录。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.config import get_settings
from app.db.database import Base, get_db
from app.db import models, news_models
from app.main import app

@pytest.fixture
def client(tmp_path, monkeypatch):
    """运行每个测试时创建隔离 SQLite 数据库。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        """提供仅属于本测试的数据库会话。"""
        db = factory()
        try: yield db
        finally: db.close()

    app.dependency_overrides[get_db] = override_get_db
    settings = get_settings(); monkeypatch.setattr(settings, "html_root", tmp_path / "html")
    with TestClient(app) as test_client: yield test_client
    app.dependency_overrides.clear(); engine.dispose()
