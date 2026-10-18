"""从已生成的简报 HTML 中提取 AI 拟定的展示标题。"""
from html import unescape
from html.parser import HTMLParser
import re


class _BriefingParser(HTMLParser):
    """仅收集简报中的首个 h1 文本，不执行或保留 HTML。"""

    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        """初始化标签栈和文本收集容器。"""
        super().__init__(convert_charrefs=True)
        self._stack: list[str] = []
        self._ignored_depth = 0
        self._heading_depth: int | None = None
        self._heading_parts: list[str] = []
        self.heading = ""

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
    def handle_endtag(self, tag: str) -> None:
        """在标题闭合时归档文本，并维护忽略层级。"""
        if self._ignored_depth:
            if tag in {"script", "style", "head"}:
                self._ignored_depth -= 1
            if self._stack:
                self._stack.pop()
            return
        if tag == "h1" and self._heading_depth == len(self._stack):
            self.heading = _clean_text("".join(self._heading_parts))
            self._heading_depth = None
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        """收集当前标题中的纯文本。"""
        if self._ignored_depth:
            return
        if self._heading_depth is not None:
            self._heading_parts.append(data)
def _clean_text(value: str) -> str:
    """解码实体并压缩空白，避免 HTML 排版字符进入展示文本。"""
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _parse(html: str) -> _BriefingParser:
    """解析不受信任 HTML 的可展示纯文本，不返回标签或属性。"""
    parser = _BriefingParser()
    parser.feed(html)
    parser.close()
    return parser


def extract_briefing_title(html: str, fallback: str) -> str:
    """提取 AI 在 h1 中拟定的标题，缺失或无效时保留已有标题。"""
    heading = _parse(html).heading
    return heading or fallback
