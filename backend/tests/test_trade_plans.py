"""Deterministic tests for the candidate-plan and price-gate core."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models import (Decision, ExecutionIntent, GateCheck, Order, Position,
                        ReviewEvent, Run, TradePlan)
from app.trading.trade_plans import (
    BLOCKED_QUOTE,
    EXPIRED,
    INVALIDATED_CONDITION,
    INVALIDATED_PRICE,
    PASS,
    REVIEW_REQUIRED,
    create_plan_from_decision,
    evaluate_price_gate,
    historical_gap_threshold,
    record_gate,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 10, 1, 35, tzinfo=UTC)


def _price_gate(**overrides):
    values = {
        "current_price": 100.0,
        "reference_price": 100.0,
        "max_buy_price": 110.0,
        "opening_price": 100.0,
        "previous_close": 100.0,
        "dynamic_gap_threshold": 0.02,
        "expires_at": NOW + timedelta(hours=1),
        "now": NOW,
    }
    values.update(overrides)
    return evaluate_price_gate(**values)


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (104.999, PASS),
        (105.000, INVALIDATED_PRICE),
        (105.001, INVALIDATED_PRICE),
    ],
)
def test_hard_price_deviation_boundary(price, expected):
    result = _price_gate(current_price=price)
    assert result.outcome == expected


def test_dynamic_opening_gap_requires_review():
    result = _price_gate(
        current_price=101.0,
        opening_price=103.0,
        previous_close=100.0,
        dynamic_gap_threshold=0.02,
    )
    assert result.outcome == REVIEW_REQUIRED
    assert result.reason_code == "dynamic_opening_gap_anomaly"
    assert result.opening_gap_pct == pytest.approx(0.03)


def test_dynamic_threshold_boundary_itself_is_not_anomaly():
    result = _price_gate(
        current_price=101.0,
        opening_price=102.0,
        previous_close=100.0,
        dynamic_gap_threshold=0.02,
    )
    assert result.outcome == PASS


def test_max_buy_price_is_an_explicit_invalidating_condition():
    result = _price_gate(
        current_price=101.0,
        max_buy_price=100.5,
    )
    assert result.outcome == INVALIDATED_CONDITION
    assert result.reason_code == "max_buy_price_exceeded"


def test_expired_plan_fails_closed_before_other_checks():
    result = _price_gate(expires_at=NOW)
    assert result.outcome == EXPIRED
    assert result.reason_code == "plan_expired"


def test_after_close_plan_cannot_be_used_before_next_window():
    result = _price_gate(valid_from_at=NOW + timedelta(minutes=1))
    assert result.outcome == BLOCKED_QUOTE
    assert result.reason_code == "plan_not_yet_valid"


@pytest.mark.parametrize(
    "missing",
    [
        "current_price",
        "reference_price",
        "max_buy_price",
        "opening_price",
        "previous_close",
        "dynamic_gap_threshold",
    ],
)
def test_missing_gate_input_fails_closed(missing):
    result = _price_gate(**{missing: None})
    assert result.outcome == BLOCKED_QUOTE
    assert result.reason_code == "missing_required_quote_data"


def test_quote_freshness_can_be_required():
    result = _price_gate(max_quote_age_seconds=10, quote_asof=None)
    assert result.outcome == BLOCKED_QUOTE
    assert result.reason_code == "missing_fresh_quote_timestamp"


def test_historical_gap_threshold_uses_only_completed_history():
    start = date(2026, 4, 1)
    bars = [
        {
            "date": start + timedelta(days=i),
            "open": 101.0,
            "prev_close": 100.0,
            "close": 100.0,
            "completed": True,
        }
        for i in range(60)
    ]
    # A huge current-day gap must not leak into the historical threshold.
    bars.append({
        "date": start + timedelta(days=60),
        "open": 150.0,
        "prev_close": 100.0,
        "close": 150.0,
        "completed": False,
    })
    threshold = historical_gap_threshold(
        bars, lookback=60, percentile=0.95, min_samples=40,
        completed_before=start + timedelta(days=60),
    )
    assert threshold == pytest.approx(0.01)


def test_historical_gap_threshold_returns_none_for_too_few_samples():
    bars = [
        {"open": 101.0, "prev_close": 100.0, "close": 100.0}
        for _ in range(39)
    ]
    assert historical_gap_threshold(
        bars, lookback=60, percentile=0.95, min_samples=40) is None


def _decision(db, model_a) -> Decision:
    run = Run(trigger="schedule")
    db.add(run)
    db.flush()
    decision = Decision(
        run_id=run.id,
        model_pk=model_a.id,
        code="600519",
        name="测试股",
        action="buy",
        target_position_pct=0.2,
        confidence=0.8,
        reason="候选逻辑",
    )
    db.add(decision)
    db.flush()
    return decision


def test_create_plan_is_candidate_only_and_gate_is_auditable(db, model_a):
    decision = _decision(db, model_a)
    plan = create_plan_from_decision(
        db,
        decision,
        reference_price=100.0,
        max_buy_price=103.0,
        data_cutoff_at=NOW - timedelta(hours=10),
        valid_from_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        invalidation_conditions={"material_disclosure": "review_required"},
        policy_snapshot={"hard_deviation_threshold": 0.05},
        idempotency_key="plan-600519-20260810-v1",
    )

    assert plan.status == "candidate"
    assert plan.version == 1
    assert plan.max_buy_price == 103.0
    assert db.query(Order).count() == 0
    assert db.query(Position).count() == 0

    evaluation = _price_gate(current_price=101.0, max_buy_price=103.0)
    check = record_gate(
        db,
        plan,
        gate_type="pretrade_quote",
        evaluation=evaluation,
        idempotency_key="gate-600519-20260810-pretrade",
        next_status="awaiting_approval",
    )
    repeated = record_gate(
        db,
        plan,
        gate_type="pretrade_quote",
        evaluation=evaluation,
        idempotency_key="gate-600519-20260810-pretrade",
        next_status="awaiting_approval",
    )

    assert check.id == repeated.id
    assert db.query(GateCheck).count() == 1
    assert plan.status == "awaiting_approval"
    assert plan.lock_version == 2


def test_new_lifecycle_tables_have_long_status_fields():
    assert TradePlan.__table__.c.status.type.length >= 32
    assert GateCheck.__table__.c.outcome.type.length >= 32
    assert ExecutionIntent.__table__.c.status.type.length >= 32
    assert ReviewEvent.__table__.c.status.type.length >= 32
