"""多票因子面板：扶摇主源 → S3 因子 → 截面快照。

不做 AKShare/东财降级；无 FUYAO_API_KEY 时明确失败。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterable

import numpy as np
import pandas as pd

from ..data import fuyao_client as fuyao
from .definitions import FACTOR_NAMES, compute_all_factors
from .score import composite_scores

logger = logging.getLogger(__name__)


def load_stock_factors(
    code: str,
    start: date | None = None,
    end: date | None = None,
    use_tushare: bool = False,  # 保留参数兼容旧调用，忽略
    roe_years: int = 4,
    fetch_latest_valuation: bool = True,
) -> pd.DataFrame:
    """单票因子时间序列（含 code 列）。主源：扶摇。"""
    del use_tushare
    if not fuyao.available():
        raise RuntimeError("FUYAO_API_KEY 未配置，无法构建因子面板")

    start = start or (date.today() - timedelta(days=365 * 3 + 60))
    end = end or date.today()

    bars = fuyao.daily_bars(code, start, end)
    if bars.empty:
        logger.warning("扶摇无日 K: %s", code)
        return pd.DataFrame()

    # 估值：扶摇仅最新快照 → 只挂在序列最后一日，历史 EP/BP 保持 NaN（防把今日 PE 填进过去）
    basic = pd.DataFrame()
    if fetch_latest_valuation:
        try:
            snap = fuyao.valuation_snapshot([code])
            if not snap.empty:
                last_date = bars["date"].iloc[-1]
                pe = snap.iloc[0].get("pe_ttm")
                pb = snap.iloc[0].get("pb")
                basic = pd.DataFrame([{
                    "date": last_date,
                    "pe_ttm": pe if pe is not None else np.nan,
                    "pb": pb if pb is not None else np.nan,
                }])
        except Exception as err:  # noqa: BLE001
            logger.warning("valuation %s: %s", code, err)

    fina = pd.DataFrame()
    try:
        fina = fuyao.roe_history(code, years=roe_years)
    except Exception as err:  # noqa: BLE001
        logger.warning("roe_history %s: %s", code, err)

    df = compute_all_factors(bars, basic, fina)
    df["code"] = code
    return df


def build_factor_panel(
    codes: Iterable[str],
    start: date | None = None,
    end: date | None = None,
    use_tushare: bool = False,
    roe_years: int = 4,
    fetch_latest_valuation: bool = True,
) -> pd.DataFrame:
    """长表：date, code, factors..."""
    del use_tushare
    frames = []
    for code in codes:
        try:
            f = load_stock_factors(
                code, start=start, end=end, roe_years=roe_years,
                fetch_latest_valuation=fetch_latest_valuation)
            if not f.empty:
                frames.append(f)
        except Exception as err:  # noqa: BLE001
            logger.warning("build_factor_panel %s: %s", code, err)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def latest_factor_snapshot(
    codes: Iterable[str],
    asof: date | None = None,
    use_tushare: bool = False,
) -> pd.DataFrame:
    """最新截面 + 综合分。"""
    del use_tushare
    asof = asof or date.today()
    start = asof - timedelta(days=120)
    # Live ranking only needs the latest two reported ROE observations.  One
    # year is sufficient and avoids fetching four years of quarterly reports
    # for every twice-daily decision run.  Historical panel builders retain the
    # four-year default.
    panel = build_factor_panel(
        codes, start=start, end=asof, roe_years=1,
        fetch_latest_valuation=False,
    )
    if panel.empty:
        return pd.DataFrame()
    panel["date"] = panel["date"].astype(str)
    asof_s = asof.strftime("%Y-%m-%d")
    panel = panel[panel["date"] <= asof_s]
    if panel.empty:
        return pd.DataFrame()
    idx = panel.groupby("code")["date"].idxmax()
    snap = panel.loc[idx].reset_index(drop=True)

    # 最新截面补全估值（全员一次 snapshot）
    try:
        code_list = snap["code"].astype(str).tolist()
        val = fuyao.valuation_snapshot(code_list)
        if not val.empty:
            val = val.set_index("code")
            for i, row in snap.iterrows():
                c = str(row["code"])
                if c not in val.index:
                    continue
                pe = val.loc[c, "pe_ttm"]
                pb = val.loc[c, "pb"]
                if pe is not None and pe == pe and pe > 0:
                    snap.at[i, "ep"] = 1.0 / float(pe)
                if pb is not None and pb == pb and pb > 0:
                    snap.at[i, "bp"] = 1.0 / float(pb)
    except Exception as err:  # noqa: BLE001
        logger.warning("snapshot valuation batch: %s", err)

    return composite_scores(snap, FACTOR_NAMES)
