"""助手真实图执行、持久补问和业务边界的隔离回归测试。"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.sandbox import BaseSandbox
from fastapi import HTTPException
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
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


def test_search_history_passes_database_dependencies_by_name(monkeypatch, users):
    """历史检索工具须按路由参数名传入会话与用户，避免误当时间范围。"""
    from app.api import history as history_api

    user, _, factory = users
    captured = {}

    def fake_history(query, limit, start=None, end=None, published_start=None,
                     published_end=None, include_terms=(), exclude_terms=(), db=None, user=None):
        """记录历史路由实际收到的依赖和过滤条件。"""
        captured.update(locals())
        return {"query": query, "items": []}

    monkeypatch.setattr(history_api, "history", fake_history)
    tool = next(item for item in runtime.business_tools(user.id, "history") if item.name == "search_history")
    tool.func(query="汽车")

    assert captured["db"].get(User, user.id) is not None
    assert captured["user"].id == user.id
    assert captured["start"] is None and captured["end"] is None
    assert captured["include_terms"] == [] and captured["exclude_terms"] == []
    assert captured["limit"] == 20


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


def test_execute_shows_full_command_and_needs_approval():
    """显式命令先把完整命令交给审批，再执行一次；拒绝时零副作用。"""
    invoked = []
    model = ScriptedModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "execute", "args": {"command": "python report.py --limit 3"}, "id": "exec-1"}]),
        AIMessage(content="命令已执行。"),
        AIMessage(content="", tool_calls=[{"name": "execute", "args": {"command": "python cleanup.py"}, "id": "exec-2"}]),
        AIMessage(content="操作已拒绝。"),
    ])

    @tool
    def execute(command: str) -> str:
        """记录测试沙箱命令执行次数。"""
        invoked.append(command)
        return "done"

    saver, store = InMemorySaver(), InMemoryStore()
    agent = create_deep_agent(model, tools=[execute], backend=StateBackend(), checkpointer=saver, store=store,
                              interrupt_on={"execute": {"allowed_decisions": ["approve", "reject"]}})
    first_config = {"configurable": {"thread_id": "approved"}}
    pending = agent.invoke({"messages": [HumanMessage(content="运行报告")]}, first_config)
    request = pending["__interrupt__"][0].value["action_requests"][0]
    assert request["args"]["command"] == "python report.py --limit 3"
    assert invoked == []
    approved = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), first_config)
    assert invoked == ["python report.py --limit 3"]
    assert approved["messages"][-1].content == "命令已执行。"

    rejected_config = {"configurable": {"thread_id": "rejected"}}
    rejected = agent.invoke({"messages": [HumanMessage(content="清理")]}, rejected_config)
    assert rejected["__interrupt__"][0].value["action_requests"][0]["args"]["command"] == "python cleanup.py"
    result = agent.invoke(Command(resume={"decisions": [{"type": "reject", "message": "不执行"}]}), rejected_config)
    assert invoked == ["python report.py --limit 3"]
    assert result["messages"][-1].content == "操作已拒绝。"


def test_production_agent_routes_sandbox_and_resume(monkeypatch, users):
    """生产图组装使用共享沙箱、要求显式审批并恢复补问及命令结果。"""
    from contextlib import contextmanager
    from types import SimpleNamespace

    user = users[0]
    commands = []
    saver, store = InMemorySaver(), InMemoryStore()
    model = ScriptedModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "ask_user", "args": {"question": "查询哪个日期？"}, "id": "prod-q"}]),
        AIMessage(content="已根据回答准备好了。"),
        AIMessage(content="", tool_calls=[{"name": "execute", "args": {"command": "python3 --version"}, "id": "prod-exec"}]),
        AIMessage(content="命令结果已返回。"),
    ])

    class FakeSharedSandbox(BaseSandbox):
        """隔离测试用远端沙箱代替，不提供宿主回退。"""

        @property
        def id(self):
            """返回测试沙箱标识。"""
            return "shared-test"

        def execute(self, command, *, timeout=None):
            """记录获批后才抵达沙箱的显式命令。"""
            commands.append(command)
            return ExecuteResponse(output="Python 3.12", exit_code=0)

        def upload_files(self, files):
            """报告测试上传结果。"""
            return [FileUploadResponse(path=path) for path, _ in files]

        def download_files(self, paths):
            """报告测试下载结果。"""
            return [FileDownloadResponse(path=path, content=b"") for path in paths]

    @contextmanager
    def persistence(thread_key):
        """提供跨调用的测试检查点。"""
        yield saver, store

    import app.services.sandbox as sandbox

    monkeypatch.setattr(runtime, "_persistence", persistence)
    monkeypatch.setattr(runtime, "ChatOpenAI", lambda **kwargs: model)
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(
        model_api_key="test-key", model_name="assistant-test", deepseek_base_url=None,
        model_request_timeout_seconds=60,
    ))
    monkeypatch.setattr(sandbox, "SharedSandboxBackend", FakeSharedSandbox)

    question = runtime.assistant_turn("帮我准备简报", user, "production")
    assert question["interrupts"][0]["value"]["question"] == "查询哪个日期？"
    answered = runtime.assistant_turn("", user, "production", resume="昨天")
    assert answered["text"] == "已根据回答准备好了。"
    approval = runtime.assistant_turn("检查 Python 版本", user, "production")
    request = approval["interrupts"][0]["value"]["action_requests"][0]
    assert request["args"]["command"] == "python3 --version"
    assert commands == []
    done = runtime.assistant_turn("", user, "production", resume={"decisions": [{"type": "approve"}]})
    assert commands == ["python3 --version"]
    assert done["text"] == "命令结果已返回。"


def test_pending_interrupts_can_be_reloaded(monkeypatch, users):
    """页面刷新后仍能从持久检查点读回同一用户的待补问。"""
    user = users[0]
    scripted_runtime(monkeypatch, [
        AIMessage(content="", tool_calls=[{"name": "ask_user", "args": {"question": "需要哪个范围？"}, "id": "reload-q"}]),
    ])
    runtime.assistant_turn("生成简报", user, "reload-thread")
    pending = runtime.pending_interrupts(user, "reload-thread")
    assert len(pending) == 1
    assert pending[0]["value"] == {"type": "question", "question": "需要哪个范围？", "options": []}


def test_shared_sandbox_is_lazy(monkeypatch):
    """普通图构建仅保存固定实例信息，首次命令才连接远端沙箱。"""
    from types import SimpleNamespace
    from app.services import sandbox

    connections = []

    class Remote:
        """提供最小命令接口的远端实例替身。"""

        def __init__(self):
            """初始化可观察命令服务。"""
            self.commands = SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(exit_code=0))

        def close(self):
            """模拟关闭连接。"""

    monkeypatch.setattr(sandbox, "get_settings", lambda: SimpleNamespace(
        open_sandbox_id="shared-id", open_sandbox_base_url="http://sandbox.local",
        open_sandbox_api_key="test", open_sandbox_command_timeout=10,
        open_sandbox_connect_timeout_seconds=10,
    ))
    monkeypatch.setattr(sandbox.SandboxSync, "connect", lambda *args, **kwargs: connections.append(Remote()) or connections[-1])
    backend = sandbox.SharedSandboxBackend()
    assert backend.id == "shared-id" and connections == []
    assert backend.execute("printf ok").exit_code == 0
    assert len(connections) == 1


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
