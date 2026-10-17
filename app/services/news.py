"""Google News RSS 采集和正文提取服务。"""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import quote
from xml.etree import ElementTree
import re
import httpx

@dataclass
class NewsArticle:
    """采集到的、具备正文的新闻文章。"""
    title: str
    url: str
    source: str
    published_at: datetime
    content: str

class _TextParser(HTMLParser):
    """提取正文可读文本的轻量 HTML 解析器。"""
    def __init__(self):
        """初始化文本节点缓冲区和忽略标签深度。"""
        super().__init__(); self.parts: list[str] = []; self.ignored_depth = 0
    def handle_data(self, data: str) -> None:
        """收集非空文本节点。"""
        value = re.sub(r"\s+", " ", data).strip()
        if value and not self.ignored_depth: self.parts.append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """忽略脚本、样式和导航区域中的文本。"""
        if tag in {"script", "style", "nav", "header", "footer", "noscript"}: self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        """离开应忽略的页面区域。"""
        if tag in {"script", "style", "nav", "header", "footer", "noscript"} and self.ignored_depth: self.ignored_depth -= 1

def fetch_articles(query: str, start: datetime | None = None, end: datetime | None = None, limit: int = 10) -> list[NewsArticle]:
    """查询 Google News RSS 并只返回成功提取正文的文章。"""
    rss_url = "https://news.google.com/rss/search?q=" + quote(query) + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "DailyNews/0.1"}) as client:
        response = client.get(rss_url); response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        output: list[NewsArticle] = []
        for item in root.findall("./channel/item")[:limit * 2]:
            title = item.findtext("title", "").strip(); url = item.findtext("link", "").strip(); date_text = item.findtext("pubDate", "")
            try: published = parsedate_to_datetime(date_text).astimezone(timezone.utc)
            except (TypeError, ValueError): continue
            if start and published < start.astimezone(timezone.utc) or end and published >= end.astimezone(timezone.utc): continue
            try:
                page = client.get(url); page.raise_for_status(); parser = _TextParser(); parser.feed(page.text); content = " ".join(parser.parts)
            except (httpx.HTTPError, UnicodeError): continue
            if len(content) < 200: continue
            source = item.findtext("source", "Google News").strip()
            output.append(NewsArticle(title=title, url=url, source=source, published_at=published, content=content[:20000]))
            if len(output) >= limit: break
        return output
