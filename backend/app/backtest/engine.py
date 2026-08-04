"""事件回测：池内等权买入持有 + S2 周频前 N 等权。

简化假设（第一季可接受）：
- 调仓日按收盘价成交
- 不考虑涨跌停无法成交（历史回测后续可加）
- 费用按 settings 佣金/印花税近似（双边）
- 整手忽略，用金额权重（便于向量化）；与实盘模拟撮合分开记账
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..config import settings
from ..factors.definitions import FACTOR_NAMES
from ..factors.score import composite_scores, select_top_n
from .metrics import compute_metrics, mark_sample_ok


@dataclass
class BacktestResult:
    name: str
    equity: pd.Series
    holdings_log: list[dict[str, Any]] = field(default_factory=list)
    closed_trades: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        eq = self.equity
        curve = []
        if eq is not None and len(eq):
            for dt, val in eq.items():
                curve.append({"date": str(dt)[:10], "equity": round(float(val), 2)})
        return {
            "name": self.name,
            "closed_trades": self.closed_trades,
            "metrics": self.metrics,
            "equity_curve": curve,
            "holdings_log": self.holdings_log[-50:],  # 最近 50 次调仓
        }


def _fee_rate() -> float:
    # 近似单边：佣金 + 过户；卖出再加印花税 → 换手时用往返
    return settings.commission_rate + settings.transfer_fee_rate


def _roundtrip_fee_rate() -> float:
    return 2 * _fee_rate() + settings.stamp_tax_rate


def _pivot_close(panel: pd.DataFrame) -> pd.DataFrame:
    """panel 长表 → 宽表 close[date x code]。"""
    df = panel.copy()
    df["date"] = pd.to_datetime(df["date"])
    return df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()


def run_equal_weight_buyhold(
    panel: pd.DataFrame,
    initial_cash: float | None = None,
    name: str = "pool_equal_weight",
) -> BacktestResult:
    """池内所有股票等权买入持有（锚）。"""
    initial_cash = initial_cash or settings.initial_cash
    closes = _pivot_close(panel)
    if closes.empty or len(closes) < 2:
        eq = pd.Series(dtype=float)
        return BacktestResult(name=name, equity=eq, metrics=compute_metrics(eq))

    # 第一日等权建仓，之后漂移
    first = closes.iloc[0]
    valid = first.dropna()
    if valid.empty:
        eq = pd.Series(dtype=float)
        return BacktestResult(name=name, equity=eq, metrics=compute_metrics(eq))

    w = 1.0 / len(valid)
    # 建仓费
    cash_deployed = initial_cash * (1 - _fee_rate())
    shares = {c: cash_deployed * w / float(first[c]) for c in valid.index}
    equity = []
    idx = []
    for dt, row in closes.iterrows():
        val = 0.0
        for c, q in shares.items():
            px = row.get(c)
            if px is not None and np.isfinite(px):
                val += q * float(px)
        equity.append(val)
        idx.append(dt)
    eq = pd.Series(equity, index=idx)
    metrics = mark_sample_ok(
        compute_metrics(eq, closed_trades=0),
        settings.race_min_trade_days,
        settings.race_min_closed_trades,
    )
    return BacktestResult(name=name, equity=eq, closed_trades=0, metrics=metrics,
                          holdings_log=[{"date": str(idx[0])[:10], "codes": list(shares)}])


def run_factor_weekly(
    panel: pd.DataFrame,
    top_n: int | None = None,
    initial_cash: float | None = None,
    name: str = "s2_factor_weekly",
    rebalance: str | None = None,
) -> BacktestResult:
    """每周按截面综合分选前 top_n 等权再平衡。"""
    top_n = top_n or settings.factor_top_n
    initial_cash = initial_cash or settings.initial_cash
    rebalance = rebalance or settings.factor_rebalance

    if panel is None or panel.empty:
        eq = pd.Series(dtype=float)
        return BacktestResult(name=name, equity=eq, metrics=compute_metrics(eq))

    df = panel.copy()
    df["date"] = pd.to_datetime(df["date"])
    closes = _pivot_close(df)
    if closes.empty:
        eq = pd.Series(dtype=float)
        return BacktestResult(name=name, equity=eq, metrics=compute_metrics(eq))

    # 每个交易日截面打分
    dates = sorted(df["date"].unique())
    score_by_date: dict[Any, list[str]] = {}
    for dt in dates:
        snap = df[df["date"] == dt].copy()
        scored = composite_scores(snap, FACTOR_NAMES)
        score_by_date[dt] = select_top_n(scored, n=top_n)

    # 再平衡日：周频
    rebal_dates = set(pd.Series(closes.index).sort_values())
    # 用 resample 标记每周第一个交易日
    week_first = closes.resample(rebalance).first().dropna(how="all")
    rebal_set = set(week_first.index)
    # 对齐到 closes 索引中真实存在的日期
    rebal_set = {d for d in closes.index if d in rebal_set or _is_week_start(d, closes.index)}
    if not rebal_set:
        rebal_set = {closes.index[0]}
    # 确保首日调仓
    rebal_set.add(closes.index[0])

    cash = float(initial_cash)
    holdings: dict[str, float] = {}  # code -> shares
    equity_vals = []
    holdings_log = []
    closed_trades = 0
    fee_rt = _roundtrip_fee_rate()

    for dt, row in closes.iterrows():
        if dt in rebal_set or (not holdings and dt == closes.index[0]):
            target_codes = score_by_date.get(dt) or score_by_date.get(
                max((d for d in score_by_date if d <= dt), default=dt), []
            )
            # 当前市值
            port_val = cash
            for c, q in holdings.items():
                px = row.get(c)
                if px is not None and np.isfinite(px):
                    port_val += q * float(px)

            # 统计平仓：旧持仓不在新目标中
            new_set = set(target_codes)
            for c in list(holdings.keys()):
                if c not in new_set:
                    closed_trades += 1

            # 清仓为现金后等权买入（扣近似换手费）
            port_val *= (1 - fee_rt * 0.5)  # 半程摩擦近似
            holdings = {}
            cash = 0.0
            buyable = []
            for c in target_codes:
                px = row.get(c)
                if px is not None and np.isfinite(px) and float(px) > 0:
                    buyable.append(c)
            if buyable:
                w = port_val / len(buyable)
                for c in buyable:
                    px = float(row[c])
                    holdings[c] = w / px
            else:
                cash = port_val
            holdings_log.append({
                "date": str(dt)[:10],
                "codes": buyable,
                "n": len(buyable),
            })

        # 盯市
        val = cash
        for c, q in holdings.items():
            px = row.get(c)
            if px is not None and np.isfinite(px):
                val += q * float(px)
        equity_vals.append(val)

    eq = pd.Series(equity_vals, index=closes.index)
    # 基准超额：池内等权
    bench = run_equal_weight_buyhold(panel, initial_cash=initial_cash)
    metrics = compute_metrics(eq, closed_trades=closed_trades, benchmark=bench.equity)
    metrics = mark_sample_ok(
        metrics, settings.race_min_trade_days, settings.race_min_closed_trades
    )
    return BacktestResult(
        name=name,
        equity=eq,
        holdings_log=holdings_log,
        closed_trades=closed_trades,
        metrics=metrics,
    )


def _is_week_start(dt, all_index) -> bool:
    """若该日是 all_index 中当周第一个交易日。"""
    week = pd.Timestamp(dt).to_period("W-SUN")
    same = [d for d in all_index if pd.Timestamp(d).to_period("W-SUN") == week]
    return bool(same) and same[0] == dt
