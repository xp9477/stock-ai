"""同花顺扶摇 (fuyao.aicubes.cn) REST 客户端。

约定：行情/财务/估值/ETF 的唯一主源；不做免费源降级。
鉴权：请求头 X-api-key = FUYAO_API_KEY。
实体标识：thscode，如 600519.SH / 510300.SH。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests

from ..runtime_settings import get_setting

logger = logging.getLogger(__name__)

BASE_URL = "https://fuyao.aicubes.cn"
TZ_SH = timezone(timedelta(hours=8))


def available() -> bool:
    return bool(str(get_setting("secrets.fuyao_api_key")).strip())


def to_thscode(code: str) -> str:
    """内部 6 位代码 → 扶摇 thscode。"""
    code = code.strip().upper()
    if "." in code:
        return code
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def from_thscode(thscode: str) -> str:
    return thscode.split(".")[0]


def _headers() -> dict[str, str]:
    return {"X-api-key": str(get_setting("secrets.fuyao_api_key")).strip()}


def _ms(d: date | datetime) -> int:
    if isinstance(d, datetime):
        dt = d if d.tzinfo else d.replace(tzinfo=TZ_SH)
    else:
        dt = datetime(d.year, d.month, d.day, tzinfo=TZ_SH)
    return int(dt.timestamp() * 1000)


def _ms_to_date(ms: int | float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=TZ_SH).strftime("%Y-%m-%d")


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not available():
        raise RuntimeError("FUYAO_API_KEY 未配置")
    from . import datasources as ds

    if not ds.is_enabled("fuyao"):
        raise RuntimeError("扶摇数据源已禁用（设置 → 数据源）")
    to = ds.timeout_sec("fuyao", 30)
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, headers=_headers(), params=params or {}, timeout=to)
    resp.raise_for_status()
    body = resp.json()
    code = body.get("code")
    if code != 0:
        raise RuntimeError(
            f"扶摇错误 code={code} message={body.get('message')} path={path}"
        )
    return body.get("data") or {}


def daily_bars(
    code: str,
    start: date | str,
    end: date | str | None = None,
    adjust: str = "forward",
) -> pd.DataFrame:
    """日 K → 标准列 date, open, high, low, close, volume, amount。"""
    if isinstance(start, str):
        start = date.fromisoformat(start[:10])
    if end is None:
        end = date.today()
    elif isinstance(end, str):
        end = date.fromisoformat(end[:10])

    # 接口窗口最长 10 年；分段拉取
    frames: list[pd.DataFrame] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(date(cursor.year + 9, cursor.month, cursor.day)
                        if cursor.month != 2 or cursor.day != 29
                        else date(cursor.year + 9, 2, 28),
                        end)
        # 简化：按约 9 年切，避免 10 年边界
        chunk_end = min(cursor + timedelta(days=365 * 9), end)
        data = _get("/api/a-share/prices/historical", {
            "thscode": to_thscode(code),
            "interval": "1d",
            "start": _ms(cursor),
            "end": _ms(chunk_end + timedelta(days=1)) - 1,
            "adjust": adjust,
        })
        items = data.get("item") or []
        if items:
            rows = []
            for it in items:
                rows.append({
                    "date": _ms_to_date(it["date_ms"]),
                    "open": float(it["open_price"]),
                    "high": float(it["high_price"]),
                    "low": float(it["low_price"]),
                    "close": float(it["close_price"]),
                    "volume": float(it.get("volume") or 0),
                    "amount": float(it.get("turnover") or 0),
                })
            frames.append(pd.DataFrame(rows))
        if chunk_end >= end:
            break
        cursor = chunk_end + timedelta(days=1)

    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


def valuation_snapshot(codes: list[str]) -> pd.DataFrame:
    """最新估值：pe_ttm, pb_mrq 等。"""
    if not codes:
        return pd.DataFrame()
    ths = ",".join(to_thscode(c) for c in codes)
    data = _get("/api/a-share/valuations/snapshot", {"thscodes": ths})
    items = data.get("item") or []
    if not items:
        return pd.DataFrame()
    rows = []
    for it in items:
        pe = it.get("pe_ttm")
        pb = it.get("pb_mrq")
        rows.append({
            "code": from_thscode(it["thscode"]),
            "thscode": it["thscode"],
            "name": it.get("name") or "",
            "pe_ttm": float(pe) if pe is not None else None,
            "pb": float(pb) if pb is not None else None,
            "ps_ttm": float(it["ps_ttm"]) if it.get("ps_ttm") is not None else None,
        })
    return pd.DataFrame(rows)


def financial_indicators(code: str, report: str) -> dict[str, Any]:
    """单期财务指标。report 如 2024-4（年报）。"""
    data = _get("/api/a-share/financials/indicators", {
        "thscode": to_thscode(code),
        "report": report,
    })
    out: dict[str, Any] = {"report": report, "roe": None, "roa": None}
    for block in data.get("abilities") or []:
        for ind in block.get("indicators") or []:
            idx = ind.get("index_id")
            val = ind.get("value")
            if val is None:
                continue
            try:
                num = float(val)
            except (TypeError, ValueError):
                continue
            if idx == "index_weighted_avg_roe":
                out["roe"] = num
            elif idx == "total_assets_net_ratio":
                out["roa"] = num
    return out


def roe_history(code: str, years: int = 4) -> pd.DataFrame:
    """近若干年季报/年报 ROE，带近似公告日（用于 PIT asof）。

    扶摇指标接口按 report 取单期，无 ann_date；用报告期末 + 滞后期近似，
    偏保守（宁可晚用、不偷看）。
    """
    today = date.today()
    reports: list[str] = []
    for y in range(today.year - years, today.year + 1):
        for q in (1, 2, 3, 4):
            if y == today.year and q > (today.month - 1) // 3 + 1:
                break
            reports.append(f"{y}-{q}")

    # 报告期结束日 + 滞后天数 → 近似可用日
    lag = {1: 45, 2: 60, 3: 45, 4: 90}
    end_md = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}

    rows = []
    for rep in reports:
        try:
            y_s, q_s = rep.split("-")
            y, q = int(y_s), int(q_s)
            m, d = end_md[q]
            end = date(y, m, d)
            ann = end + timedelta(days=lag[q])
            if ann > today + timedelta(days=30):
                continue
            ind = financial_indicators(code, rep)
            if ind.get("roe") is None:
                continue
            rows.append({
                "ann_date": ann.strftime("%Y%m%d"),
                "end_date": end.strftime("%Y%m%d"),
                "report": rep,
                "roe": ind["roe"],
                "roa": ind.get("roa"),
            })
        except Exception as err:  # noqa: BLE001
            logger.debug("roe %s %s: %s", code, rep, err)
            continue
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("ann_date").reset_index(drop=True)


def prices_snapshot(codes: list[str]) -> list[dict[str, Any]]:
    """行情快照（A 股）。"""
    if not codes:
        return []
    ths = ",".join(to_thscode(c) for c in codes)
    data = _get("/api/a-share/prices/snapshot", {"thscodes": ths})
    return data.get("item") or []


def etf_daily_bars(
    code: str,
    start: date | str,
    end: date | str | None = None,
) -> pd.DataFrame:
    """ETF 历史日线（基金行情接口，最长约 5 年/次）。"""
    if isinstance(start, str):
        start = date.fromisoformat(start[:10])
    if end is None:
        end = date.today()
    elif isinstance(end, str):
        end = date.fromisoformat(end[:10])

    frames = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=365 * 4), end)
        data = _get("/api/fund/market/historical", {
            "thscode": to_thscode(code),
            "interval": "1d",
            "start": _ms(cursor),
            "end": _ms(chunk_end + timedelta(days=1)) - 1,
        })
        items = data.get("item") or []
        if items:
            rows = []
            for it in items:
                # 字段名与 A 股 historical 对齐；若不同做兼容
                ms = it.get("date_ms") or it.get("timestamp")
                rows.append({
                    "date": _ms_to_date(ms),
                    "open": float(it.get("open_price") or it.get("open") or 0),
                    "high": float(it.get("high_price") or it.get("high") or 0),
                    "low": float(it.get("low_price") or it.get("low") or 0),
                    "close": float(it.get("close_price") or it.get("close") or 0),
                    "volume": float(it.get("volume") or 0),
                    "amount": float(it.get("turnover") or it.get("amount") or 0),
                })
            frames.append(pd.DataFrame(rows))
        if chunk_end >= end:
            break
        cursor = chunk_end + timedelta(days=1)

    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
