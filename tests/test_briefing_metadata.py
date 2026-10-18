"""简报展示标题与正文示意的回归测试。"""

from app.services.briefing_metadata import extract_briefing_preview, extract_briefing_title


def test_extracts_ai_heading_and_first_meaningful_paragraph():
    """AI 标题和首段正文应跳过品牌、主题、时间及事件计数元信息。"""
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
    assert extract_briefing_preview(html) == "本期聚焦端侧智能设备与算力硬件的最新协同进展。"


def test_metadata_parser_falls_back_and_bounds_preview():
    """缺少有效标题时保留旧值，正文示意必须受固定长度限制。"""
    html = f"<p>DailyNews · 新闻简报</p><p>{'新闻内容' * 80}</p>"

    assert extract_briefing_title(html, "历史标题") == "历史标题"
    assert extract_briefing_preview(html, limit=12) == f"{'新闻内容' * 3}…"
