"""以 PostgreSQL 保存会话、补问和审批的主助手运行时。"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StoreBackend
from deepagents.profiles import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore
from langgraph.types import Command, interrupt
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db.database import SessionLocal
from app.db.models import User


def _tool_result(kind: str, data: dict | list) -> tuple[str, dict]:
    """将业务结果同时保存为模型可读文字和可恢复的界面资源。"""
    artifact = {"type": kind, "data": jsonable_encoder(data)}
    return json.dumps(artifact, ensure_ascii=False), artifact


def _operation_key(thread_key: str, tool_call_id: str) -> str:
    """由持久化工具调用编号生成稳定幂等键，隔离用户和线程。"""
    return "assistant:" + hashlib.sha256(f"{thread_key}:{tool_call_id}".encode()).hexdigest()


def _owned_user(db, user_id: str) -> User:
    """在业务事务内重新加载已认证用户，避免复用请求会话对象。"""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(401, detail={"code": "AUTH_REQUIRED", "message": "用户不存在"})
    return user


def business_tools(user_id: str, thread_key: str) -> list:
    """构造只访问当前用户的业务工具，复用 API 校验和事务规则。"""
    from app.api.auth import patch_settings, user_payload
    from app.api.history import history
    from app.api.news import BriefingRequest, create_briefing
    from app.api.tasks import TaskRequest, create_task, list_tasks, transition
    from app.services.security import SettingsPatch

    @tool(response_format="content_and_artifact")
    def publish_briefing(request: BriefingRequest, runtime: ToolRuntime) -> tuple[str, dict]:
        """发布即时简报任务；必须明确主题、条数、带时区的新闻起止时间，邮件仅发给当前账号。"""
        with SessionLocal() as db:
            result = create_briefing(request, db, _owned_user(db, user_id), idempotency_key=_operation_key(thread_key, runtime.tool_call_id))
            return _tool_result("generation_task", result)

    @tool(response_format="content_and_artifact")
    def schedule_briefing(request: TaskRequest, runtime: ToolRuntime) -> tuple[str, dict]:
        """创建 daily/once 定时任务；缺少执行时间、IANA 时区或一次性新闻范围时先补问。"""
        with SessionLocal() as db:
            result = create_task(request, db, _owned_user(db, user_id), idempotency_key=_operation_key(thread_key, runtime.tool_call_id))
            return _tool_result("scheduled_task", result)

    @tool(response_format="content_and_artifact")
    def read_settings() -> tuple[str, dict]:
        """读取当前账号偏好和 settings_version，修改设置前先读取版本。"""
        with SessionLocal() as db:
            return _tool_result("settings", user_payload(_owned_user(db, user_id)))

    @tool(response_format="content_and_artifact")
    def change_settings(patch: SettingsPatch) -> tuple[str, dict]:
        """按明确意图更新偏好；version 必须来自 read_settings，未出现的字段保留，daily_push_time=null 清空。"""
        if patch.version is None:
            raise ValueError("修改设置前必须读取当前版本，并在 version 中提交")
        with SessionLocal() as db:
            return _tool_result("settings", patch_settings(patch, db, _owned_user(db, user_id)))

    @tool(response_format="content_and_artifact")
    def scheduled_briefings() -> tuple[str, dict]:
        """列出当前用户的定时任务及允许操作。"""
        with SessionLocal() as db:
            return _tool_result("scheduled_tasks", list_tasks(db, _owned_user(db, user_id)))

    @tool(response_format="content_and_artifact")
    def manage_scheduled_briefing(task_id: str, action: Literal["pause", "resume", "cancel"]) -> tuple[str, dict]:
        """暂停、恢复或取消当前账号任务；默认每日任务不能取消，必须尊重接口返回的允许操作。"""
        with SessionLocal() as db:
            result = transition(task_id, action, db, _owned_user(db, user_id))
            return _tool_result("scheduled_task", result)

    @tool(response_format="content_and_artifact")
    def search_history(query: str, limit: int = 20) -> tuple[str, dict]:
        """检索当前账号历史新闻；query 长度 2 至 200，limit 为 1 至 20。"""
        if not 2 <= len(query) <= 200 or not 1 <= limit <= 20:
            raise ValueError("历史检索关键词长度须为 2 至 200，条数须为 1 至 20")
        with SessionLocal() as db:
            return _tool_result("history", history(query, limit, db, _owned_user(db, user_id)))

    @tool
    def ask_user(question: str, options: list[str] | None = None) -> str:
        """缺少必要信息时暂停并向用户补问；恢复后返回用户实际答复，禁止自行填造。"""
        answer = interrupt({"type": "question", "question": question, "options": options or []})
        return json.dumps(answer, ensure_ascii=False)

    return [publish_briefing, schedule_briefing, read_settings, change_settings, scheduled_briefings, manage_scheduled_briefing, search_history, ask_user]


def _lock_key(value: str) -> int:
    """生成 PostgreSQL advisory lock 使用的有符号 64 位稳定键。"""
    return int.from_bytes(hashlib.blake2b(value.encode(), digest_size=8).digest(), "big", signed=True)


@contextmanager
def _persistence(thread_key: str):
    """持有线程级会话锁，关闭连接时自动释放，初始化数据库结构时另行串行。"""
    settings = get_settings()
    if not settings.database_url or make_url(settings.database_url).get_backend_name() != "postgresql":
        raise HTTPException(503, detail={"code": "PERSISTENCE_REQUIRED", "message": "助手需要 PostgreSQL 持久化配置"})
    dsn = make_url(settings.database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    with PostgresSaver.from_conn_string(dsn) as checkpointer, PostgresStore.from_conn_string(dsn) as store:
        connection = checkpointer.conn
        acquired = connection.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (_lock_key(thread_key),)).fetchone()["acquired"]
        if not acquired:
            raise HTTPException(409, detail={"code": "THREAD_BUSY", "message": "该对话正在处理，请稍后重试"})
        try:
            # 两种持久化组件共享数据库 schema，避免并发首启迁移相互竞争。
            connection.execute("SELECT pg_advisory_lock(%s)", (_lock_key("dailynews:assistant:schema"),))
            try:
                checkpointer.setup()
                store.setup()
            finally:
                connection.execute("SELECT pg_advisory_unlock(%s)", (_lock_key("dailynews:assistant:schema"),))
            yield checkpointer, store
        finally:
            connection.execute("SELECT pg_advisory_unlock(%s)", (_lock_key(thread_key),))


def _build_agent(user: User, thread_key: str, checkpointer, store):
    """组装固定模型、用户记忆与共享沙箱，只为显式 execute 设置持久审批。"""
    from app.services.sandbox import SharedSandboxBackend

    settings = get_settings()
    if not settings.model_api_key or not settings.model_name:
        raise HTTPException(503, detail={"code": "MODEL_NOT_CONFIGURED", "message": "请配置 MODEL_API_KEY 和 MODEL_NAME"})
    register_harness_profile(
        f"openai:{settings.model_name}",
        HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
    )
    model = ChatOpenAI(model=settings.model_name, api_key=settings.model_api_key, base_url=settings.deepseek_base_url, temperature=0, extra_body={"thinking": {"type": "disabled"}})
    def private_namespace(runtime) -> tuple[str, str]:
        """将框架归档及工具结果限定在已认证账号命名空间。"""
        return "dailynews", user.id

    backend = CompositeBackend(
        default=SharedSandboxBackend(),
        routes={"/private/": StoreBackend(namespace=private_namespace, store=store)},
        artifacts_root="/private",
    )
    prompt = (
        "你是 DailyNews 新闻助手，使用中文沟通。仅处理当前账号的数据，收件人固定为注册邮箱。"
        "立即生成需要明确主题、事件条数、新闻开始和结束时间；缺项先用 ask_user 补问。"
        "定时任务仅支持 daily/once，必须明确执行时间和 IANA 时区，一次性还需明确新闻范围。"
        "持久偏好只在用户明确要求保存时修改，否则放在本次 request.overrides。修改偏好前读取版本。"
        "业务工具已发布异步任务不代表生成或发信成功，应返回任务标识与真实状态。"
        "新闻处理由发布任务交给固定新闻流程执行。仅显式 execute 需要用户审批；审批拒绝后不得换命令绕过。"
        "文件工具在共享沙箱内运行，不存在本机命令回退。框架归档写入当前账号私有 Store。"
        f"当前 UTC 时间：{datetime.now(timezone.utc).isoformat()}，账号时区：{user.timezone}。"
    )
    return create_deep_agent(
        model, tools=business_tools(user.id, thread_key), system_prompt=prompt,
        backend=backend, checkpointer=checkpointer, store=store,
        interrupt_on={"execute": {"allowed_decisions": ["approve", "reject"]}},
    )


def _result_payload(result: dict, previous_ids: set[str]) -> dict:
    """提取本次助手回复、持久中断和业务资源，不泄露推理或历史工具结果。"""
    messages = [message for message in result.get("messages", []) if message.id not in previous_ids]
    replies = [message.text for message in messages if isinstance(message, AIMessage) and not message.tool_calls]
    resources = [message.artifact for message in messages if isinstance(message, ToolMessage) and message.artifact]
    interrupts = [{"id": item.id, "value": jsonable_encoder(item.value)} for item in result.get("__interrupt__", [])]
    return {"text": "\n".join(replies), "interrupts": interrupts, "resources": resources}


def assistant_turn(message: str, user: User, thread_id: str, resume=None) -> dict:
    """执行或恢复当前用户线程，确保待审批状态不会被普通新消息覆盖。

    Args:
        message: 本轮用户文字；恢复中断时可为空。
        user: 已通过会话认证的用户。
        thread_id: 当前账号内的线程编号。
        resume: 补问答复，或 execute 审批的 decisions 对象。

    Returns:
        本轮文字、带 id/value 的中断列表和业务资源。

    Raises:
        HTTPException: 配置缺失、线程忙碌或中断恢复状态不匹配。
    """
    if not thread_id or len(thread_id) > 200:
        raise HTTPException(422, detail={"code": "INVALID_THREAD", "message": "对话编号长度须为 1 至 200"})
    thread_key = f"{user.id}::{thread_id}"
    config = {"configurable": {"thread_id": thread_key}, "recursion_limit": 50}
    with _persistence(thread_key) as (checkpointer, store):
        agent = _build_agent(user, thread_key, checkpointer, store)
        state = agent.get_state(config)
        pending = bool(state.interrupts)
        if pending and resume is None:
            raise HTTPException(409, detail={"code": "RESUME_REQUIRED", "message": "请先回答补问或处理命令审批", "interrupts": [{"id": item.id, "value": jsonable_encoder(item.value)} for item in state.interrupts]})
        if resume is not None and not pending:
            raise HTTPException(409, detail={"code": "NO_PENDING_INTERRUPT", "message": "该对话没有待处理的补问或审批"})
        previous_ids = {item.id for item in state.values.get("messages", [])}
        graph_input = Command(resume=resume) if resume is not None else {"messages": [{"role": "user", "content": message}]}
        return _result_payload(agent.invoke(graph_input, config), previous_ids)
