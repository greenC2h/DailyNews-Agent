"""Google News RSS 原文采集、有限重试和规则粗筛。"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
import json
import logging
import re
from urllib.parse import urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import feedparser
from googlenewsdecoder import GoogleDecoderAsync
import httpx
import trafilatura
from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class NewsArticle:
    """仅在任务处理期间使用的原文资料。"""
    title: str
    url: str
    source: str
    published_at: datetime
    content: str
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    language: str = ""
    content_hash: str = ""
    published_date_only: bool = False


@dataclass
class CollectionIssue:
    """不含响应内容和凭据的采集失败或过滤记录。"""
    url: str
    stage: str
    attempts: int
    error: str
    retryable: bool = False


class ArticleCollection(list[NewsArticle]):
    """兼容现有列表接口，同时保留部分失败和过滤原因。"""
    def __init__(self) -> None:
        """初始化正文集合与采集诊断列表。"""
        super().__init__()
        self.issues: list[CollectionIssue] = []


class CollectionError(RuntimeError):
    """RSS 不可用或所有原文均采集失败，不能伪装成无合格内容。"""
    def __init__(self, stage: str, issues: list[CollectionIssue]) -> None:
        """携带安全错误分类和诊断供执行记录保存。"""
        super().__init__(f"新闻采集失败：{stage}")
        self.stage = stage
        self.issues = issues


class _PublicationParser(HTMLParser):
    """只读取明确的原文发布时间，避免把更新时间或推测日期当发布时间。"""
    def __init__(self) -> None:
        """初始化元信息、结构化数据与时间标签缓冲。"""
        super().__init__()
        self.dates: list[str] = []
        self.json_parts: list[str] = []
        self.in_json = False
        self.in_publication_time = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """读取明确命名的发布时间元数据和 JSON-LD。"""
        values = dict(attrs)
        key = (values.get("property") or values.get("name") or values.get("itemprop") or "").lower()
        if tag == "meta" and key in {"article:published_time", "datepublished", "date", "pubdate", "publishdate", "publish_date", "publication_date", "dc.date.issued", "dcterms.issued", "sailthru.date"}:
            self.dates.append(values.get("content") or "")
        if tag == "script" and values.get("type") == "application/ld+json":
            self.in_json = True
            self.json_parts = []
        if tag == "time" and ("pubdate" in values or key == "datepublished"):
            self.in_publication_time = True
            if values.get("datetime"):
                self.dates.append(values["datetime"])

    def handle_data(self, data: str) -> None:
        """收集 JSON-LD 或明确发布时间标签的文本。"""
        if self.in_json:
            self.json_parts.append(data)
        if self.in_publication_time:
            self.dates.append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        """解析 JSON-LD 中的 datePublished，忽略 dateModified。"""
        if tag == "script" and self.in_json:
            self.in_json = False
            try:
                self._json_dates(json.loads("".join(self.json_parts)))
            except (ValueError, TypeError):
                pass
        if tag == "time":
            self.in_publication_time = False

    def _json_dates(self, value: object) -> None:
        """遍历 JSON-LD 对象及图结构中的明确发布时间。"""
        if isinstance(value, dict):
            if isinstance(value.get("datePublished"), str):
                self.dates.append(value["datePublished"])
            for child in value.values():
                if isinstance(child, (dict, list)):
                    self._json_dates(child)
        elif isinstance(value, list):
            for child in value:
                self._json_dates(child)


def _publication(html: str, timezone_name: str) -> tuple[datetime, bool] | None:
    """从原文明示元数据解析时间；缺少时区时采用任务时区，日期保留精度标记。"""
    parser = _PublicationParser()
    parser.feed(html)
    for raw in parser.dates:
        raw = raw.strip()
        date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw))
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                value = parsedate_to_datetime(raw)
            except (TypeError, ValueError, OverflowError):
                continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(timezone_name))
        return value.astimezone(timezone.utc), date_only
    return None


def _language(content: str, language: str) -> bool:
    """按实际正文字符比例粗筛中英文，未知语言设置明确拒绝。"""
    letters = sum(char.isalpha() for char in content)
    chinese = len(re.findall(r"[\u3400-\u9fff]", content))
    if not letters:
        return False
    if language.lower() in {"zh", "zh-cn", "zh-hans", "中文"}:
        return chinese / letters >= 0.2
    if language.lower() in {"en", "en-us", "english", "英文"}:
        words = re.findall(r"[a-z]+", content.lower())
        markers = sum(word in {"the", "a", "an", "of", "and", "to", "in", "is", "for", "that", "with", "on", "from", "as"} for word in words)
        return chinese / letters < 0.1 and markers >= max(2, len(words) * 0.015)
    raise ValueError("仅支持中文或英文来源语言")


def _in_window(published: datetime, date_only: bool, start: datetime | None, end: datetime | None, timezone_name: str) -> bool:
    """明确时间按左闭右开区间筛选，只有日期时按任务本地日期判断。"""
    zone = ZoneInfo(timezone_name)
    if date_only:
        day = published.astimezone(zone).date()
        if start and day < start.astimezone(zone).date():
            return False
        if end:
            local_end = end.astimezone(zone)
            if day > local_end.date() or day == local_end.date() and local_end.time() == time.min:
                return False
        return True
    return (start is None or published >= start.astimezone(timezone.utc)) and (end is None or published < end.astimezone(timezone.utc))


def _normalize_url(url: str) -> str:
    """规范化 URL 以去除片段及常见跟踪参数，不合并业务查询参数。"""
    from urllib.parse import parse_qsl
    parts = urlsplit(url)
    pairs = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(pairs), ""))


async def _request(client: httpx.AsyncClient, url: str, attempts: int, backoff: float) -> httpx.Response:
    """仅对连接、超时和临时 HTTP 错误执行有限指数退避。"""
    for attempt in range(attempts):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            retryable = isinstance(exc, httpx.TransportError) or isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {408, 429, 500, 502, 503, 504}
            if not retryable or attempt + 1 == attempts:
                exc.attempts = attempt + 1
                raise
            await asyncio.sleep(backoff * 2 ** attempt)
    raise RuntimeError("采集重试次数必须大于零")


async def fetch_articles_async(query: str, start: datetime | None = None, end: datetime | None = None, limit: int = 10, *, language: str = "zh", timezone_name: str = "Asia/Shanghai", include_terms: list[str] | None = None, exclude_terms: list[str] | None = None) -> ArticleCollection:
    """从 Google RSS 解码并获取原文；返回合格正文，全部采集失败时抛 CollectionError。

    Args:
        query: 已确认的主题和软关键词查询。
        start: 时间窗口开始（含），必须带时区。
        end: 时间窗口结束（不含），必须带时区。
        limit: 正文采集上限。
        language: 来源语言，支持中文或英文。
        timezone_name: 日期精度或缺少时区的原文使用的任务时区。
        include_terms: 明确必须包含的词，逐词检查正文及标题。
        exclude_terms: 明确排除的词，任一命中即跳过。
    """
    settings = get_settings()
    attempts = max(1, settings.news_retry_attempts)
    backoff = settings.news_retry_backoff_seconds
    output = ArticleCollection()
    if limit < 1:
        return output
    for boundary in (start, end):
        if boundary and boundary.tzinfo is None:
            raise ValueError("采集时间范围必须包含时区")
    _language("the news and the article", language)
    ZoneInfo(timezone_name)
    english = language.lower() in {"en", "en-us", "english", "英文"}
    locale = {"hl": "en-US", "gl": "US", "ceid": "US:en"} if english else {"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}
    search = query
    # 查询日期只减小候选范围；最终依据原文明示日期筛选，保留一天余量覆盖时区边界。
    if start:
        from datetime import timedelta
        search += " after:" + (start.astimezone(ZoneInfo(timezone_name)).date() - timedelta(days=1)).isoformat()
    if end:
        from datetime import timedelta
        search += " before:" + (end.astimezone(ZoneInfo(timezone_name)).date() + timedelta(days=1)).isoformat()
    rss_url = "https://news.google.com/rss/search?" + urlencode({"q": search, **locale})
    async with httpx.AsyncClient(timeout=settings.rss_timeout_seconds, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 DailyNews/0.1"}) as client, GoogleDecoderAsync(timeout=settings.rss_timeout_seconds) as decoder:
        try:
            response = await _request(client, rss_url, attempts, backoff)
        except httpx.HTTPError as exc:
            raise CollectionError("rss_request", [CollectionIssue(rss_url, "rss_request", getattr(exc, "attempts", 1), type(exc).__name__, True)]) from None
        feed = feedparser.parse(response.content)
        if feed.bozo or not feed.get("version"):
            raise CollectionError("rss_parse", [CollectionIssue(rss_url, "rss_parse", 1, "INVALID_RSS")])
        seen_urls: set[str] = set()
        seen_content: set[str] = set()
        for item in feed.entries[:limit * 4]:
            rss_link = item.get("link", "")
            url = rss_link
            if urlsplit(url).hostname == "news.google.com":
                decoded = None
                for attempt in range(attempts):
                    decoded = await decoder.decode_google_news_url(rss_link)
                    if decoded.get("success"):
                        break
                    if attempt + 1 < attempts:
                        await asyncio.sleep(backoff * 2 ** attempt)
                if not decoded or not decoded.get("success"):
                    output.issues.append(CollectionIssue(rss_link, "link_decode", attempts, "DECODE_FAILED", True))
                    continue
                url = decoded["decoded_url"]
            if urlsplit(url).scheme not in {"http", "https"} or urlsplit(url).hostname in {"news.google.com", "consent.google.com"}:
                output.issues.append(CollectionIssue(rss_link, "link_decode", 1, "INVALID_ORIGINAL_URL"))
                continue
            url = _normalize_url(url)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            extracted = None
            page = None
            failed = False
            for attempt in range(attempts):
                try:
                    # 请求和提取共用同一来源预算，避免嵌套重试成倍放大请求。
                    page = await _request(client, url, 1, backoff)
                except httpx.HTTPError as exc:
                    temporary = isinstance(exc, httpx.TransportError) or isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {408, 429, 500, 502, 503, 504}
                    if temporary and attempt + 1 < attempts:
                        await asyncio.sleep(backoff * 2 ** attempt)
                        continue
                    output.issues.append(CollectionIssue(url, "body_request", attempt + 1, type(exc).__name__, temporary))
                    failed = True
                    break
                # Google 中间页即使返回大量文字，也绝不是新闻正文。
                if urlsplit(str(page.url)).hostname in {"news.google.com", "consent.google.com"}:
                    extracted = None
                else:
                    try:
                        extracted = trafilatura.bare_extraction(page.text, url=str(page.url), include_comments=False, with_metadata=True, favor_precision=True, date_extraction_params={"extensive_search": False, "original_date": True})
                        extracted = extracted.as_dict() if extracted else None
                    except (ValueError, TypeError, UnicodeError):
                        extracted = None
                if extracted and len((extracted.get("text") or "").strip()) >= 200:
                    break
                extracted = None
                if attempt + 1 < attempts:
                    await asyncio.sleep(backoff * 2 ** attempt)
            if failed:
                continue
            if not extracted or page is None:
                output.issues.append(CollectionIssue(url, "body_extract", attempts, "BODY_UNAVAILABLE", True))
                continue
            content = extracted["text"].strip()
            publication = _publication(page.text, timezone_name)
            if publication is None:
                output.issues.append(CollectionIssue(url, "time_filter", 1, "ORIGINAL_PUBLICATION_MISSING"))
                continue
            published, date_only = publication
            if not _in_window(published, date_only, start, end, timezone_name):
                output.issues.append(CollectionIssue(url, "time_filter", 1, "OUTSIDE_WINDOW"))
                continue
            if not _language(content, language):
                output.issues.append(CollectionIssue(url, "language_filter", 1, "LANGUAGE_MISMATCH"))
                continue
            title = extracted.get("title") or item.get("title", "")
            searchable = (title + "\n" + content).casefold()
            if any(term.casefold() not in searchable for term in include_terms or []) or any(term.casefold() in searchable for term in exclude_terms or []):
                output.issues.append(CollectionIssue(url, "terms_filter", 1, "EXPLICIT_CONDITION_MISMATCH"))
                continue
            digest = sha256(re.sub(r"\s+", "", content).encode()).hexdigest()
            if digest in seen_content:
                continue
            seen_content.add(digest)
            source = (item.get("source") or {}).get("title") or extracted.get("sitename") or urlsplit(str(page.url)).hostname or ""
            output.append(NewsArticle(title, _normalize_url(str(page.url)), source, published, content[:30000], language=language, content_hash=digest, published_date_only=date_only))
            if len(output) >= limit:
                break
    failures = [issue for issue in output.issues if issue.stage in {"link_decode", "body_request", "body_extract"}]
    for issue in output.issues:
        logger.info("news source stage=%s error=%s attempts=%s", issue.stage, issue.error, issue.attempts)
    if not output and failures:
        raise CollectionError("original_articles", output.issues)
    return output


def fetch_articles(query: str, start: datetime | None = None, end: datetime | None = None, limit: int = 10, **kwargs) -> ArticleCollection:
    """为同步 Worker 保留原接口，异步调用方应直接使用 fetch_articles_async。"""
    return asyncio.run(fetch_articles_async(query, start, end, limit, **kwargs))
