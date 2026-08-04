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
from . import news_rss
from .indicators import indicators_text

logger = logging.getLogger(__name__)


def build_factsheet(
    code: str,
    name: str = "",
    asof: date | None = None,
    peer_codes: list[str] | None = None,
    use_tushare: bool = False,
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
        snaps = fuyao.prices_snapshot([code])
        if snaps:
            it = snaps[0]
            # 扶摇 snapshot 实测字段
            price = it.get("last_price") or it.get("close_price") or it.get("price")
            pct = it.get("price_change_ratio_pct")
            if pct is None:
                pct = it.get("change_ratio") or it.get("pct_change")
            sheet["quote"] = {
                "price": price,
                "pct_change": pct,
                "turnover": it.get("turnover") or it.get("amount"),
                "volume": it.get("volume"),
            }
            if it.get("name") and not name:
                sheet["name"] = it["name"]
                name = sheet["name"]
        else:
            missing.append("quote")
    except Exception as err:  # noqa: BLE001
        missing.append(f"quote:{err}")

    try:
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
            sheet["technical_summary"] = indicators_text(kline, days=40)
    except Exception as err:  # noqa: BLE001
        missing.append(f"technical:{err}")
        sheet["technical_summary"] = f"(K线不可用: {err})"

    sheet["company_info"] = {"thscode": fuyao.to_thscode(code), "name": sheet.get("name") or name}

    # 新闻：Vibe 式 RSS
    try:
        sheet["news"] = news_rss.news_for_stock(code, sheet.get("name") or name, limit=10)
        if not sheet["news"]:
            missing.append("news")
    except Exception as err:  # noqa: BLE001
        missing.append(f"news:{err}")
        sheet["news"] = []

    # S2 因子截面
    codes = list(dict.fromkeys([code] + (peer_codes or [])))
    try:
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
