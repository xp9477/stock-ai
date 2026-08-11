"""按结构化规格跑回测：截面因子周/月调仓 + 简单日线事件。"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..factors.score import composite_scores, select_top_n
from ..runtime_settings import get_setting
from .engine import (
    BacktestResult,
    _is_week_start,
    _pivot_close,
    _roundtrip_fee_rate,
    run_equal_weight_buyhold,
)
from .metrics import compute_metrics, mark_sample_ok


def run_spec_backtest(
    panel: pd.DataFrame,
    spec: dict[str, Any],
    initial_cash: float | None = None,
) -> BacktestResult:
    initial_cash = initial_cash or float(get_setting("account.initial_cash"))
    name = str(spec.get("name") or "spec_strategy")
    mode = spec.get("mode") or "factor_cross_section"

    if panel is None or panel.empty:
        eq = pd.Series(dtype=float)
        return BacktestResult(name=name, equity=eq, metrics=compute_metrics(eq))

    if mode == "equal_weight":
        res = run_equal_weight_buyhold(panel, initial_cash=initial_cash, name=name)
        res = _apply_events_to_result(panel, res, spec, initial_cash)
        return res

    return _run_factor_spec(panel, spec, initial_cash, name)


def _run_factor_spec(
    panel: pd.DataFrame,
    spec: dict[str, Any],
    initial_cash: float,
    name: str,
) -> BacktestResult:
    factors = list(spec.get("factors") or [])
    top_n = int(spec.get("top_n") or 10)
    rebalance = str(spec.get("rebalance") or "W-MON")
    events = list(spec.get("events") or [])

    df = panel.copy()
    df["date"] = pd.to_datetime(df["date"])
    if "close" not in df.columns and "收盘" in df.columns:
        df = df.rename(columns={"收盘": "close"})
    closes = _pivot_close(df)
    if closes.empty:
        eq = pd.Series(dtype=float)
        return BacktestResult(name=name, equity=eq, metrics=compute_metrics(eq))

    dates = sorted(df["date"].unique())
    score_by_date: dict[Any, list[str]] = {}
    for dt in dates:
        snap = df[df["date"] == dt].copy()
        if factors:
            scored = composite_scores(snap, factors)
        else:
            from ..factors.definitions import FACTOR_NAMES
            scored = composite_scores(snap, FACTOR_NAMES)
        score_by_date[dt] = select_top_n(scored, n=top_n)

    if rebalance.startswith("W"):
        rebal_set = {d for d in closes.index if _is_week_start(d, closes.index)}
    else:
        # 月频：每月第一个交易日
        rebal_set = set()
        seen_months: set[str] = set()
        for d in closes.index:
            key = pd.Timestamp(d).strftime("%Y-%m")
            if key not in seen_months:
                seen_months.add(key)
                rebal_set.add(d)
    cash = float(initial_cash)
    holdings: dict[str, float] = {}
    cost: dict[str, float] = {}  # 成本价
    entry_i: dict[str, int] = {}  # 建仓日索引（交易日序号）
    equity_vals = []
    holdings_log = []
    closed_trades = 0
    fee_rt = _roundtrip_fee_rate()

    stop = None
    take = None
    ma_win = None
    hold_max = None
    for ev in events:
        if ev.get("type") == "stop_loss_pct":
            stop = float(ev["value"])
        elif ev.get("type") == "take_profit_pct":
            take = float(ev["value"])
        elif ev.get("type") == "ma_exit":
            ma_win = int(ev.get("window") or 20)
        elif ev.get("type") == "hold_max_days":
            hold_max = int(ev.get("days") or 20)

    # 预计算均线
    ma = {}
    if ma_win:
        for c in closes.columns:
            ma[c] = closes[c].rolling(ma_win, min_periods=max(2, ma_win // 2)).mean()

    day_i = 0
    previous_dt = None
    previous_row = None
    for dt, row in closes.iterrows():
        # 事件信号基于上一交易日收盘，在当前交易日价格执行。
        for c in list(holdings.keys()):
            signal_px = previous_row.get(c) if previous_row is not None else None
            execution_px = row.get(c)
            if (signal_px is None or execution_px is None
                    or not np.isfinite(signal_px) or not np.isfinite(execution_px)
                    or float(execution_px) <= 0):
                continue
            signal_px = float(signal_px)
            execution_px = float(execution_px)
            basis = cost.get(c) or signal_px
            pnl = signal_px / basis - 1 if basis else 0
            exit_pos = False
            if stop is not None and pnl <= stop:
                exit_pos = True
            if take is not None and pnl >= take:
                exit_pos = True
            if hold_max is not None and c in entry_i and (day_i - entry_i[c]) >= hold_max:
                exit_pos = True
            if ma_win and c in ma and previous_dt is not None:
                mv = (
                    ma[c].get(previous_dt)
                    if hasattr(ma[c], "get")
                    else ma[c].loc[previous_dt] if previous_dt in ma[c].index else None
                )
                try:
                    if mv is not None and np.isfinite(mv) and signal_px < float(mv):
                        exit_pos = True
                except Exception:  # noqa: BLE001
                    pass
            if exit_pos:
                cash += holdings[c] * execution_px * (1 - fee_rt * 0.25)
                del holdings[c]
                cost.pop(c, None)
                entry_i.pop(c, None)
                closed_trades += 1

        if previous_dt is not None and dt in rebal_set:
            target_codes = score_by_date.get(previous_dt) or []

            port_val = cash
            for c, q in holdings.items():
                px = row.get(c)
                if px is not None and np.isfinite(px):
                    port_val += q * float(px)

            new_set = set(target_codes)
            for c in list(holdings.keys()):
                if c not in new_set:
                    closed_trades += 1

            port_val *= (1 - fee_rt * 0.5)
            holdings = {}
            cost = {}
            entry_i = {}
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
                    cost[c] = px
                    entry_i[c] = day_i
            else:
                cash = port_val
            holdings_log.append({
                "date": str(dt)[:10],
                "signal_date": str(previous_dt)[:10],
                "codes": buyable,
                "n": len(buyable),
            })
        day_i += 1

        val = cash
        for c, q in holdings.items():
            px = row.get(c)
            if px is not None and np.isfinite(px):
                val += q * float(px)
        equity_vals.append(val)
        previous_dt = dt
        previous_row = row

    eq = pd.Series(equity_vals, index=closes.index)
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


def _apply_events_to_result(
    panel: pd.DataFrame,
    res: BacktestResult,
    spec: dict[str, Any],
    initial_cash: float,
) -> BacktestResult:
    """等权模式若带事件，走因子路径会更一致；此处直接返回原结果并附 notes。"""
    if not spec.get("events"):
        return res
    # 对等权+事件：复用因子路径但 factors 空 + equal 目标 = 全池
    # 简化：用 panel 全代码当 top_n 极大
    s2 = dict(spec)
    s2["mode"] = "factor_cross_section"
    s2["factors"] = []
    s2["top_n"] = 500
    # 空 factors 时 composite 用全 FACTOR_NAMES — 改 equal：强制每天目标=全部有价代码
    return _run_equal_with_events(panel, s2, initial_cash, str(spec.get("name") or "eq_events"))


def _run_equal_with_events(
    panel: pd.DataFrame,
    spec: dict[str, Any],
    initial_cash: float,
    name: str,
) -> BacktestResult:
    """等权持有全池，叠加事件出清。"""
    df = panel.copy()
    df["date"] = pd.to_datetime(df["date"])
    if "close" not in df.columns and "收盘" in df.columns:
        df = df.rename(columns={"收盘": "close"})
    closes = _pivot_close(df)
    if closes.empty:
        eq = pd.Series(dtype=float)
        return BacktestResult(name=name, equity=eq, metrics=compute_metrics(eq))

    events = list(spec.get("events") or [])
    stop = take = None
    ma_win = None
    for ev in events:
        if ev.get("type") == "stop_loss_pct":
            stop = float(ev["value"])
        elif ev.get("type") == "take_profit_pct":
            take = float(ev["value"])
        elif ev.get("type") == "ma_exit":
            ma_win = int(ev.get("window") or 20)

    ma = {}
    if ma_win:
        for c in closes.columns:
            ma[c] = closes[c].rolling(ma_win, min_periods=max(2, ma_win // 2)).mean()

    from .engine import _fee_rate
    fee = _fee_rate()
    fee_rt = _roundtrip_fee_rate()
    first = closes.iloc[0]
    valid = first.dropna()
    if valid.empty:
        eq = pd.Series(dtype=float)
        return BacktestResult(name=name, equity=eq, metrics=compute_metrics(eq))

    cash_deployed = initial_cash * (1 - fee)
    w = cash_deployed / len(valid)
    holdings = {c: w / float(first[c]) for c in valid.index}
    cost = {c: float(first[c]) for c in valid.index}
    cash = 0.0
    closed = 0
    equity_vals = []
    previous_dt = None
    previous_row = None
    for dt, row in closes.iterrows():
        for c in list(holdings.keys()):
            signal_px = previous_row.get(c) if previous_row is not None else None
            execution_px = row.get(c)
            if (signal_px is None or execution_px is None
                    or not np.isfinite(signal_px) or not np.isfinite(execution_px)):
                continue
            signal_px = float(signal_px)
            execution_px = float(execution_px)
            basis = cost.get(c) or signal_px
            pnl = signal_px / basis - 1
            exit_pos = False
            if stop is not None and pnl <= stop:
                exit_pos = True
            if take is not None and pnl >= take:
                exit_pos = True
            if ma_win and c in ma and previous_dt in ma[c].index:
                mv = ma[c].loc[previous_dt]
                if np.isfinite(mv) and signal_px < float(mv):
                    exit_pos = True
            if exit_pos:
                cash += holdings[c] * execution_px * (1 - fee_rt * 0.25)
                del holdings[c]
                cost.pop(c, None)
                closed += 1
        val = cash
        for c, q in holdings.items():
            px = row.get(c)
            if px is not None and np.isfinite(px):
                val += q * float(px)
        equity_vals.append(val)
        previous_dt = dt
        previous_row = row

    eq = pd.Series(equity_vals, index=closes.index)
    metrics = mark_sample_ok(
        compute_metrics(eq, closed_trades=closed),
        int(get_setting("race.min_trade_days")),
        int(get_setting("race.min_closed_trades")),
    )
    return BacktestResult(name=name, equity=eq, closed_trades=closed, metrics=metrics)
