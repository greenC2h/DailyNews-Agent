"""候选归并、逐事件处理与可复用结构化资料的 HTML 生成。"""
from collections.abc import Callable
from datetime import datetime
import json
import logging
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
from deepagents import create_deep_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_openai import ChatOpenAI
from app.config import get_settings
from app.services.news import NewsArticle

logger = logging.getLogger(__name__)
SKILL_PATH = Path(__file__).resolve().parents[2] / "skills" / "news_brief" / "SKILL.md"


class EventSource(BaseModel):
    """事件所引用的原始报道与必要证据，全文不进入持久化资料。"""
    title: str
    source: str
    url: str
    published_at: str
    excerpt: str
    collected_at: str = ""
    language: str = ""
    content_hash: str = ""
    published_date_only: bool = False

    @field_validator("published_at")
    @classmethod
    def parse_publication(cls, value: str) -> str:
        """保证日期字段可以被持久化层解析，保留模型提供的精度表达。"""
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class EventRecord(BaseModel):
    """适合保存与历史检索的事件资料，来源至少包含一个结构化对象。"""
    title: str
    summary: str
    facts: list[str] = Field(default_factory=list)
    sources: list[EventSource] = Field(min_length=1)
    differences: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    event_key: str = ""


class EventBatch(BaseModel):
    """成功事件的结构化集合，用于 HTML 复用与中间资料序列化。"""
    events: list[EventRecord]


class EventCandidate(BaseModel):
    """模型筛选、评分并归并后的事件候选。"""
    title: str
    article_indices: list[int] = Field(min_length=1)
    relevance: float = Field(ge=0, le=1)
    rationale: str = ""


class CandidateBatch(BaseModel):
    """已由模型执行相关性阈值、数量和排序要求的候选集合。"""
    events: list[EventCandidate]


class HtmlDocument(BaseModel):
    """HTML 阶段的完整简报结果。"""
    html: str = Field(min_length=1)


class EventGenerationError(RuntimeError):
    """所有候选事件生成失败，不能记作没有符合条件的新闻。"""


class HtmlGenerationError(RuntimeError):
    """HTML 阶段失败，携带已成功生成的事件以便保存并重试。"""
    def __init__(self, events: list[EventRecord], error_code: str) -> None:
        """保留可复用事件对象与安全错误分类，不保留外部响应。"""
        super().__init__(f"HTML 生成失败：{error_code}")
        self.events = events
        self.error_code = error_code


def build_news_agent(response_schema: type[BaseModel], system_prompt: str):
    """按锁定版本创建结构化新闻 Agent，格式修复由业务层限定一次重试。"""
    settings = get_settings()
    if not settings.model_api_key or not settings.deepseek_base_url or not settings.news_model_name:
        raise RuntimeError("MODEL_API_KEY、DEEPSEEK_BASE_URL 和 NEWS_MODEL_NAME 必须配置")
    # 供应商 thinking 模式拒绝强制 tool_choice；此独立新闻处理实例明确关闭 thinking。
    model = ChatOpenAI(model=settings.news_model_name, api_key=settings.model_api_key, base_url=settings.deepseek_base_url, temperature=0, timeout=90, max_retries=0, extra_body={"thinking": {"type": "disabled"}})
    return create_deep_agent(model=model, system_prompt=system_prompt, response_format=ToolStrategy(response_schema, handle_errors=False), name="dailynews_news_processor")


def _invoke(schema: type[BaseModel], instruction: str, prompt: str) -> BaseModel:
    """最多两次调用并解析结构化结果，第二次附加格式修复指令。"""
    skill = SKILL_PATH.resolve().read_text(encoding="utf-8")
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            agent = build_news_agent(schema, skill + "\n\n" + instruction)
            content = prompt
            if attempt:
                content += "\n上次输出未成功解析或模型请求失败。请重新输出符合响应工具结构的完整资料；只修复本阶段。"
            result = agent.invoke({"messages": [{"role": "user", "content": content}]}, config={"recursion_limit": 12})
            parsed = result.get("structured_response")
            if isinstance(parsed, schema):
                return parsed
            if isinstance(parsed, dict):
                return schema.model_validate(parsed)
            raise ValueError("STRUCTURED_RESPONSE_MISSING")
        except Exception as exc:
            last_error = exc
            logger.warning("news generation schema=%s attempt=%s error=%s", schema.__name__, attempt + 1, type(exc).__name__)
    raise EventGenerationError(f"{schema.__name__}：{type(last_error).__name__}") from None


def _context(query: str, language: str, style: dict, time_window: dict | None) -> str:
    """统一传递主题、输出语言、风格及已固定的新闻时间范围。"""
    return f"主题：{query}\n输出语言：{language}\n风格：{json.dumps(style, ensure_ascii=False)}\n新闻时间范围：{json.dumps(time_window or {}, ensure_ascii=False, default=str)}"


def generate_events(articles: list[NewsArticle], query: str, language: str, style: dict, *, time_window: dict | None = None) -> list[EventRecord]:
    """先识别候选再逐事件生成；单事件重试耗尽后跳过，其余事件继续。

    Returns:
        成功事件资料；模型筛选无候选时返回空列表。

    Raises:
        EventGenerationError: 候选识别失败或所有候选均生成失败。
    """
    if not articles:
        return []
    article_data = [{"index": index, "title": article.title, "url": article.url, "source": article.source, "published_at": article.published_at.isoformat(), "collected_at": article.collected_at.isoformat(), "language": article.language, "content_hash": article.content_hash, "published_date_only": article.published_date_only, "content": article.content} for index, article in enumerate(articles)]
    context = _context(query, language, style, time_window)
    candidates = _invoke(CandidateBatch, "当前为候选识别阶段：根据正文判断相关性、内容价值及来源可信度，按0到1评分，阈值0.6。你负责过滤阈值、同事件归并、事件数量上限及排序；只输出合格候选和对应文章的index，不生成摘要或HTML。默认最多5个事件、最新报道优先；风格中的明确设置优先。", context + "\n文章资料：" + json.dumps(article_data, ensure_ascii=False))
    events: list[EventRecord] = []
    for candidate in candidates.events:
        try:
            # 索引是输入引用协议，越界属于结构解析失败，不对模型分数或排序做二次语义筛选。
            if any(index < 0 or index >= len(article_data) for index in candidate.article_indices):
                raise ValueError("ARTICLE_REFERENCE_INVALID")
            sources = [article_data[index] for index in candidate.article_indices]
            result = _invoke(EventRecord, "当前仅处理一个事件：根据提供正文输出该事件的结构化资料。必须保存关键事实的必要原文片段、来源差异、冲突和不确定性；不得输出HTML或其他事件。来源日期及链接使用输入原始值，全文只用于处理，不复制到摘要或证据。", context + "\n事件候选：" + candidate.model_dump_json() + "\n本事件原文：" + json.dumps(sources, ensure_ascii=False))
            events.append(result)
        except Exception as exc:
            logger.warning("news event skipped error=%s", type(exc).__name__)
    if candidates.events and not events:
        raise EventGenerationError("所有候选事件生成失败")
    return events


def generate_html(events: list[EventRecord], query: str, language: str, style: dict, *, time_window: dict | None = None) -> str:
    """只使用已成功的结构化事件生成 HTML，允许失败重试时跳过采集与事件生成。"""
    if not events:
        return ""
    try:
        document = _invoke(HtmlDocument, "当前为独立HTML阶段：只根据成功事件资料生成完整自包含HTML文档。紧凑排版，避免装饰性长CSS和重复复述；保留全部事件标题、原摘要、来源链接及差异、冲突和不确定性，关键事实已体现在摘要时不重复展开。目标控制在7000字符内。遵从输入语言与风格，只返回html字段。", _context(query, language, style, time_window) + "\n标题：" + query + " 新闻简报\n成功事件资料：" + EventBatch(events=events).model_dump_json())
        if not document.html.strip():
            raise ValueError("HTML_EMPTY")
        return document.html
    except Exception as exc:
        raise HtmlGenerationError(events, type(exc).__name__) from None


def generate_briefing(articles: list[NewsArticle], query: str, language: str, style: dict, *, events: list[EventRecord] | None = None, on_events: Callable[[list[EventRecord]], None] | None = None, time_window: dict | None = None) -> tuple[list[EventRecord], str]:
    """保持 Worker 原有接口，并支持保存成功事件与复用中间资料重试 HTML。

    Args:
        articles: 正文采集结果，复用 events 时可以为空。
        query: 主题。
        language: 简报输出语言。
        style: 任务固定风格快照。
        events: 已保存事件资料；传入时直接进入 HTML 阶段。
        on_events: HTML 前持久化事件资料的回调，异常直接传播。
        time_window: 固定新闻时间窗口和时区，用于 HTML 标题与范围。
    """
    successful = events if events is not None else generate_events(articles, query, language, style, time_window=time_window)
    if not successful:
        return [], ""
    if on_events is not None:
        on_events(successful)
    return successful, generate_html(successful, query, language, style, time_window=time_window)

