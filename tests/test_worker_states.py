"""Worker 提前结束路径与提交前取消的回归测试。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app import worker
from app.db.database import Base
from app.db.models import User
from app.db.news_models import Briefing, GenerationTask, TaskExecution


@pytest.mark.parametrize("cancel_during_model", [False, True])
def test_early_exit_finishes_execution(tmp_path, monkeypatch, cancel_during_model):
    """无内容和生成期间取消均结束执行记录，取消后不发布简报。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    with factory() as db:
        user = User(email="worker@example.com", password_hash="unused")
        db.add(user); db.flush()
        end = datetime.now(timezone.utc)
        task = GenerationTask(user_id=user.id, query="测试", window_start=end-timedelta(days=1), window_end=end)
        db.add(task); db.commit(); identifier = task.id

    def fake_generate(*args, **kwargs):
        """在模型结果返回前提交取消标记，模拟真实独立请求。"""
        with factory() as db:
            task = db.get(GenerationTask, identifier)
            task.cancel_requested = True
            db.commit()
        return [SimpleNamespace()], "<html>迟到结果</html>"

    monkeypatch.setattr(worker, "fetch_articles", lambda *args, **kwargs: [object()] if cancel_during_model else [])
    monkeypatch.setattr(worker, "generate_briefing", fake_generate)
    worker.process_task(identifier)
    with factory() as db:
        task = db.get(GenerationTask, identifier)
        execution = db.scalar(select(TaskExecution).where(TaskExecution.generation_task_id == identifier))
        assert task.status == ("cancelled" if cancel_during_model else "no_events")
        assert execution.status == task.status
        assert execution.finished_at is not None
        assert db.scalar(select(Briefing.id)) is None
    engine.dispose()
