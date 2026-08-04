"""Tushare Pro 数据客户端。

无 token 或调用失败时返回空/None，由上层降级到 AKShare/腾讯。
所有财务字段按公告日(ann_date)做 point-in-time 对齐，禁止用 end_date 偷看。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

import pandas as pd

from ..runtime_settings import get_setting

logger = logging.getLogger(__name__)

_pro = None
_pro_token: str | None = None


def available() -> bool:
    return bool(str(get_setting("secrets.tushare_token")).strip())


def reset_client() -> None:
    global _pro, _pro_token
    _pro = None
    _pro_token = None


def _get_pro():
    global _pro, _pro_token
    token = str(get_setting("secrets.tushare_token")).strip()
    if not token:
        logger.warning("TUSHARE_TOKEN 未配置，因子基本面将不可用")
        return None
    if _pro is not None and _pro_token == token:
        return _pro
    try:
        import tushare as ts
        ts.set_token(token)
        _pro = ts.pro_api()
        _pro_token = token
        return _pro
    except Exception as err:  # noqa: BLE001
        logger.error("Tushare 初始化失败: %s", err)
        _pro = None
        _pro_token = None
        return None


def _to_ts_code(code: str) -> str:
    code = code.strip()
    if "." in code:
        return code
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _from_ts_code(ts_code: str) -> str:
    return ts_code.split(".")[0]


def _ymd(d: date | datetime | str) -> str:
    if isinstance(d, datetime):
        return d.strftime("%Y%m%d")
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    return str(d).replace("-", "")[:8]


@lru_cache(maxsize=8)
def trade_cal(start: str, end: str) -> tuple[str, ...]:
    """返回 [start, end] 内开市日 YYYYMMDD 元组。"""
    pro = _get_pro()
    if pro is None:
        return ()
    try:
        df = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
        if df is None or df.empty:
            return ()
        return tuple(sorted(df["cal_date"].astype(str).tolist()))
    except Exception as err:  # noqa: BLE001
        logger.warning("trade_cal 失败: %s", err)
        return ()


def daily_bars(code: str, start: date | str, end: date | str | None = None) -> pd.DataFrame:
    """日线 OHLCV，列: date, open, high, low, close, volume, amount。"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    end = end or date.today()
    try:
        df = pro.daily(ts_code=_to_ts_code(code), start_date=_ymd(start), end_date=_ymd(end))
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.sort_values("trade_date")
        out = pd.DataFrame({
            "date": pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d"),
            "open": df["open"].astype(float),
            "high": df["high"].astype(float),
            "low": df["low"].astype(float),
            "close": df["close"].astype(float),
            "volume": df["vol"].astype(float),
            "amount": df["amount"].astype(float) * 1000,  # 千元 → 元
        })
        return out.reset_index(drop=True)
    except Exception as err:  # noqa: BLE001
        logger.warning("daily_bars %s 失败: %s", code, err)
        return pd.DataFrame()


def daily_basic_range(code: str, start: date | str, end: date | str | None = None) -> pd.DataFrame:
    """每日估值: date, pe_ttm, pb, total_mv, turnover_rate。"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    end = end or date.today()
    try:
        df = pro.daily_basic(
            ts_code=_to_ts_code(code),
            start_date=_ymd(start),
            end_date=_ymd(end),
            fields="ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate",
        )
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.sort_values("trade_date")
        return pd.DataFrame({
            "date": pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d"),
            "pe_ttm": pd.to_numeric(df["pe_ttm"], errors="coerce"),
            "pb": pd.to_numeric(df["pb"], errors="coerce"),
            "total_mv": pd.to_numeric(df["total_mv"], errors="coerce"),
            "turnover_rate": pd.to_numeric(df["turnover_rate"], errors="coerce"),
        }).reset_index(drop=True)
    except Exception as err:  # noqa: BLE001
        logger.warning("daily_basic %s 失败: %s", code, err)
        return pd.DataFrame()


def fina_indicator_pit(code: str, asof: date | str | None = None) -> dict[str, Any]:
    """point-in-time 财务指标：仅使用 ann_date <= asof 的最新一期。"""
    pro = _get_pro()
    if pro is None:
        return {}
    asof = asof or date.today()
    asof_s = _ymd(asof)
    try:
        df = pro.fina_indicator(
            ts_code=_to_ts_code(code),
            fields="ts_code,ann_date,end_date,roe,roa,grossprofit_margin,debt_to_assets",
        )
        if df is None or df.empty:
            return {}
        df = df.dropna(subset=["ann_date"])
        df = df[df["ann_date"].astype(str) <= asof_s]
        if df.empty:
            return {}
        df = df.sort_values(["ann_date", "end_date"])
        row = df.iloc[-1]
        return {
            "ann_date": str(row["ann_date"]),
            "end_date": str(row.get("end_date", "")),
            "roe": _num(row.get("roe")),
            "roa": _num(row.get("roa")),
            "grossprofit_margin": _num(row.get("grossprofit_margin")),
            "debt_to_assets": _num(row.get("debt_to_assets")),
        }
    except Exception as err:  # noqa: BLE001
        logger.warning("fina_indicator %s 失败: %s", code, err)
        return {}


def fina_indicator_history(code: str) -> pd.DataFrame:
    """全历史财务指标（含 ann_date），供回测 asof merge。"""
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        df = pro.fina_indicator(
            ts_code=_to_ts_code(code),
            fields="ts_code,ann_date,end_date,roe,roa,grossprofit_margin,debt_to_assets",
        )
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.dropna(subset=["ann_date"]).copy()
        df["ann_date"] = df["ann_date"].astype(str)
        df["roe"] = pd.to_numeric(df["roe"], errors="coerce")
        return df.sort_values("ann_date").reset_index(drop=True)
    except Exception as err:  # noqa: BLE001
        logger.warning("fina_indicator_history %s 失败: %s", code, err)
        return pd.DataFrame()


def _num(value: Any) -> float | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def default_history_start(years: int = 3) -> date:
    return date.today() - timedelta(days=365 * years + 30)
