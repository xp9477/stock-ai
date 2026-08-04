"""RSS 新闻解析单测（无网络）。"""
from app.data.news_rss import _parse_rss, _strip_html, news_for_stock


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test</title>
    <item>
      <title>贵州茅台发布季度报告</title>
      <description>营收增长相关内容</description>
      <link>https://example.com/1</link>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>博彩网站优惠</title>
      <description>赌场</description>
      <link>https://example.com/bad</link>
    </item>
  </channel>
</rss>
""".encode("utf-8")


def test_parse_rss_and_blocklist():
    items = _parse_rss(SAMPLE_RSS, "test")
    assert len(items) == 2
    assert "茅台" in items[0]["title"]


def test_strip_html():
    assert _strip_html("<p>hello <b>x</b></p>") == "hello x"


def test_news_for_stock_matches(monkeypatch):
    from app.data import news_rss

    def fake_headlines():
        return [
            {"title": "600519 贵州茅台上涨", "content": "白酒", "time": "t",
             "url": "u", "source": "s"},
            {"title": "无关宏观", "content": "美联储", "time": "t",
             "url": "u", "source": "s"},
        ]

    monkeypatch.setattr(news_rss, "fetch_all_headlines", fake_headlines)
    hit = news_for_stock("600519", "贵州茅台", limit=5)
    assert hit and hit[0]["match"] == "stock"
    miss = news_for_stock("000001", "平安银行", limit=5)
    # 无命中时退回 general
    assert miss and miss[0]["match"] == "general"
