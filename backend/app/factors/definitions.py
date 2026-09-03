"""S3 单票因子定义（可向量化，便于回测）。

S2 基础：动量 / 低波 / 估值 / ROE
S3 加厚：反转 / 相对换手 / ROE 改善（成长代理）
另输出 size_proxy 供截面中性化（非打分因子）。

价量因子仅依赖 OHLCV；EP/BP 来自估值；质量/成长用 PIT 财务。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..runtime_settings import get_setting

# 参与综合打分的因子（顺序固定；size_proxy 不在此列）
FACTOR_NAMES = (
    # S2
    "mom_short",    # 短期动量
    "mom_mid",      # 中期动量
    "low_vol",      # 低波动（越大越好）
    "ep",           # 盈利收益率 E/P
    "bp",           # 账面市值比 B/P
    "quality_roe",  # 质量：ROE
    # S3
    "rev_1m",       # 一月反转（-近月收益，抄底/均值回归）
    "low_turn",     # 低换手（-相对成交活跃度）
    "growth_roe",   # ROE 改善（近两期差分）
)

# 中性化用控制变量（不进 score 等权）
CONTROL_NAMES = ("size_proxy",)


def board_of_code(code: str) -> str:
    """板块伪行业：用于截面中性化（无正式行业分类时的代理）。"""
    c = str(code).zfill(6)
    if c.startswith("688") or c.startswith("689"):
        return "STAR"
    if c.startswith("300") or c.startswith("301"):
        return "ChiNext"
    if c.startswith("60") or c.startswith("68"):
        return "SH"
    if c.startswith("00"):
        return "SZ"
    return "OTHER"


def compute_price_factors(bars: pd.DataFrame) -> pd.DataFrame:
    """输入列 date, close[, high, low, volume, amount]，输出价量因子。"""
    if bars is None or bars.empty:
        return pd.DataFrame()

    df = bars.copy()
    if "date" not in df.columns and "日期" in df.columns:
        df = df.rename(columns={"日期": "date", "收盘": "close", "最高": "high",
                                "最低": "low", "成交量": "volume", "开盘": "open",
                                "成交额": "amount"})
    df = df.sort_values("date").reset_index(drop=True)
    close = df["close"].astype(float)
    short = int(get_setting("factor.lookback_short"))
    mid = int(get_setting("factor.lookback_mid"))
    vol_w = int(get_setting("factor.vol_window"))
    try:
        rev_w = int(get_setting("factor.lookback_rev"))
    except Exception:  # noqa: BLE001
        rev_w = 20
    try:
        turn_w = int(get_setting("factor.turnover_window"))
    except Exception:  # noqa: BLE001
        turn_w = 20

    if rev_w in {short, mid}:
        raise ValueError(
            "factor.lookback_rev must differ from momentum windows; "
            "otherwise reversal exactly cancels momentum"
        )

    df["mom_short"] = close.pct_change(short)
    df["mom_mid"] = close.pct_change(mid)
    ret = close.pct_change()
    # 低波动：负的滚动标准差 → 波动越小分数越高
    df["low_vol"] = -ret.rolling(vol_w, min_periods=max(5, vol_w // 2)).std()

    # S3：一月反转（做多近期弱势）
    df["rev_1m"] = -close.pct_change(rev_w)

    # S3：相对换手 — 用成交额/均额 或 成交量/均量；取负 → 低换手偏好
    if "amount" in df.columns and pd.to_numeric(df["amount"], errors="coerce").fillna(0).sum() > 0:
        activity = pd.to_numeric(df["amount"], errors="coerce")
    else:
        activity = pd.to_numeric(df.get("volume", 0), errors="coerce")
    act_ma = activity.rolling(turn_w, min_periods=max(5, turn_w // 2)).mean()
    rel_turn = activity / act_ma.replace(0, np.nan)
    df["low_turn"] = -rel_turn

    # 规模代理：log(近窗均成交额 或 close) — 仅中性化，不进 FACTOR_NAMES
    size_base = act_ma if act_ma.notna().any() else close
    df["size_proxy"] = np.log(size_base.replace(0, np.nan).clip(lower=1e-6))

    return df


def attach_valuation(df: pd.DataFrame, basic: pd.DataFrame) -> pd.DataFrame:
    """按 date merge pe/pb → ep/bp。"""
    if df.empty:
        return df
    out = df.copy()
    if basic is None or basic.empty:
        out["ep"] = np.nan
        out["bp"] = np.nan
        return out
    cols = [c for c in ("date", "pe_ttm", "pb") if c in basic.columns]
    if "date" not in cols:
        out["ep"] = np.nan
        out["bp"] = np.nan
        return out
    b = basic[cols].copy()
    out = out.merge(b, on="date", how="left")
    pe = pd.to_numeric(out.get("pe_ttm"), errors="coerce")
    pb = pd.to_numeric(out.get("pb"), errors="coerce")
    out["ep"] = np.where(pe > 0, 1.0 / pe, np.nan)
    out["bp"] = np.where(pb > 0, 1.0 / pb, np.nan)
    return out


def attach_roe_pit(df: pd.DataFrame, fina: pd.DataFrame) -> pd.DataFrame:
    """asof merge：每个交易日使用 ann_date <= date 的最新 ROE；并算 growth_roe。"""
    out = df.copy()
    if out.empty:
        return out
    if fina is None or fina.empty or "ann_date" not in fina.columns:
        out["quality_roe"] = np.nan
        out["growth_roe"] = np.nan
        return out

    f = fina.dropna(subset=["ann_date", "roe"]).copy()
    f["ann_date"] = f["ann_date"].astype(str).str.replace("-", "")
    f["ann_dt"] = pd.to_datetime(f["ann_date"], format="%Y%m%d", errors="coerce")
    f = f.dropna(subset=["ann_dt"]).sort_values("ann_dt")
    f["roe"] = pd.to_numeric(f["roe"], errors="coerce")
    f = f.dropna(subset=["roe"])
    # 相对上一期 ROE 差分（成长代理）
    f["growth_roe"] = f["roe"].diff()

    left = out.copy()
    left["_dt"] = pd.to_datetime(left["date"])
    left = left.sort_values("_dt")
    merged = pd.merge_asof(
        left,
        f[["ann_dt", "roe", "growth_roe"]].rename(columns={"roe": "quality_roe"}),
        left_on="_dt",
        right_on="ann_dt",
        direction="backward",
    )
    merged = merged.drop(columns=["_dt", "ann_dt"], errors="ignore")
    return merged.reset_index(drop=True)


def compute_all_factors(
    bars: pd.DataFrame,
    basic: pd.DataFrame | None = None,
    fina: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """单票完整 S3 因子时间序列。"""
    df = compute_price_factors(bars)
    if df.empty:
        return df
    df = attach_valuation(df, basic if basic is not None else pd.DataFrame())
    df = attach_roe_pit(df, fina if fina is not None else pd.DataFrame())
    return df
