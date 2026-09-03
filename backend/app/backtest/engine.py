"""事件回测：池内等权买入持有 + S2 周频前 N 等权。

简化假设：
- 前一交易日收盘生成信号，下一交易日收盘成交
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
from .execution import (
    ExecutionCostModel,
    portfolio_value,
    rebalance_equal_weight,
)
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
    from ..runtime_settings import get_setting

    return (
        float(get_setting("trading.commission_rate"))
        + float(get_setting("trading.transfer_fee_rate"))
    )


def _roundtrip_fee_rate() -> float:
    from ..runtime_settings import get_setting

    return 2 * _fee_rate() + float(get_setting("trading.stamp_tax_rate"))


def _pivot_close(panel: pd.DataFrame) -> pd.DataFrame:
    """panel 长表 → 宽表 close[date x code]。"""
    df = panel.copy()
    df["date"] = pd.to_datetime(df["date"])
    return df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()


def _valuation_closes(closes: pd.DataFrame) -> pd.DataFrame:
    """Return point-in-time close prices suitable for valuation.

    A held security can legitimately have no row/quote on a portfolio date
    (suspension, data-source gap, or an asynchronous universe).  Valuation must
    use only information available on or before that date, so invalid prices
    are replaced by the security's last positive finite close.  ``ffill`` is
    deliberately column-wise and never looks into the future.
    """
    numeric = closes.apply(pd.to_numeric, errors="coerce")
    valid = np.isfinite(numeric) & numeric.gt(0)
    return numeric.where(valid).ffill()


def _mark_to_market(
    cash: float,
    holdings: dict[str, float],
    prices: pd.Series,
) -> float:
    """Value holdings from current-or-last-valid prices, failing on no basis."""
    return portfolio_value(cash, holdings, prices)


def _select_factor_codes(
    snapshot: pd.DataFrame,
    factor_names: tuple[str, ...] | list[str],
    top_n: int,
) -> list[str]:
    """Select only rows with finite values for every declared factor."""
    factors = tuple(dict.fromkeys(str(name) for name in factor_names if str(name)))
    if snapshot is None or snapshot.empty or not factors:
        return []

    numeric = pd.DataFrame(index=snapshot.index)
    for factor in factors:
        if factor in snapshot.columns:
            numeric[factor] = pd.to_numeric(snapshot[factor], errors="coerce")
        else:
            numeric[factor] = np.nan
    eligible_mask = np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1)
    if not eligible_mask.any():
        return []

    eligible = snapshot.loc[eligible_mask].copy()
    for factor in factors:
        eligible[factor] = numeric.loc[eligible_mask, factor]
    scored = composite_scores(
        eligible,
        factors,
        # A one-factor experiment is valid; for multiple factors eligibility
        # above already guarantees that every declared input is present.
        min_factors=len(factors),
    )
    return select_top_n(scored, n=top_n)


def _rebalance_dates(all_index, rebalance: str) -> set[pd.Timestamp]:
    """Map a calendar frequency to real observed trading dates.

    ``W-MON``/``W-FRI`` mean the first/last observed session of each
    Monday-to-Sunday week.  ``MS``/``ME`` mean the first/last observed session
    of each calendar month.  No synthetic resample labels are returned.
    """
    index = pd.DatetimeIndex(pd.to_datetime(all_index, errors="coerce"))
    index = index[~index.isna()].unique().sort_values()
    if index.empty:
        return set()

    frequency = str(rebalance or "").upper()
    if frequency in {"W-MON", "W-FRI"}:
        grouping_index = index.tz_localize(None) if index.tz is not None else index
        periods = grouping_index.to_period("W-SUN")
        keep = "first" if frequency == "W-MON" else "last"
    elif frequency in {"MS", "ME"}:
        grouping_index = index.tz_localize(None) if index.tz is not None else index
        periods = grouping_index.to_period("M")
        keep = "first" if frequency == "MS" else "last"
    else:
        raise ValueError(f"unsupported rebalance frequency: {rebalance}")

    grouped = pd.DataFrame({"date": index, "period": periods})
    selected = grouped.drop_duplicates("period", keep=keep)["date"]
    return {pd.Timestamp(value) for value in selected}


def run_equal_weight_buyhold(
    panel: pd.DataFrame,
    initial_cash: float | None = None,
    name: str = "pool_equal_weight",
) -> BacktestResult:
    """池内所有股票等权买入持有（锚）。"""
    from ..runtime_settings import get_setting

    initial_cash = (
        initial_cash if initial_cash is not None
        else float(get_setting("account.initial_cash"))
    )
    closes = _pivot_close(panel)
    if closes.empty or len(closes) < 2:
        eq = pd.Series(dtype=float)
        return BacktestResult(name=name, equity=eq, metrics=compute_metrics(eq))
    valuation_closes = _valuation_closes(closes)

    # 第一日等权建仓，之后漂移
    first = valuation_closes.iloc[0]
    valid = first.dropna()
    if valid.empty:
        eq = pd.Series(dtype=float)
        return BacktestResult(name=name, equity=eq, metrics=compute_metrics(eq))

    cost_model = ExecutionCostModel.from_settings()
    opening = rebalance_equal_weight(
        cash=float(initial_cash),
        holdings={},
        execution_prices=first,
        valuation_prices=first,
        target_codes=[str(code) for code in valid.index],
        cost_model=cost_model,
    )
    cash = opening.cash
    shares = opening.holdings
    equity = []
    idx = []
    for dt, row in valuation_closes.iterrows():
        equity.append(_mark_to_market(cash, shares, row))
        idx.append(dt)
    eq = pd.Series(equity, index=idx)
    metrics = mark_sample_ok(
        compute_metrics(eq, closed_trades=0),
        int(get_setting("race.min_trade_days")),
        int(get_setting("race.min_closed_trades")),
    )
    return BacktestResult(name=name, equity=eq, closed_trades=0, metrics=metrics,
                          holdings_log=[{
                              "date": str(idx[0])[:10],
                              "codes": list(shares),
                              "fees": opening.fees,
                              "turnover": opening.turnover,
                              "untradeable_codes": list(opening.untradeable_codes),
                          }])


def run_factor_weekly(
    panel: pd.DataFrame,
    top_n: int | None = None,
    initial_cash: float | None = None,
    name: str = "s2_factor_weekly",
    rebalance: str | None = None,
) -> BacktestResult:
    """每周按截面综合分选前 top_n 等权再平衡。"""
    from ..runtime_settings import get_setting

    top_n = top_n if top_n is not None else int(get_setting("factor.top_n"))
    initial_cash = (
        initial_cash if initial_cash is not None
        else float(get_setting("account.initial_cash"))
    )
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
    valuation_closes = _valuation_closes(closes)

    # 每个交易日截面打分
    dates = sorted(df["date"].unique())
    score_by_date: dict[Any, list[str]] = {}
    for dt in dates:
        snap = df[df["date"] == dt].copy()
        score_by_date[dt] = _select_factor_codes(snap, FACTOR_NAMES, top_n)

    rebal_set = _rebalance_dates(closes.index, rebalance)
    if not rebal_set:
        rebal_set = {closes.index[0]}
    cash = float(initial_cash)
    holdings: dict[str, float] = {}  # code -> shares
    equity_vals = []
    holdings_log = []
    closed_trades = 0
    cost_model = ExecutionCostModel.from_settings()

    previous_dt = None
    for dt, row in closes.iterrows():
        valuation_row = valuation_closes.loc[dt]
        # T 日收盘生成信号，只允许在下一个交易日价格执行。
        if previous_dt is not None and dt in rebal_set:
            target_codes = score_by_date.get(previous_dt, [])
            rebalanced = rebalance_equal_weight(
                cash=cash,
                holdings=holdings,
                execution_prices=row,
                valuation_prices=valuation_row,
                target_codes=target_codes,
                cost_model=cost_model,
            )
            cash = rebalanced.cash
            holdings = rebalanced.holdings
            closed_trades += rebalanced.closed_positions
            holdings_log.append({
                "date": str(dt)[:10],
                "signal_date": str(previous_dt)[:10],
                "codes": sorted(holdings),
                "target_codes": target_codes,
                "n": len(holdings),
                "fees": rebalanced.fees,
                "turnover": rebalanced.turnover,
                "untradeable_codes": list(rebalanced.untradeable_codes),
            })

        # 盯市
        equity_vals.append(_mark_to_market(cash, holdings, valuation_row))
        previous_dt = dt

    eq = pd.Series(equity_vals, index=closes.index)
    # 基准超额：池内等权
    bench = run_equal_weight_buyhold(panel, initial_cash=initial_cash)
    metrics = compute_metrics(eq, closed_trades=closed_trades, benchmark=bench.equity)
    metrics = mark_sample_ok(
        metrics,
        int(get_setting("race.min_trade_days")),
        int(get_setting("race.min_closed_trades")),
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
    return pd.Timestamp(dt) in _rebalance_dates(all_index, "W-MON")
