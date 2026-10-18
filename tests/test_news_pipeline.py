"""新闻采集、事件隔离及 HTML 中间资料复用的回归测试。"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import httpx
import pytest
from app.services import news, generation

START = datetime(2026, 10, 16, tzinfo=timezone.utc)
END = datetime(2026, 10, 19, tzinfo=timezone.utc)
CHINESE = "人工智能研究团队发布新的开源语言模型，介绍模型训练过程、推理能力与评估结果。该项目提供技术报告、实验数据和完整的部署方式，研究人员可以检查并复现实验。研发团队介绍，系统将首先开放给合作实验室开展评测，重点观察长文本处理、复杂推理和工具调用的表现。报告分别列举了不同计算条件下的性能、延迟以及数据处理方法，并说明当前评估仅适用于已公开的测试集。机构同时发布可下载的模型权重和配套使用文档，研究者能够在本地环境完成基础部署。项目负责人表示，后续会根据社区反馈完善技术说明，公开目前尚未覆盖的安全评估结果，相关数据以正式报告为准。"
ENGLISH = "The research team released an open language model and the technical report with results of the evaluation. The article explains the training process and provides details for researchers to review the findings. "


def _html(content: str = CHINESE, date: str = "2026-10-17T12:00:00+08:00") -> str:
    """构造包含真实正文与明确原文日期的测试新闻页面。"""
    meta = f'<meta property="article:published_time" content="{date}">' if date else ""
    return '<html><head><title>人工智能模型发布</title>' + meta + '</head><body><article><h1>模型发布</h1>' + ''.join(f'<p>{content}</p>' for _ in range(8)) + '</article></body></html>'


def _rss(links: list[str]) -> str:
    """构造 RSS 日期与正文可独立变化的候选数据。"""
    return '<?xml version="1.0"?><rss version="2.0"><channel><title>News</title>' + ''.join(f'<item><title>AI {index}</title><link>{link.replace("&", "&amp;")}</link><pubDate>Fri, 17 Oct 2025 12:00:00 GMT</pubDate><description>足够长的RSS摘要{CHINESE * 5}</description><source>Test Source</source></item>' for index, link in enumerate(links)) + '</channel></rss>'


@pytest.fixture
def collection_mock(monkeypatch):
    """使用真实 HTTPX transport 与正文提取器，只替换网络边界。"""
    original = httpx.AsyncClient
    state = SimpleNamespace(calls=[], pages={}, rss=_rss(["https://example.com/article"]), rss_failures=0, decode_calls=0, decode_success=True)

    def handler(request):
        """按 URL 返回模拟新闻并记录请求次数。"""
        url = str(request.url)
        state.calls.append(url)
        if url.startswith("https://news.google.com/rss/search"):
            if state.rss_failures:
                state.rss_failures -= 1
                return httpx.Response(503)
            return httpx.Response(200, text=state.rss)
        value = state.pages.get(url, _html())
        return httpx.Response(value if isinstance(value, int) else 200, text="" if isinstance(value, int) else value)

    def client_factory(**kwargs):
        """保留生产 HTTPX 参数，并将请求路由到测试 transport。"""
        return original(transport=httpx.MockTransport(handler), **kwargs)

    class Decoder:
        """隔离 Google 链接解析协议请求。"""
        def __init__(self, **kwargs):
            """接受生产配置以保持构建接口一致。"""

        async def __aenter__(self):
            """返回可用的解码上下文。"""
            return self

        async def __aexit__(self, *args):
            """结束无副作用测试上下文。"""

        async def decode_google_news_url(self, url):
            """记录解码次数并返回当前测试期望结果。"""
            state.decode_calls += 1
            return {"success": state.decode_success, "decoded_url": "https://example.com/article"}

    monkeypatch.setattr(news.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(news, "GoogleDecoderAsync", Decoder)
    monkeypatch.setattr(news.get_settings(), "news_retry_backoff_seconds", 0)
    return state


def test_original_body_date_and_decoded_url(collection_mock):
    """正文日期优先于过期 RSS 日期，最终证据链接必须是发布站点。"""
    collection_mock.rss = _rss(["https://news.google.com/rss/articles/encoded"])
    rows = news.fetch_articles("AI", START, END)
    assert len(rows) == 1
    assert rows[0].url == "https://example.com/article"
    assert rows[0].published_at == datetime(2026, 10, 17, 4, tzinfo=timezone.utc)
    assert rows[0].content_hash
    assert collection_mock.decode_calls == 1


def test_rss_transient_retry_and_malformed_failure(collection_mock):
    """临时请求失败有限重试，坏 XML 作为采集失败而非无新闻。"""
    collection_mock.rss_failures = 1
    assert len(news.fetch_articles("AI", START, END)) == 1
    assert sum("rss/search" in url for url in collection_mock.calls) == 2
    collection_mock.rss = "<rss><not-valid"
    with pytest.raises(news.CollectionError, match="rss_parse"):
        news.fetch_articles("AI", START, END)


def test_all_body_failures_never_use_rss_summary(collection_mock):
    """正文不可用时不能把很长的 RSS 标题摘要伪装为新闻。"""
    collection_mock.pages["https://example.com/article"] = "<html><body><h1>模型发布</h1></body></html>"
    with pytest.raises(news.CollectionError) as caught:
        news.fetch_articles("AI", START, END)
    assert caught.value.issues[0].stage == "body_extract"
    assert collection_mock.calls.count("https://example.com/article") == 2


def test_permanent_body_error_is_not_retried(collection_mock):
    """永久 HTTP 错误不消耗无意义的第二次请求。"""
    collection_mock.pages["https://example.com/article"] = 404
    with pytest.raises(news.CollectionError):
        news.fetch_articles("AI", START, END)
    assert collection_mock.calls.count("https://example.com/article") == 1


def test_partial_failure_and_url_content_dedup(collection_mock):
    """部分失败仍保留其他正文，相同 URL 和正文明确去重。"""
    collection_mock.rss = _rss(["https://example.com/fail", "https://example.com/article?utm_source=x", "https://example.com/article", "https://example.com/copy"])
    collection_mock.pages["https://example.com/fail"] = 503
    rows = news.fetch_articles("AI", START, END)
    assert len(rows) == 1
    assert rows.issues[0].stage == "body_request"
    assert collection_mock.calls.count("https://example.com/article") == 1


def test_original_date_required_and_date_modified_rejected(collection_mock):
    """没有原文发布时间时跳过，dateModified 与 RSS 日期不构成兜底。"""
    collection_mock.pages["https://example.com/article"] = _html(date="").replace("</head>", '<script type="application/ld+json">{"dateModified":"2026-10-17"}</script></head>')
    rows = news.fetch_articles("AI", START, END)
    assert rows == []
    assert rows.issues[0].error == "ORIGINAL_PUBLICATION_MISSING"


def test_language_and_explicit_conditions(collection_mock):
    """按实际正文语言和明确条件过滤，英文查询配置同时传给 RSS。"""
    collection_mock.pages["https://example.com/article"] = _html(ENGLISH)
    assert news.fetch_articles("AI", START, END) == []
    assert len(news.fetch_articles("AI", START, END, language="en", include_terms=["technical report"])) == 1
    assert "hl=en-US" in collection_mock.calls[-2]
    assert news.fetch_articles("AI", START, END, language="en", exclude_terms=["technical report"]) == []


def test_date_only_window_uses_task_timezone():
    """日期精度以任务本地自然日判断，不假定 UTC 日期。"""
    publication = news._publication(_html(date="2026-10-17"), "Asia/Shanghai")
    assert publication == (datetime(2026, 10, 16, 16, tzinfo=timezone.utc), True)
    assert news._in_window(*publication, datetime(2026, 10, 17, 2, tzinfo=timezone.utc), datetime(2026, 10, 17, 16, tzinfo=timezone.utc), "Asia/Shanghai")
    assert not news._in_window(*publication, None, datetime(2026, 10, 16, 16, tzinfo=timezone.utc), "Asia/Shanghai")


def test_decode_retry_exhausted_is_collection_failure(collection_mock):
    """链接解码有限重试并保留错误分类，不读取 Google 聚合页面为正文。"""
    collection_mock.rss = _rss(["https://news.google.com/rss/articles/encoded"])
    collection_mock.decode_success = False
    with pytest.raises(news.CollectionError) as caught:
        news.fetch_articles("AI", START, END)
    assert caught.value.issues[0].stage == "link_decode"
    assert collection_mock.decode_calls == 2


def _event(title: str = "成功事件") -> generation.EventRecord:
    """构造可供复用和持久化的最小事件结构。"""
    return generation.EventRecord(title=title, summary="实际正文摘要", facts=["已发布"], sources=[generation.EventSource(title="原始标题", source="原始来源", url="https://example.com/article", published_at="2026-10-17T04:00:00+00:00", excerpt="正文证据")])


def test_individual_event_retry_skip_and_html_context(monkeypatch):
    """一个事件失败两次后仅跳过该事件，HTML 获得语言、风格和固定时间。"""
    calls = []
    system_prompts = []
    saved = []

    class Agent:
        """根据阶段模拟成功或坏结构响应。"""
        def __init__(self, schema):
            """保存本次阶段的响应协议。"""
            self.schema = schema

        def invoke(self, payload, config):
            """返回候选、单事件资料或 HTML，记录上下文。"""
            prompt = payload["messages"][0]["content"]
            calls.append((self.schema, prompt))
            if self.schema is generation.CandidateBatch:
                return {"structured_response": {"events": [{"title": "坏事件", "article_indices": [0], "relevance": 0.8}, {"title": "成功事件", "article_indices": [1], "relevance": 0.7}]}}
            if self.schema is generation.EventRecord:
                if '"title":"坏事件"' in prompt:
                    return {"structured_response": None}
                return {"structured_response": _event()}
            return {"structured_response": generation.HtmlDocument(html="<!doctype html><html>成功事件</html>")}

    def build(schema, prompt):
        """替换模型构建，保留生产调用流程。"""
        system_prompts.append((schema, prompt))
        return Agent(schema)

    monkeypatch.setattr(generation, "build_news_agent", build)
    articles = [news.NewsArticle("A", "https://example.com/a", "S", START, CHINESE * 5), news.NewsArticle("B", "https://example.com/b", "S", START, CHINESE * 5)]
    events, html = generation.generate_briefing(articles, "AI", "en", {"format": "grouped"}, on_events=saved.extend, time_window={"start": START.isoformat(), "end": END.isoformat()})
    assert len(events) == len(saved) == 1
    assert sum(schema is generation.EventRecord for schema, _ in calls) == 3
    assert "成功事件" in html
    assert "输出语言：en" in calls[-1][1] and "grouped" in calls[-1][1] and START.isoformat() in calls[-1][1]
    html_prompt = next(prompt for schema, prompt in system_prompts if schema is generation.HtmlDocument)
    assert "不展示独立不确定性栏目或不确定性条目" in html_prompt
    assert "不直接复制或拼接检索主题" in html_prompt
    html_user_prompt = next(prompt for schema, prompt in calls if schema is generation.HtmlDocument)
    assert "标题：AI 新闻简报" not in html_user_prompt


def test_html_failure_retains_events_and_retry_reuses_them(monkeypatch):
    """HTML 失败返回已完成资料，重试时不调用采集或候选识别。"""
    events = [_event()]
    seen = []

    def failing(schema, instruction, prompt):
        """模拟 HTML 模型失败并记录唯一阶段。"""
        seen.append(schema)
        raise generation.EventGenerationError("MODEL_FAILED")

    monkeypatch.setattr(generation, "_invoke", failing)
    with pytest.raises(generation.HtmlGenerationError) as caught:
        generation.generate_briefing([], "AI", "zh", {}, events=events)
    assert caught.value.events == events
    assert seen == [generation.HtmlDocument]


def test_no_candidates_is_empty_but_all_event_failures_raise(monkeypatch):
    """无合格候选与模型事件全部失败使用不同结果。"""
    article = news.NewsArticle("A", "https://example.com/a", "S", START, CHINESE * 5)

    def empty(schema, instruction, prompt):
        """模拟筛选无候选。"""
        return generation.CandidateBatch(events=[])

    monkeypatch.setattr(generation, "_invoke", empty)
    assert generation.generate_briefing([article], "AI", "zh", {}) == ([], "")

    def broken(schema, instruction, prompt):
        """识别一个候选后模拟事件生成失败。"""
        if schema is generation.CandidateBatch:
            return generation.CandidateBatch(events=[generation.EventCandidate(title="A", article_indices=[0], relevance=0.9)])
        raise generation.EventGenerationError("MODEL_FAILED")

    monkeypatch.setattr(generation, "_invoke", broken)
    with pytest.raises(generation.EventGenerationError, match="所有候选"):
        generation.generate_briefing([article], "AI", "zh", {})
