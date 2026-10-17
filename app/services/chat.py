"""主 Agent 对话服务。"""
from collections.abc import Iterator
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from app.config import get_settings
from app.db.models import User

def stream_reply(message: str, user: User) -> Iterator[dict]:
    """根据用户偏好流式生成普通回答，并提示可使用的任务接口。"""
    settings = get_settings()
    if not settings.model_api_key or not settings.deepseek_base_url or not settings.model_name: raise RuntimeError("模型配置缺失")
    model = ChatOpenAI(model=settings.model_name, api_key=settings.model_api_key, base_url=settings.deepseek_base_url, temperature=0, streaming=True, extra_body={"thinking":{"type":"disabled"}})
    prompt = f"用户昵称：{user.nickname}\n长期偏好：话题={user.topics}，关键词={user.keywords}，语言={user.language}，风格={user.style}\n用户消息：{message}"
    for chunk in model.stream([SystemMessage(content="你是 DailyNews 主助手。识别新闻简报、定时任务、偏好修改和历史查询意图。缺少即时新闻时间范围时明确追问；不要声称后台任务已完成。当前版本可通过任务 API 创建简报。"), HumanMessage(content=prompt)]):
        text = chunk.content if isinstance(chunk.content, str) else "".join(part.get("text", "") for part in chunk.content if isinstance(part, dict))
        if text: yield {"type":"delta","text":text}
