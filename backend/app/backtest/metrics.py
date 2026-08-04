"""回测/前瞻统一绩效指标（胜率只展示，不作决策）。"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def compute_metrics(
    equity: pd.Series,
    closed_trades: int = 0,
    benchmark: pd.Series | None = None,
    risk_free: float = 0.0,
) -> dict[str, Any]:
    """equity: 按交易日索引的净值序列（或总资产）。"""
    if equity is None or len(equity) < 2:
        return {
            "n_days": 0,
            "total_return": 0.0,
            "ann_return": 0.0,
            "ann_vol": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "calmar": 0.0,
            "closed_trades": closed_trades,
            "excess_return": None,
            "sample_ok": False,
        }

    eq = equity.astype(float).dropna()
    rets = eq.pct_change().dropna()
    n = len(eq)
    total_return = float(eq.iloc[-1] / eq.iloc[0] - 1) if eq.iloc[0] else 0.0
    ann_factor = 252.0 / max(n - 1, 1)
    ann_return = float((1 + total_return) ** ann_factor - 1) if total_return > -1 else -1.0
    ann_vol = float(rets.std() * math.sqrt(252)) if len(rets) else 0.0
    sharpe = float((rets.mean() * 252 - risk_free) / (rets.std() * math.sqrt(252))) \
        if len(rets) and rets.std() > 1e-12 else 0.0

    peak = eq.cummax()
    dd = eq / peak - 1.0
    max_drawdown = float(dd.min()) if len(dd) else 0.0
    calmar = float(ann_return / abs(max_drawdown)) if max_drawdown < -1e-12 else 0.0

    excess = None
    if benchmark is not None and len(benchmark) >= 2:
        b = benchmark.reindex(eq.index).ffill().dropna()
        aligned = eq.reindex(b.index).dropna()
        b = b.reindex(aligned.index)
        if len(aligned) >= 2 and b.iloc[0]:
            excess = float(aligned.iloc[-1] / aligned.iloc[0] - b.iloc[-1] / b.iloc[0])

    return {
        "n_days": n,
        "total_return": round(total_return, 6),
        "ann_return": round(ann_return, 6),
        "ann_vol": round(ann_vol, 6),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_drawdown, 6),
        "calmar": round(calmar, 4),
        "closed_trades": closed_trades,
        "excess_return": None if excess is None else round(excess, 6),
        "sample_ok": False,  # 由调用方按 60 日 ∧ 100 笔填充
    }


def mark_sample_ok(metrics: dict[str, Any], min_days: int, min_trades: int) -> dict[str, Any]:
    out = dict(metrics)
    out["sample_ok"] = (
        int(out.get("n_days") or 0) >= min_days
        and int(out.get("closed_trades") or 0) >= min_trades
    )
    return out
