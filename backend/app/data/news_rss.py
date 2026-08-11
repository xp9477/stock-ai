"""Vibe 路线资讯：公开 RSS，零 Key，本地抓取。

不做付费新闻；个股匹配靠标题/摘要关键词（代码或简称）。
源列表刻意精简，可后续扩到 investment-news 全量。
"""
from __future__ import annotations

import logging
import re
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..data.cache import ttl_cache

logger = logging.getLogger(__name__)

# 真实 RSS/Atom（stdlib 可解析；失效源自动跳过，探测页可见健康）
# 国内财经向 + 国际宏观（保留，对风险偏好/外盘有指导意义）
RSS_FEEDS: list[dict[str, str]] = [
    # ----- 国内财经 -----
    {"id": "people_finance", "name": "人民网财经",
     "url": "http://www.people.com.cn/rss/finance.xml", "region": "cn"},
    {"id": "chinanews_finance", "name": "中新网财经",
     "url": "https://www.chinanews.com.cn/rss/finance.xml", "region": "cn"},
    {"id": "chinanews_stock", "name": "中新网证券",
     "url": "https://www.chinanews.com.cn/rss/stock.xml", "region": "cn"},
    {"id": "sina_finance", "name": "新浪财经滚动",
     "url": "https://rss.sina.com.cn/roll/finance/hot_roll.xml", "region": "cn"},
    {"id": "jrj_finance", "name": "金融界",
     "url": "https://rss.jrj.com.cn/finance.xml", "region": "cn"},
    {"id": "huxiu", "name": "虎嗅",
     "url": "https://www.huxiu.com/rss/0.xml", "region": "cn"},
    {"id": "36kr", "name": "36氪",
     "url": "https://36kr.com/feed", "region": "cn"},
    # ----- 国际 / 宏观（保留）-----
    {"id": "bbc_biz", "name": "BBC Business",
     "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "region": "global"},
    {"id": "cnbc_top", "name": "CNBC Top News",
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
     "region": "global"},
    {"id": "reuters_biz", "name": "Reuters Business",
     "url": "https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best",
     "region": "global"},
    {"id": "sspai", "name": "少数派",
     "url": "https://sspai.com/feed", "region": "global"},
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


def _fetch_feed(url: str, timeout: int | None = None) -> bytes | None:
    if timeout is None:
        try:
            from . import datasources as ds
            timeout = ds.timeout_sec("rss", 12)
        except Exception:  # noqa: BLE001
            timeout = 12
    try:
        req = Request(url, headers={"User-Agent": "stock-ai-news/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (URLError, TimeoutError, OSError) as err:
        logger.debug("RSS fetch fail %s: %s", url, err)
        return None


def _collect_headlines(limit_per_feed: int = 15) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """抓取一次并同时返回逐源健康状态；供盘前强制刷新使用。"""
    try:
        from . import datasources as ds
        if not ds.is_enabled("rss"):
            return [], [{"id": f["id"], "ok": False, "reason": "disabled"}
                        for f in RSS_FEEDS]
    except Exception:  # noqa: BLE001
        pass

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for feed in RSS_FEEDS:
        raw = _fetch_feed(feed["url"])
        if not raw:
            sources.append({"id": feed["id"], "name": feed["name"],
                            "ok": False, "item_count": 0, "reason": "fetch_failed"})
            continue
        parsed = _parse_rss(raw, feed["name"])[:limit_per_feed]
        sources.append({"id": feed["id"], "name": feed["name"],
                        "ok": True, "item_count": len(parsed), "reason": ""})
        for item in parsed:
            title = item.get("title") or ""
            if not title or _BLOCK.search(title) or _BLOCK.search(item.get("content") or ""):
                continue
            key = title[:80]
            if key in seen:
                continue
            seen.add(key)
            normalized = {**item, "feed_id": feed["id"], "region": feed.get("region", "")}
            normalized["content_hash"] = hashlib.sha256(
                f"{normalized.get('url', '')}\n{title}\n{normalized.get('content', '')}".encode("utf-8")
            ).hexdigest()
            out.append(normalized)
    return out, sources


@ttl_cache(600)
def fetch_all_headlines(limit_per_feed: int = 15) -> list[dict[str, Any]]:
    """展示/分析用缓存聚合；不能单独证明盘前信息覆盖完整。"""
    return _collect_headlines(limit_per_feed)[0]


def _match_stock_news(
    headlines: list[dict[str, Any]], code: str, name: str, limit: int,
) -> list[dict[str, Any]]:
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

    return matched[:limit]


def news_for_stock(
    code: str, name: str = "", limit: int = 10, *, include_general: bool = True,
) -> list[dict[str, Any]]:
    """按代码/简称过滤资讯。

    通用头条只用于 LLM 的宏观背景；信息门禁必须使用
    :func:`stock_news_gate_snapshot`，绝不能把 general 当作个股覆盖证明。
    """
    headlines = fetch_all_headlines()
    matched = _match_stock_news(headlines, code, name, limit)
    if matched or not include_general:
        return matched

    # 无个股命中：给宏观样本，避免底稿新闻全空
    general = [{**h, "match": "general"} for h in headlines[:limit]]
    return general


def stock_news_gate_snapshot(
    code: str, name: str = "", limit: int = 20, *, force_refresh: bool = True,
) -> dict[str, Any]:
    """盘前增量审查快照。

    RSS 是补充信息源，不是法定披露源，因此 ``official_coverage`` 永远为
    False，直到项目显式接入并验证正式公告供应商。调用者必须 fail closed
    或要求用户逐笔确认已经人工核对正式公告。
    """
    if force_refresh and hasattr(fetch_all_headlines, "cache_clear"):
        fetch_all_headlines.cache_clear()
    headlines, sources = _collect_headlines()
    items = _match_stock_news(headlines, code, name, limit)
    fingerprint = hashlib.sha256(
        "\n".join(sorted(str(i.get("content_hash") or "") for i in items)).encode("utf-8")
    ).hexdigest()
    successful = sum(1 for source in sources if source.get("ok"))
    return {
        "code": code,
        "items": items,
        "fingerprint": fingerprint,
        "fetched_at": datetime.now().isoformat(),
        "rss_coverage_ok": successful > 0,
        "official_coverage": False,
        "source_results": sources,
    }
