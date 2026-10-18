"""从已生成的简报 HTML 中提取展示标题和正文示意。"""
from html import unescape
from html.parser import HTMLParser
import re


class _BriefingParser(HTMLParser):
    """仅收集简报中的首个 h1 与全部段落文本，不执行或保留 HTML。"""

    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        """初始化标签栈和文本收集容器。"""
        super().__init__(convert_charrefs=True)
        self._stack: list[str] = []
        self._ignored_depth = 0
        self._heading_depth: int | None = None
        self._paragraph_depth: int | None = None
        self._heading_parts: list[str] = []
        self._paragraph_parts: list[str] = []
        self.heading = ""
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """记录语义标签；脚本、样式与页头内容不参与文字提取。"""
        if tag in self._VOID_TAGS:
            return
        self._stack.append(tag)
        if tag in {"script", "style", "head"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "h1" and not self.heading and self._heading_depth is None:
            self._heading_depth = len(self._stack)
            self._heading_parts = []
        if tag == "p" and self._paragraph_depth is None:
            self._paragraph_depth = len(self._stack)
            self._paragraph_parts = []

    def handle_endtag(self, tag: str) -> None:
        """在对应语义标签闭合时归档文本，并维护忽略层级。"""
        if self._ignored_depth:
            if tag in {"script", "style", "head"}:
                self._ignored_depth -= 1
            if self._stack:
                self._stack.pop()
            return
        if tag == "h1" and self._heading_depth == len(self._stack):
            self.heading = _clean_text("".join(self._heading_parts))
            self._heading_depth = None
        if tag == "p" and self._paragraph_depth == len(self._stack):
            paragraph = _clean_text("".join(self._paragraph_parts))
            if paragraph:
                self.paragraphs.append(paragraph)
            self._paragraph_depth = None
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        """收集当前标题或段落中的纯文本。"""
        if self._ignored_depth:
            return
        if self._heading_depth is not None:
            self._heading_parts.append(data)
        if self._paragraph_depth is not None:
            self._paragraph_parts.append(data)


def _clean_text(value: str) -> str:
    """解码实体并压缩空白，避免 HTML 排版字符进入展示文本。"""
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _is_metadata(value: str) -> bool:
    """识别品牌、主题、时间和计数等不应作为正文示意的元信息。"""
    return bool(re.search(
        r"(?:\bdailynews\b|新闻简报|latest\s+dispatch|^主题\s*[:：]|^新闻时间范围\s*[:：]|^生成时间\s*[:：]|^(?:共|合计)\s*\d+\s*个事件|^\d+\s*个事件[·。.；;]|^\d{4}[-年]\d{1,2}[-月]\d{1,2})",
        value,
        re.IGNORECASE,
    ))


def _parse(html: str) -> _BriefingParser:
    """解析不受信任 HTML 的可展示纯文本，不返回标签或属性。"""
    parser = _BriefingParser()
    parser.feed(html)
    parser.close()
    return parser


def extract_briefing_title(html: str, fallback: str) -> str:
    """提取 AI 在 h1 中拟定的标题，缺失或无效时保留已有标题。"""
    heading = _parse(html).heading
    return heading if heading and not _is_metadata(heading) else fallback


def extract_briefing_preview(html: str, limit: int = 120) -> str:
    """返回首段有效正文示意，过滤品牌与元信息并限制展示长度。"""
    for paragraph in _parse(html).paragraphs:
        if not _is_metadata(paragraph):
            return paragraph if len(paragraph) <= limit else f"{paragraph[:limit].rstrip()}…"
    return ""
