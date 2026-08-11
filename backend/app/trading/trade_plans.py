"""Auditable trade-plan lifecycle primitives.

This module intentionally contains no broker calls.  An analytical decision
becomes a conditional candidate, while deterministic gates decide whether the
candidate needs review, is invalid, or may proceed to human approval.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.orm import Session

from ..models import Decision, GateCheck, TradePlan, utc_now


PASS = "pass"
REVIEW_REQUIRED = "review_required"
BLOCKED_QUOTE = "blocked_quote"
INVALIDATED_PRICE = "invalidated_price"
INVALIDATED_CONDITION = "invalidated_condition"
EXPIRED = "expired"

_NON_PASS_PLAN_STATUSES = {
    REVIEW_REQUIRED,
    BLOCKED_QUOTE,
    "blocked_information",
    INVALIDATED_PRICE,
    INVALIDATED_CONDITION,
    EXPIRED,
}


class IdempotencyConflict(ValueError):
    """A request key was already consumed by a different domain operation."""


@dataclass(frozen=True)
class PriceGateResult:
    """Deterministic outcome of the pre-trade price gate.

    All ``*_pct`` values are decimal ratios: ``0.05`` means five percent.
    """

    outcome: str
    reason_code: str
    reason: str
    checked_at: datetime
    current_price: float | None = None
    reference_price: float | None = None
    max_buy_price: float | None = None
    signal_price_deviation_pct: float | None = None
    opening_gap_pct: float | None = None
    dynamic_gap_threshold_pct: float | None = None
    quote_asof: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _records(data: Sequence[Mapping[str, Any]] | Any) -> list[Mapping[str, Any]]:
    """Convert a records sequence or pandas-like DataFrame without importing pandas."""
    if hasattr(data, "to_dict"):
        try:
            converted = data.to_dict("records")
            if isinstance(converted, list):
                return converted
        except TypeError:
            pass
    return list(data)


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_float(value: Any) -> float | None:
    number = _finite_float(value)
    return number if number is not None and number > 0 else None


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _percentile_linear(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def historical_gap_threshold(
    daily_bars: Sequence[Mapping[str, Any]] | Any,
    *,
    lookback: int = 60,
    percentile: float = 0.95,
    min_samples: int = 40,
    completed_before: date | datetime | str | None = None,
) -> float | None:
    """Return the empirical absolute opening-gap threshold.

    Only rows explicitly marked completed (``completed``/``is_completed`` is
    not false) are considered.  When ``completed_before`` is supplied, rows on
    or after that date are excluded; rows without a parseable date are also
    excluded because their completion cannot be proven.  Callers that omit the
    cutoff are responsible for passing completed historical bars only.

    A row may provide ``prev_close`` directly.  Otherwise the previous
    completed row's close is used.  Invalid rows are skipped and ``None`` is
    returned when fewer than ``min_samples`` valid gaps remain.
    """
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if min_samples <= 0 or min_samples > lookback:
        raise ValueError("min_samples must be between 1 and lookback")
    if not 0.0 <= percentile <= 1.0:
        raise ValueError("percentile must be between 0 and 1")

    rows = _records(daily_bars)
    cutoff = _date_value(completed_before) if completed_before is not None else None

    indexed: list[tuple[int, Mapping[str, Any], date | None]] = []
    for index, row in enumerate(rows):
        row_date = _date_value(_first(row, "date", "日期"))
        indexed.append((index, row, row_date))
    if indexed and all(row_date is not None for _, _, row_date in indexed):
        indexed.sort(key=lambda item: (item[2], item[0]))

    gaps: list[float] = []
    previous_close: float | None = None
    for _, row, row_date in indexed:
        completed = _first(row, "completed", "is_completed")
        if completed is not None and not bool(completed):
            continue
        if cutoff is not None:
            if row_date is None or row_date >= cutoff:
                continue

        open_price = _positive_float(_first(row, "open", "开盘"))
        explicit_prev = _positive_float(
            _first(row, "prev_close", "previous_close", "昨收", "前收盘"))
        base_close = explicit_prev or previous_close
        if open_price is not None and base_close is not None:
            gaps.append(abs(open_price / base_close - 1.0))

        close_price = _positive_float(_first(row, "close", "收盘"))
        if close_price is not None:
            previous_close = close_price

    sample = gaps[-lookback:]
    if len(sample) < min_samples:
        return None
    return _percentile_linear(sample, percentile)


def _result(
    *,
    outcome: str,
    reason_code: str,
    reason: str,
    checked_at: datetime,
    current_price: float | None,
    reference_price: float | None,
    max_buy_price: float | None,
    signal_deviation: float | None,
    opening_gap: float | None,
    dynamic_threshold: float | None,
    quote_asof: datetime | None,
) -> PriceGateResult:
    return PriceGateResult(
        outcome=outcome,
        reason_code=reason_code,
        reason=reason,
        checked_at=checked_at,
        current_price=current_price,
        reference_price=reference_price,
        max_buy_price=max_buy_price,
        signal_price_deviation_pct=signal_deviation,
        opening_gap_pct=opening_gap,
        dynamic_gap_threshold_pct=dynamic_threshold,
        quote_asof=quote_asof,
    )


def _datetime_delta_seconds(later: datetime, earlier: datetime) -> float | None:
    """Return a delta, refusing mixed aware/naive timestamp inputs."""
    if (later.tzinfo is None) != (earlier.tzinfo is None):
        return None
    return (later - earlier).total_seconds()


def evaluate_price_gate(
    *,
    current_price: float | None,
    reference_price: float | None,
    max_buy_price: float | None,
    opening_price: float | None,
    previous_close: float | None,
    dynamic_gap_threshold: float | None,
    expires_at: datetime | None,
    valid_from_at: datetime | None = None,
    now: datetime | None = None,
    quote_asof: datetime | None = None,
    max_quote_age_seconds: float | None = None,
    hard_deviation_threshold: float = 0.05,
    side: str = "buy",
) -> PriceGateResult:
    """Evaluate a fresh quote against a conditional trade plan.

    Precedence is intentional: expiry and missing evidence fail closed; the
    provisional hard deviation invalidates the old signal; an exceeded limit
    price invalidates its explicit condition; a dynamic gap anomaly requires a
    new review.  No outcome in this function executes a trade.
    """
    checked_at = now or datetime.now(timezone.utc)
    side = str(side).lower()

    if expires_at is None:
        return _result(
            outcome=BLOCKED_QUOTE, reason_code="missing_expiry",
            reason="计划缺少有效期，拒绝继续", checked_at=checked_at,
            current_price=current_price, reference_price=reference_price,
            max_buy_price=max_buy_price, signal_deviation=None,
            opening_gap=None, dynamic_threshold=dynamic_gap_threshold,
            quote_asof=quote_asof)
    expiry_delta = _datetime_delta_seconds(expires_at, checked_at)
    if expiry_delta is None:
        return _result(
            outcome=BLOCKED_QUOTE, reason_code="timestamp_timezone_mismatch",
            reason="时间戳时区不一致，无法验证有效期", checked_at=checked_at,
            current_price=current_price, reference_price=reference_price,
            max_buy_price=max_buy_price, signal_deviation=None,
            opening_gap=None, dynamic_threshold=dynamic_gap_threshold,
            quote_asof=quote_asof)
    if expiry_delta <= 0:
        return _result(
            outcome=EXPIRED, reason_code="plan_expired",
            reason="交易计划已过期", checked_at=checked_at,
            current_price=current_price, reference_price=reference_price,
            max_buy_price=max_buy_price, signal_deviation=None,
            opening_gap=None, dynamic_threshold=dynamic_gap_threshold,
            quote_asof=quote_asof)
    if valid_from_at is not None:
        start_delta = _datetime_delta_seconds(checked_at, valid_from_at)
        if start_delta is None:
            return _result(
                outcome=BLOCKED_QUOTE,
                reason_code="timestamp_timezone_mismatch",
                reason="时间戳时区不一致，无法验证计划起始时间",
                checked_at=checked_at,
                current_price=current_price,
                reference_price=reference_price,
                max_buy_price=max_buy_price,
                signal_deviation=None,
                opening_gap=None,
                dynamic_threshold=dynamic_gap_threshold,
                quote_asof=quote_asof,
            )
        if start_delta < 0:
            return _result(
                outcome=BLOCKED_QUOTE,
                reason_code="plan_not_yet_valid",
                reason="收盘后计划尚未进入下一交易日有效窗口",
                checked_at=checked_at,
                current_price=current_price,
                reference_price=reference_price,
                max_buy_price=max_buy_price,
                signal_deviation=None,
                opening_gap=None,
                dynamic_threshold=dynamic_gap_threshold,
                quote_asof=quote_asof,
            )

    price = _positive_float(current_price)
    reference = _positive_float(reference_price)
    opening = _positive_float(opening_price)
    prev_close = _positive_float(previous_close)
    dynamic = _finite_float(dynamic_gap_threshold)
    max_price = _positive_float(max_buy_price)
    hard = _finite_float(hard_deviation_threshold)
    required_missing = []
    if price is None:
        required_missing.append("current_price")
    if reference is None:
        required_missing.append("reference_price")
    if opening is None:
        required_missing.append("opening_price")
    if prev_close is None:
        required_missing.append("previous_close")
    if dynamic is None or dynamic < 0:
        required_missing.append("dynamic_gap_threshold")
    if side == "buy" and max_price is None:
        required_missing.append("max_buy_price")
    if hard is None or hard <= 0:
        required_missing.append("hard_deviation_threshold")
    if required_missing:
        return _result(
            outcome=BLOCKED_QUOTE, reason_code="missing_required_quote_data",
            reason="缺少必要行情数据: " + ", ".join(required_missing),
            checked_at=checked_at, current_price=price,
            reference_price=reference, max_buy_price=max_price,
            signal_deviation=None, opening_gap=None,
            dynamic_threshold=dynamic, quote_asof=quote_asof)

    if max_quote_age_seconds is not None:
        if max_quote_age_seconds < 0 or quote_asof is None:
            return _result(
                outcome=BLOCKED_QUOTE, reason_code="missing_fresh_quote_timestamp",
                reason="无法验证成交报价新鲜度", checked_at=checked_at,
                current_price=price, reference_price=reference,
                max_buy_price=max_price, signal_deviation=None,
                opening_gap=None, dynamic_threshold=dynamic,
                quote_asof=quote_asof)
        quote_age = _datetime_delta_seconds(checked_at, quote_asof)
        if quote_age is None:
            return _result(
                outcome=BLOCKED_QUOTE, reason_code="timestamp_timezone_mismatch",
                reason="报价时间戳时区不一致", checked_at=checked_at,
                current_price=price, reference_price=reference,
                max_buy_price=max_price, signal_deviation=None,
                opening_gap=None, dynamic_threshold=dynamic,
                quote_asof=quote_asof)
        if quote_age < -5 or quote_age > max_quote_age_seconds:
            return _result(
                outcome=BLOCKED_QUOTE, reason_code="stale_or_future_quote",
                reason="成交报价过旧或时间异常", checked_at=checked_at,
                current_price=price, reference_price=reference,
                max_buy_price=max_price, signal_deviation=None,
                opening_gap=None, dynamic_threshold=dynamic,
                quote_asof=quote_asof)

    # All variables below were validated above; assignments narrow their types.
    assert price is not None and reference is not None
    assert opening is not None and prev_close is not None
    assert dynamic is not None and hard is not None
    signal_deviation = abs(price / reference - 1.0)
    opening_gap = opening / prev_close - 1.0

    if signal_deviation > hard or math.isclose(
            signal_deviation, hard, rel_tol=1e-12, abs_tol=1e-12):
        return _result(
            outcome=INVALIDATED_PRICE, reason_code="hard_price_deviation",
            reason="当前价格相对分析参考价达到绝对失效线",
            checked_at=checked_at, current_price=price,
            reference_price=reference, max_buy_price=max_price,
            signal_deviation=signal_deviation, opening_gap=opening_gap,
            dynamic_threshold=dynamic, quote_asof=quote_asof)

    if side == "buy" and max_price is not None and price > max_price and not math.isclose(
            price, max_price, rel_tol=1e-12, abs_tol=1e-12):
        return _result(
            outcome=INVALIDATED_CONDITION, reason_code="max_buy_price_exceeded",
            reason="当前价格超过计划允许的最高买入价",
            checked_at=checked_at, current_price=price,
            reference_price=reference, max_buy_price=max_price,
            signal_deviation=signal_deviation, opening_gap=opening_gap,
            dynamic_threshold=dynamic, quote_asof=quote_asof)

    if abs(opening_gap) > dynamic and not math.isclose(
            abs(opening_gap), dynamic, rel_tol=1e-12, abs_tol=1e-12):
        return _result(
            outcome=REVIEW_REQUIRED, reason_code="dynamic_opening_gap_anomaly",
            reason="开盘缺口超过个股历史动态阈值，必须重新分析",
            checked_at=checked_at, current_price=price,
            reference_price=reference, max_buy_price=max_price,
            signal_deviation=signal_deviation, opening_gap=opening_gap,
            dynamic_threshold=dynamic, quote_asof=quote_asof)

    return _result(
        outcome=PASS, reason_code="price_gate_passed",
        reason="成交前价格门禁通过", checked_at=checked_at,
        current_price=price, reference_price=reference,
        max_buy_price=max_price, signal_deviation=signal_deviation,
        opening_gap=opening_gap, dynamic_threshold=dynamic,
        quote_asof=quote_asof)


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _json_dump(value: Any, empty: Any) -> str:
    if value is None:
        value = empty
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)


def _datetime_ordered(later: datetime, earlier: datetime) -> bool:
    delta = _datetime_delta_seconds(later, earlier)
    if delta is None:
        raise ValueError("datetime timezone awareness must be consistent")
    return delta > 0


def create_plan_from_decision(
    db: Session,
    decision: Decision,
    *,
    reference_price: float,
    data_cutoff_at: datetime,
    expires_at: datetime,
    max_buy_price: float | None = None,
    reference_price_at: datetime | None = None,
    reference_price_kind: str = "official_close",
    valid_from_at: datetime | None = None,
    invalidation_conditions: Mapping[str, Any] | None = None,
    policy_snapshot: Mapping[str, Any] | None = None,
    factsheet_hash: str = "",
    idempotency_key: str | None = None,
    supersedes: TradePlan | None = None,
    commit: bool = False,
) -> TradePlan:
    """Persist a candidate plan without creating an order or touching holdings."""
    if idempotency_key:
        existing = (db.query(TradePlan)
                    .filter(TradePlan.idempotency_key == idempotency_key).first())
        if existing is not None:
            if existing.decision_id != decision.id:
                raise IdempotencyConflict(
                    "trade-plan idempotency key belongs to another decision")
            return existing

    if decision.id is None:
        db.flush()
    side = str(decision.action).lower()
    if side not in {"buy", "sell"}:
        raise ValueError("only buy/sell decisions can create a trade plan")
    target_pct = _finite_float(decision.target_position_pct)
    if target_pct is None or not 0.0 <= target_pct <= 1.0:
        raise ValueError("target_position_pct must be between 0 and 1")
    ref = _positive_float(reference_price)
    if ref is None:
        raise ValueError("reference_price must be positive")
    max_price = _positive_float(max_buy_price)
    if side == "buy" and max_price is None:
        raise ValueError("buy plan requires a positive max_buy_price")
    valid_from = valid_from_at or data_cutoff_at
    if not _datetime_ordered(expires_at, valid_from):
        raise ValueError("expires_at must be later than valid_from_at")

    if supersedes is not None:
        if supersedes.code != decision.code or supersedes.model_pk != decision.model_pk:
            raise ValueError("superseded plan must have the same model and code")
        version = supersedes.version + 1
        supersedes.status = "superseded"
        supersedes.status_reason_code = "new_plan_version"
        supersedes.status_reason = "已生成更新后的交易计划"
        supersedes.lock_version += 1
        supersedes.updated_at = utc_now()
    else:
        version = 1

    plan = TradePlan(
        decision_id=decision.id,
        run_id=decision.run_id,
        model_pk=decision.model_pk,
        supersedes_plan_id=supersedes.id if supersedes else None,
        code=decision.code,
        name=decision.name,
        side=side,
        status="candidate",
        version=version,
        target_position_pct=target_pct,
        max_buy_price=max_price,
        reference_price=ref,
        reference_price_kind=reference_price_kind,
        reference_price_at=reference_price_at or data_cutoff_at,
        data_cutoff_at=data_cutoff_at,
        valid_from_at=valid_from,
        expires_at=expires_at,
        confidence=float(decision.confidence or 0.0),
        thesis=decision.reason or "",
        invalidation_conditions_json=_json_dump(invalidation_conditions, {}),
        policy_snapshot_json=_json_dump(policy_snapshot, {}),
        factsheet_hash=factsheet_hash,
        idempotency_key=idempotency_key,
    )
    db.add(plan)
    db.flush()
    if commit:
        db.commit()
    return plan


def record_gate(
    db: Session,
    plan: TradePlan,
    *,
    gate_type: str,
    outcome: str | None = None,
    evaluation: PriceGateResult | None = None,
    reason_code: str = "",
    reason: str = "",
    checked_at: datetime | None = None,
    coverage_from: datetime | None = None,
    coverage_to: datetime | None = None,
    required_sources: Iterable[str] | None = None,
    source_results: Mapping[str, Any] | None = None,
    new_information_ids: Iterable[Any] | None = None,
    metrics: Mapping[str, Any] | None = None,
    input_hash: str = "",
    idempotency_key: str | None = None,
    next_status: str | None = None,
    commit: bool = False,
) -> GateCheck:
    """Append gate evidence and optionally advance the plan status.

    Supplying an idempotency key makes repeated scheduler/API calls return the
    original check instead of duplicating evidence.
    """
    if idempotency_key:
        existing = (db.query(GateCheck)
                    .filter(GateCheck.idempotency_key == idempotency_key).first())
        if existing is not None:
            if (
                existing.plan_id != plan.id
                or existing.plan_version != plan.version
                or existing.gate_type != gate_type
            ):
                raise IdempotencyConflict(
                    "gate idempotency key belongs to another plan or gate")
            return existing
    if plan.id is None:
        db.flush()

    if evaluation is not None:
        if outcome is not None and outcome != evaluation.outcome:
            raise ValueError("outcome conflicts with evaluation")
        outcome = evaluation.outcome
        reason_code = reason_code or evaluation.reason_code
        reason = reason or evaluation.reason
        checked_at = checked_at or evaluation.checked_at
        quote_price = evaluation.current_price
        quote_asof = evaluation.quote_asof
        opening_gap = evaluation.opening_gap_pct
        dynamic_threshold = evaluation.dynamic_gap_threshold_pct
        signal_deviation = evaluation.signal_price_deviation_pct
        merged_metrics = evaluation.to_dict()
        merged_metrics.update(dict(metrics or {}))
    else:
        quote_price = None
        quote_asof = None
        opening_gap = None
        dynamic_threshold = None
        signal_deviation = None
        merged_metrics = dict(metrics or {})

    if not outcome:
        raise ValueError("outcome or evaluation is required")
    checked = checked_at or utc_now()
    check = GateCheck(
        plan_id=plan.id,
        plan_version=plan.version,
        gate_type=gate_type,
        outcome=outcome,
        reason_code=reason_code,
        reason=reason,
        idempotency_key=idempotency_key,
        checked_at=checked,
        coverage_from=coverage_from,
        coverage_to=coverage_to,
        required_sources_json=_json_dump(list(required_sources or []), []),
        source_results_json=_json_dump(source_results, {}),
        quote_price=quote_price,
        quote_asof=quote_asof,
        opening_gap_pct=opening_gap,
        dynamic_gap_threshold_pct=dynamic_threshold,
        signal_price_deviation_pct=signal_deviation,
        metrics_json=_json_dump(merged_metrics, {}),
        new_information_ids_json=_json_dump(list(new_information_ids or []), []),
        input_hash=input_hash,
    )
    db.add(check)

    resolved_status = next_status
    if resolved_status is None and outcome in _NON_PASS_PLAN_STATUSES:
        resolved_status = outcome
    if resolved_status is not None and resolved_status != plan.status:
        plan.status = resolved_status
        plan.status_reason_code = reason_code
        plan.status_reason = reason
        plan.lock_version += 1
        plan.updated_at = checked

    db.flush()
    if commit:
        db.commit()
    return check
