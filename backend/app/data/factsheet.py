"""X1 事实底稿：所有策略（规则 / AI）共享的同一信息包。

原则：能拿到真数据就绝不让模型编；缺失字段显式标 missing。
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from ..factors.definitions import FACTOR_NAMES
from ..factors.panel import latest_factor_snapshot
from . import fuyao_client as fuyao
from . import market
from . import news_rss
from .indicators import indicators_text, latest_indicator_snapshot

logger = logging.getLogger(__name__)


def build_factsheet(
    code: str,
    name: str = "",
    asof: date | None = None,
    peer_codes: list[str] | None = None,
    use_tushare: bool = False,
    factor_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建单票事实底稿：扶摇行情/因子 + RSS 资讯。"""
    del use_tushare
    asof = asof or date.today()
    missing: list[str] = []
    sheet: dict[str, Any] = {
        "code": code,
        "name": name,
        "asof": asof.isoformat(),
        "schema": "x1_v1",
        "data_source": "fuyao",
        "news_source": "rss",
        "factors": {},
        "quote": {},
        "valuation": {},
        "technical": {},
        "technical_summary": "",
        "news": [],
        "missing": missing,
    }

    if not fuyao.available():
        missing.append("fuyao_api_key")
        sheet["missing"] = missing
        return sheet

    # 行情 + 估值（扶摇）
    try:
        quote = market.get_quote(code)
        if quote:
            price = quote.get("price")
            pct = quote.get("pct_change")
            sheet["quote"] = {
                "price": price,
                "pct_change": pct,
                "turnover": quote.get("turnover"),
                "volume": quote.get("volume"),
                "quote_asof": quote.get("quote_asof"),
                "source": quote.get("source"),
            }
            if quote.get("name") and not name:
                sheet["name"] = quote["name"]
                name = sheet["name"]
        else:
            missing.append("quote")
    except Exception as err:  # noqa: BLE001
        missing.append(f"quote:{err}")

    try:
        if factor_data is not None:
            ep = factor_data.get("ep")
            bp = factor_data.get("bp")
            sheet["valuation"] = {
                "pe": (1.0 / float(ep)) if ep is not None and float(ep) > 0 else None,
                "pb": (1.0 / float(bp)) if bp is not None and float(bp) > 0 else None,
                "ps": None,
            }
            if not sheet["valuation"]["pe"] and not sheet["valuation"]["pb"]:
                missing.append("valuation")
        else:
            val = fuyao.valuation_snapshot([code])
            if not val.empty:
                r = val.iloc[0]
                sheet["valuation"] = {
                    "pe": r.get("pe_ttm"),
                    "pb": r.get("pb"),
                    "ps": r.get("ps_ttm"),
                }
                if r.get("name") and not sheet.get("name"):
                    sheet["name"] = r["name"]
            else:
                missing.append("valuation")
    except Exception as err:  # noqa: BLE001
        missing.append(f"valuation:{err}")

    # 技术摘要：扶摇日 K → 本地指标
    try:
        bars = fuyao.daily_bars(code, asof - timedelta(days=400), asof)
        if bars.empty:
            missing.append("technical")
            sheet["technical_summary"] = "(K线为空)"
        else:
            kline = bars.rename(columns={
                "date": "日期", "open": "开盘", "close": "收盘",
                "high": "最高", "low": "最低", "volume": "成交量",
            })
            close = kline["收盘"].astype(float)
            kline["涨跌幅"] = (close.pct_change() * 100).round(2)
            sheet["technical"] = latest_indicator_snapshot(kline)
            sheet["technical_summary"] = indicators_text(kline, days=40)
    except Exception as err:  # noqa: BLE001
        missing.append(f"technical:{err}")
        sheet["technical_summary"] = f"(K线不可用: {err})"

    sheet["company_info"] = {"thscode": fuyao.to_thscode(code), "name": sheet.get("name") or name}

    # 新闻：Vibe 式 RSS
    try:
        # 大盘背景已在独立的 market_overview 中提供；这里仅允许个股直达新闻，
        # 避免陈旧宏观头条被模型误当成该公司的催化或反证。
        sheet["news"] = news_rss.news_for_stock(
            code, sheet.get("name") or name, limit=10, include_general=False)
        if not sheet["news"]:
            missing.append("news")
    except Exception as err:  # noqa: BLE001
        missing.append(f"news:{err}")
        sheet["news"] = []

    # S2 因子截面
    try:
        if factor_data is not None:
            if not factor_data:
                missing.append("factors_self")
                for f in FACTOR_NAMES:
                    sheet["factors"][f] = None
                sheet["factors"]["score"] = None
                sheet["factors"]["rank"] = None
            else:
                for f in FACTOR_NAMES:
                    sheet["factors"][f] = factor_data.get(f)
                sheet["factors"]["score"] = factor_data.get("score")
                sheet["factors"]["rank"] = factor_data.get("rank")
                sheet["factors"]["universe_size"] = factor_data.get("universe_size")
        else:
            codes = list(dict.fromkeys([code] + (peer_codes or [])))
            snap = latest_factor_snapshot(codes, asof=asof)
            if snap.empty:
                missing.append("factors")
                for f in FACTOR_NAMES:
                    sheet["factors"][f] = None
                sheet["factors"]["score"] = None
                sheet["factors"]["rank"] = None
            else:
                row = snap[snap["code"] == code]
                if row.empty:
                    missing.append("factors_self")
                    for f in FACTOR_NAMES:
                        sheet["factors"][f] = None
                    sheet["factors"]["score"] = None
                    sheet["factors"]["rank"] = None
                else:
                    r = row.iloc[0]
                    for f in FACTOR_NAMES:
                        val = r.get(f)
                        sheet["factors"][f] = (
                            None if val is None or (isinstance(val, float) and val != val)
                            else float(val)
                        )
                    score = r.get("score")
                    sheet["factors"]["score"] = None if score != score else float(score)
                    ranked = snap.dropna(subset=["score"]).sort_values("score", ascending=False)
                    ranks = {c: i + 1 for i, c in enumerate(ranked["code"].astype(str).tolist())}
                    sheet["factors"]["rank"] = ranks.get(code)
                    sheet["factors"]["universe_size"] = int(len(ranked))
    except Exception as err:  # noqa: BLE001
        logger.warning("factsheet factors %s: %s", code, err)
        missing.append(f"factors:{err}")

    sheet["missing"] = missing
    return sheet



def factsheet_text(sheet: dict[str, Any]) -> str:
    """给 LLM 的只读文本；明确列出缺失项，禁止臆测。"""
    lines = [
        f"【事实底稿 X1】{sheet.get('name', '')}({sheet.get('code')}) asof={sheet.get('asof')}",
        "以下为客观数据；missing 中的字段不得臆造。",
        "",
        "行情: " + json.dumps(sheet.get("quote") or {}, ensure_ascii=False),
        "估值: " + json.dumps(sheet.get("valuation") or {}, ensure_ascii=False),
        "S2因子: " + json.dumps(sheet.get("factors") or {}, ensure_ascii=False),
        "结构化技术指标: " + json.dumps(sheet.get("technical") or {}, ensure_ascii=False),
        "",
        "技术摘要:",
        sheet.get("technical_summary") or "(无)",
        "",
        "公司信息:",
        json.dumps(sheet.get("company_info") or {}, ensure_ascii=False)[:1500],
        "",
        "新闻:",
    ]
    for item in (sheet.get("news") or [])[:8]:
        lines.append(f"- [{item.get('time')}] {item.get('title')}: {item.get('content', '')[:120]}")
    if sheet.get("missing"):
        lines.append("")
        lines.append("缺失字段: " + ", ".join(sheet["missing"]))
        lines.append("对缺失字段请标注「无数据」，不得编造数字。")
    return "\n".join(lines)
