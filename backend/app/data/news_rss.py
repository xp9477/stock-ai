"""Vibe 路线资讯：公开 RSS，零 Key，本地抓取。

不做付费新闻；个股匹配靠标题/摘要关键词（代码或简称）。
源列表刻意精简，可后续扩到 investment-news 全量。
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..data.cache import ttl_cache

logger = logging.getLogger(__name__)

# 真实 RSS/Atom（stdlib 可解析；失效源自动跳过，可后续扩到 investment-news 全量）
RSS_FEEDS: list[dict[str, str]] = [
    {"id": "bbc_biz", "name": "BBC Business",
     "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"id": "cnbc_top", "name": "CNBC Top News",
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"},
    {"id": "people_finance", "name": "人民网财经",
     "url": "http://www.people.com.cn/rss/finance.xml"},
    {"id": "36kr", "name": "36氪", "url": "https://36kr.com/feed"},
    {"id": "sspai", "name": "少数派", "url": "https://sspai.com/feed"},
]

# 合规粗滤（Vibe 同思路：剔除赌/预测市场/黄赌毒等）
_BLOCK = re.compile(
    r"(赌场|博彩|色情|成人|polymarket|prediction\s*market|crypto\s*casino|"
    r"赌博|彩票中奖)",
    re.I,
)


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _parse_rss(xml_bytes: bytes, source: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return items

    # RSS 2.0
    channel_items = root.findall("./channel/item")
    # Atom
    if not channel_items:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns) or root.findall(
            "{http://www.w3.org/2005/Atom}entry"
        ):
            title = entry.findtext("{http://www.w3.org/2005/Atom}title") or entry.findtext("title") or ""
            summary = (
                entry.findtext("{http://www.w3.org/2005/Atom}summary")
                or entry.findtext("{http://www.w3.org/2005/Atom}content")
                or ""
            )
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = ""
            if link_el is not None:
                link = link_el.get("href") or (link_el.text or "")
            updated = (
                entry.findtext("{http://www.w3.org/2005/Atom}updated")
                or entry.findtext("{http://www.w3.org/2005/Atom}published")
                or ""
            )
            items.append({
                "title": _strip_html(title),
                "content": _strip_html(summary)[:300],
                "time": updated[:19],
                "url": link,
                "source": source,
            })
        return items

    for it in channel_items:
        title = it.findtext("title") or ""
        desc = it.findtext("description") or it.findtext("content:encoded") or ""
        link = it.findtext("link") or ""
        pub = it.findtext("pubDate") or it.findtext("dc:date") or ""
        time_s = pub
        try:
            if pub:
                time_s = parsedate_to_datetime(pub).strftime("%Y-%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            time_s = pub[:19]
        items.append({
            "title": _strip_html(title),
            "content": _strip_html(desc)[:300],
            "time": time_s,
            "url": link,
            "source": source,
        })
    return items


def _fetch_feed(url: str, timeout: int = 12) -> bytes | None:
    try:
        req = Request(url, headers={"User-Agent": "stock-ai-news/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (URLError, TimeoutError, OSError) as err:
        logger.debug("RSS fetch fail %s: %s", url, err)
        return None


@ttl_cache(600)
def fetch_all_headlines(limit_per_feed: int = 15) -> list[dict[str, Any]]:
    """抓取全部配置源，合并去重。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for feed in RSS_FEEDS:
        raw = _fetch_feed(feed["url"])
        if not raw:
            continue
        for item in _parse_rss(raw, feed["name"])[:limit_per_feed]:
            title = item.get("title") or ""
            if not title or _BLOCK.search(title) or _BLOCK.search(item.get("content") or ""):
                continue
            key = title[:80]
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def news_for_stock(code: str, name: str = "", limit: int = 10) -> list[dict[str, Any]]:
    """按代码/简称过滤相关资讯；无命中则返回宏观头条前几条并标注 general。"""
    headlines = fetch_all_headlines()
    keys = [code]
    if name:
        # 去掉常见后缀
        short = re.sub(r"(股份|集团|有限|公司|科技|控股)$", "", name)
        if short:
            keys.append(short)
        keys.append(name)

    matched = []
    for item in headlines:
        blob = f"{item.get('title', '')} {item.get('content', '')}"
        if any(k and k in blob for k in keys):
            matched.append({**item, "match": "stock"})
        if len(matched) >= limit:
            break

    if matched:
        return matched[:limit]

    # 无个股命中：给宏观样本，避免底稿新闻全空
    general = [{**h, "match": "general"} for h in headlines[:limit]]
    return general
