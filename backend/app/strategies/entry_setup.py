"""Versioned, deterministic entry-candidate contract for the LLM pipeline.

This module does not predict returns and does not create orders.  It turns the
existing factor and technical facts into an auditable shortlist so that an LLM
must reason about a concrete setup instead of defaulting every symbol to a
generic ``hold``.
"""
from __future__ import annotations

from typing import Any

from ..runtime_settings import get_setting

CONTRACT_VERSION = "entry_setup_v1"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def assess_entry_setup(
    factsheet: dict[str, Any] | None,
    *,
    top_pct: float | None = None,
    watch_pct: float | None = None,
    min_confirmations: int | None = None,
    max_rsi: float | None = None,
) -> dict[str, Any]:
    """Classify a 5–20 trading-day long candidate from frozen facts only."""
    sheet = factsheet or {}
    factors = sheet.get("factors") or {}
    technical = sheet.get("technical") or {}
    valuation = sheet.get("valuation") or {}

    top_pct = float(
        get_setting("signal.entry_top_pct") if top_pct is None else top_pct)
    watch_pct = float(
        get_setting("signal.entry_watch_pct") if watch_pct is None else watch_pct)
    watch_pct = max(watch_pct, top_pct)
    min_confirmations = int(
        get_setting("signal.entry_min_confirmations")
        if min_confirmations is None else min_confirmations)
    max_rsi = float(
        get_setting("signal.entry_max_rsi") if max_rsi is None else max_rsi)

    rank = _number(factors.get("rank"))
    universe = _number(factors.get("universe_size"))
    factor_score = _number(factors.get("score"))
    rank_pct = rank / universe if rank and universe and universe > 0 else None

    close = _number(technical.get("close"))
    ma20 = _number(technical.get("ma20"))
    macd = _number(technical.get("macd"))
    rsi = _number(technical.get("rsi14"))
    mom_short = _number(factors.get("mom_short"))
    mom_mid = _number(factors.get("mom_mid"))

    confirmations: dict[str, bool | None] = {
        "mom_short_positive": None if mom_short is None else mom_short > 0,
        "mom_mid_positive": None if mom_mid is None else mom_mid > 0,
        "close_above_ma20": (
            None if close is None or ma20 is None else close > ma20),
        "macd_positive": None if macd is None else macd > 0,
    }
    known_confirmations = sum(value is not None for value in confirmations.values())
    positive_confirmations = sum(value is True for value in confirmations.values())

    hard_blockers: list[str] = []
    cautions: list[str] = []
    if rank_pct is None or factor_score is None:
        hard_blockers.append("缺少可比较的因子排名或综合分")
    if known_confirmations < min_confirmations:
        hard_blockers.append(
            f"动量/趋势确认数据不足 {known_confirmations}/{min_confirmations}")
    if rsi is not None and rsi >= max_rsi:
        hard_blockers.append(f"RSI14={rsi:.1f} 达到过热线 {max_rsi:.1f}")

    pe = _number(valuation.get("pe"))
    if pe is None:
        cautions.append("PE 缺失，估值不作为通过证据")
    elif pe <= 0:
        cautions.append(f"PE={pe:.2f} 非正，盈利质量需单独复核")
    elif pe > 100:
        cautions.append(f"PE={pe:.2f} 较高，需有增长证据支持")
    if rsi is None:
        cautions.append("RSI14 缺失，无法检查过热")

    factor_gate = bool(
        rank_pct is not None and rank_pct <= top_pct
        and factor_score is not None and factor_score > 0)
    confirmation_gate = positive_confirmations >= min_confirmations
    actionable = factor_gate and confirmation_gate and not hard_blockers

    if actionable:
        status = "actionable"
    elif (
        rank_pct is not None and rank_pct <= watch_pct
        and factor_score is not None and factor_score > 0
        and positive_confirmations >= max(1, min_confirmations - 1)
        and not any("数据不足" in item for item in hard_blockers)
    ):
        status = "watch"
    elif rank_pct is None or factor_score is None or known_confirmations < min_confirmations:
        status = "data_insufficient"
    else:
        status = "rejected"

    score = 0.0
    if rank_pct is not None:
        score += 40.0 if rank_pct <= top_pct else (20.0 if rank_pct <= watch_pct else 0.0)
    score += 12.5 * positive_confirmations
    if rsi is not None and 35 <= rsi < max_rsi:
        score += 10.0
    score = min(score, 100.0)

    reasons = [
        (
            f"因子排名 {int(rank)}/{int(universe)} ({rank_pct:.1%})"
            if rank_pct is not None else "因子排名不可用"
        ),
        f"动量/趋势确认 {positive_confirmations}/{len(confirmations)}",
    ]
    if status == "actionable":
        reasons.append("通过确定性候选门禁，进入多模型合议")
    elif status == "watch":
        reasons.append("接近门禁但确认不足，仅列观察")
    elif status == "data_insufficient":
        reasons.append("关键数据不足，禁止生成新建仓计划")
    else:
        reasons.append("未通过排名或趋势门禁，禁止生成新建仓计划")

    return {
        "version": CONTRACT_VERSION,
        "classification": "provisional",
        "horizon_trading_days": [5, 20],
        "status": status,
        "actionable": actionable,
        "setup_score": round(score, 2),
        "factor_rank_pct": None if rank_pct is None else round(rank_pct, 6),
        "positive_confirmations": positive_confirmations,
        "known_confirmations": known_confirmations,
        "confirmations": confirmations,
        "hard_blockers": hard_blockers,
        "cautions": cautions,
        "reasons": reasons,
        "policy": {
            "entry_top_pct": top_pct,
            "entry_watch_pct": watch_pct,
            "entry_min_confirmations": min_confirmations,
            "entry_max_rsi": max_rsi,
        },
    }
