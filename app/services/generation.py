"""新闻处理子 Agent 的分阶段生成流程。"""
import json
from pathlib import Path
from pydantic import BaseModel, Field
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
from app.config import get_settings
from app.services.news import NewsArticle

class EventSource(BaseModel):
    """事件所引用的原始报道。"""
    title: str
    source: str
    url: str
    published_at: str
    excerpt: str

class EventRecord(BaseModel):
    """归并后的结构化新闻事件。"""
    title: str
    summary: str
    facts: list[str] = Field(default_factory=list)
    sources: list[EventSource]

class EventBatch(BaseModel):
    """事件资料阶段的完整结果。"""
    events: list[EventRecord]

class HtmlDocument(BaseModel):
    """HTML 阶段的完整简报结果。"""
    html: str

def build_news_agent(response_schema: type[BaseModel], system_prompt: str):
    """按项目锁定版本创建结构化新闻处理子 Agent。"""
    settings = get_settings()
    if not settings.model_api_key or not settings.deepseek_base_url or not settings.news_model_name:
        raise RuntimeError("MODEL_API_KEY、DEEPSEEK_BASE_URL 和 NEWS_MODEL_NAME 必须配置")
    # DeepSeek thinking mode currently rejects forced tool_choice used by LangChain structured output.
    model = ChatOpenAI(model=settings.news_model_name, api_key=settings.model_api_key, base_url=settings.deepseek_base_url, temperature=0, extra_body={"thinking": {"type": "disabled"}})
    return create_deep_agent(model=model, system_prompt=system_prompt, response_format=response_schema, name="dailynews_news_processor")

def generate_briefing(articles: list[NewsArticle], query: str, language: str, style: dict) -> tuple[list[EventRecord], str]:
    """先生成结构化事件，再独立生成完整 HTML。"""
    skill = Path("skills/news_brief/SKILL.md").read_text(encoding="utf-8")
    article_data = [{"title": a.title, "url": a.url, "source": a.source, "published_at": a.published_at.isoformat(), "content": a.content} for a in articles]
    prompt = f"主题：{query}\n输出语言：{language}\n风格：{json.dumps(style, ensure_ascii=False)}\n文章资料：{json.dumps(article_data, ensure_ascii=False)}"
    event_agent = build_news_agent(EventBatch, skill + "\n\n事件阶段只返回结构化事件资料，必须逐项保留来源。")
    event_result = event_agent.invoke({"messages": [{"role": "user", "content": prompt}]})
    batch = event_result.get("structured_response")
    if not isinstance(batch, EventBatch): raise ValueError("事件结构化输出无法解析")
    if not batch.events: return [], ""
    html_agent = build_news_agent(HtmlDocument, skill + "\n\nHTML 阶段只根据提供的成功事件资料生成完整 HTML，原样提供的事件语义不得丢失。")
    html_result = html_agent.invoke({"messages": [{"role": "user", "content": f"标题：{query} 新闻简报\n成功事件资料：{batch.model_dump_json()}"}]})
    document = html_result.get("structured_response")
    if not isinstance(document, HtmlDocument) or not document.html.strip(): raise ValueError("HTML 结构化输出无法解析")
    return batch.events, document.html
