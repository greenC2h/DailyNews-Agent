"""助手真实图执行、持久补问和业务边界的隔离回归测试。"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from fastapi import HTTPException
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import User
from app.db.news_models import GenerationTask, ScheduledTask
from app.services import assistant_runtime as runtime


class ScriptedModel(FakeMessagesListChatModel):
    """提供确定性模型消息，同时保留真实工具节点与中断机制。"""

    def bind_tools(self, tools, **kwargs):
        """接受图绑定工具，测试消息已预先给出完整工具调用。"""
        return self


@pytest.fixture
def users(tmp_path, monkeypatch):
    """创建两个隔离测试账号，并让业务工具仅连接临时 SQLite。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'assistant.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(runtime, "SessionLocal", factory)
    with factory() as db:
        first = User(email="first@example.test", password_hash="unused", nickname="甲")
        second = User(email="second@example.test", password_hash="unused", nickname="乙")
        db.add_all([first, second])
        db.commit()
    yield first, second, factory
    engine.dispose()


def scripted_runtime(monkeypatch, responses):
    """替换外部连接与模型，但使用真实 DeepAgent 编译、检查点及工具执行。"""
    saver, store = InMemorySaver(), InMemoryStore()
    model = ScriptedModel(responses=responses)

    @contextmanager
    def persistence(thread_key):
        """跨请求保留内存检查点，用于确定性测试恢复过程。"""
        yield saver, store

    def build(user, thread_key, checkpointer, store):
        """构建带业务工具的实际图，不访问远程模型或共享沙箱。"""
        return create_deep_agent(model, tools=runtime.business_tools(user.id, thread_key), backend=StateBackend(), checkpointer=checkpointer, store=store)

    monkeypatch.setattr(runtime, "_persistence", persistence)
    monkeypatch.setattr(runtime, "_build_agent", build)
    return saver


def test_question_persists_and_resumes(monkeypatch, users):
    """工具补问在下一次图重建后恢复，普通新消息不能覆盖待处理问题。"""
    user = users[0]
    saver = scripted_runtime(monkeypatch, [
        AIMessage(content="", tool_calls=[{"name": "ask_user", "args": {"question": "需要几条新闻？", "options": ["5", "10"]}, "id": "question-1"}]),
        AIMessage(content="已确认五条。"),
    ])
    result = runtime.assistant_turn("新闻", user, "same-thread")
    assert result["interrupts"][0]["value"]["question"] == "需要几条新闻？"
    with pytest.raises(HTTPException) as pending:
        runtime.assistant_turn("跳过问题", user, "same-thread")
    assert pending.value.detail["code"] == "RESUME_REQUIRED"
    result = runtime.assistant_turn("", user, "same-thread", resume="5")
    assert result["text"] == "已确认五条。" and result["interrupts"] == []
    graph = runtime._build_agent(user, f"{user.id}::same-thread", saver, InMemoryStore())
    state = graph.get_state({"configurable": {"thread_id": f"{user.id}::same-thread"}})
    assert any(isinstance(item, ToolMessage) and item.content == '"5"' for item in state.values["messages"])
    with pytest.raises(HTTPException) as duplicate:
        runtime.assistant_turn("", user, "same-thread", resume="5")
    assert duplicate.value.detail["code"] == "NO_PENDING_INTERRUPT"


def test_checkpoint_isolated_by_user(monkeypatch, users):
    """两个用户使用相同前端线程编号仍保存到独立检查点。"""
    saver = scripted_runtime(monkeypatch, [AIMessage(content="回复甲"), AIMessage(content="回复乙")])
    runtime.assistant_turn("甲的私有问题", users[0], "shared")
    runtime.assistant_turn("乙的私有问题", users[1], "shared")
    for user, expected, excluded in [(users[0], "甲的私有问题", "乙的私有问题"), (users[1], "乙的私有问题", "甲的私有问题")]:
        graph = runtime._build_agent(user, f"{user.id}::shared", saver, InMemoryStore())
        state = graph.get_state({"configurable": {"thread_id": f"{user.id}::shared"}})
        content = [item.content for item in state.values["messages"] if isinstance(item, HumanMessage)]
        assert expected in content and excluded not in content


def test_tools_return_persisted_resources(monkeypatch, users):
    """业务工具资源附在真实 ToolMessage，响应只提取本轮新增资料。"""
    scripted_runtime(monkeypatch, [
        AIMessage(content="", tool_calls=[{"name": "read_settings", "args": {}, "id": "settings-1"}]),
        AIMessage(content="已读取设置"), AIMessage(content="后续回复"),
    ])
    first = runtime.assistant_turn("读取设置", users[0], "resources")
    assert first["resources"][0]["data"]["email"] == users[0].email
    assert first["resources"][0]["type"] == "settings"
    second = runtime.assistant_turn("继续", users[0], "resources")
    assert second["resources"] == [] and second["text"] == "后续回复"


def test_publish_and_schedule_replay_are_idempotent(users):
    """已持久化工具调用重新执行不会重复发布即时或定时任务。"""
    from app.api.news import BriefingRequest
    from app.api.tasks import TaskRequest

    user, _, factory = users
    tools = {item.name: item for item in runtime.business_tools(user.id, f"{user.id}::replay")}
    now = datetime.now(timezone.utc)
    request = BriefingRequest(query="人工智能", limit=5, start=now - timedelta(days=1), end=now)
    call = SimpleNamespace(tool_call_id="publish-1")
    first = tools["publish_briefing"].func(request=request, runtime=call)
    second = tools["publish_briefing"].func(request=request, runtime=call)
    assert first == second
    scheduled = TaskRequest(frequency="daily", run_at=now + timedelta(hours=1), timezone="Asia/Shanghai", query="人工智能")
    call = SimpleNamespace(tool_call_id="schedule-1")
    assert tools["schedule_briefing"].func(request=scheduled, runtime=call) == tools["schedule_briefing"].func(request=scheduled, runtime=call)
    with factory() as db:
        assert len(db.scalars(select(GenerationTask)).all()) == 1
        assert len(db.scalars(select(ScheduledTask)).all()) == 1


def test_settings_tool_requires_version_and_keeps_ownership(users):
    """助手设置工具拒绝缺少版本，且只更新闭包绑定的当前账号。"""
    from app.services.security import SettingsPatch

    first, second, factory = users
    tool = next(item for item in runtime.business_tools(first.id, "settings") if item.name == "change_settings")
    with pytest.raises(ValueError, match="version"):
        tool.func(SettingsPatch(nickname="更新"))
    tool.func(SettingsPatch(nickname="更新", version=1))
    with factory() as db:
        assert db.get(User, first.id).nickname == "更新"
        assert db.get(User, second.id).nickname == "乙"


def test_invalid_thread_is_rejected(users):
    """无效线程在访问远程资源前被拒绝。"""
    with pytest.raises(HTTPException) as failure:
        runtime.assistant_turn("消息", users[0], "")
    assert failure.value.status_code == 422


@pytest.mark.skipif(os.getenv("RUN_ASSISTANT_POSTGRES_TESTS") != "1", reason="显式启用后使用独立 PostgreSQL schema 验证")
def test_real_postgres_checkpoint_store_and_thread_lock(monkeypatch, users):
    """独立 schema 真实验证连接重建后的补问恢复、持久记忆与并发线程锁。"""
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    settings = runtime.get_settings()
    original_url = make_url(settings.database_url)
    schema = "test_assistant_" + uuid4().hex
    admin_dsn = original_url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            isolated_url = original_url.update_query_dict({"options": f"-csearch_path={schema}"})
            monkeypatch.setattr(settings, "database_url", isolated_url.render_as_string(hide_password=False))
            user = users[0]
            thread_key = f"{user.id}::pg-test"
            config = {"configurable": {"thread_id": thread_key}}
            model = ScriptedModel(responses=[
                AIMessage(content="", tool_calls=[{"name": "ask_user", "args": {"question": "请选择新闻范围"}, "id": "pg-question"}]),
                AIMessage(content="已恢复持久化补问"),
            ])
            with runtime._persistence(thread_key) as (saver, store):
                graph = create_deep_agent(model, tools=runtime.business_tools(user.id, thread_key), backend=StateBackend(), checkpointer=saver, store=store)
                first = graph.invoke({"messages": [HumanMessage(content="测试新闻")]}, config)
                assert first["__interrupt__"][0].value["question"] == "请选择新闻范围"
                store.put(("dailynews", user.id), "preference", {"topic": "科技"})
                with pytest.raises(HTTPException) as locked:
                    with runtime._persistence(thread_key):
                        pytest.fail("同一线程不能同时获得锁")
                assert locked.value.detail["code"] == "THREAD_BUSY"
            with runtime._persistence(thread_key) as (saver, store):
                from langgraph.types import Command

                graph = create_deep_agent(model, tools=runtime.business_tools(user.id, thread_key), backend=StateBackend(), checkpointer=saver, store=store)
                assert graph.get_state(config).interrupts
                result = graph.invoke(Command(resume="昨天"), config)
                assert result["messages"][-1].content == "已恢复持久化补问"
                assert store.get(("dailynews", user.id), "preference").value == {"topic": "科技"}
                assert store.get(("dailynews", users[1].id), "preference") is None
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
