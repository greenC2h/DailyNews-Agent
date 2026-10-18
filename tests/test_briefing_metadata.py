"""简报 AI 标题提取的回归测试。"""

from app.services.briefing_metadata import extract_briefing_title


def test_extracts_ai_heading():
    """AI 标题应从 HTML 的首个 h1 提取，而不是使用旧标题。"""
    html = """
    <html><head><title>浏览器标题</title></head><body>
      <p>DailyNews · 新闻简报</p>
      <h1>AI 落地路径：从终端协同到原生仪器</h1>
      <p>主题：汽车、AI、科技、创新</p>
      <p>新闻时间范围：2026年10月17日</p>
      <p>共 3 个事件，按报道时间由新到旧排列</p>
      <p>本期聚焦端侧智能设备与算力硬件的最新协同进展。</p>
    </body></html>
    """

    assert extract_briefing_title(html, "旧标题") == "AI 落地路径：从终端协同到原生仪器"


def test_heading_falls_back_when_html_has_no_h1():
    """HTML 缺少 h1 时应继续使用数据库中的标题。"""
    assert extract_briefing_title("<p>没有标题</p>", "历史标题") == "历史标题"
