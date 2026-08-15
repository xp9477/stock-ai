"""Integration contracts for information/price gates and human approval."""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.models import (
    Account,
    CanaryState,
    Decision,
    ExecutionIntent,
    GateCheck,
    Order,
    Position,
    Run,
)
from app.trading.plan_service import (
    PlanBlocked,
    approve_plan,
    maybe_auto_fill_ticket,
    maybe_auto_issue_ticket,
    review_preopen_information,
)
from app.trading.trade_plans import create_plan_from_decision


UTC = timezone.utc
NOW = datetime(2026, 8, 10, 1, 35, tzinfo=UTC)


def _bars():
    start = date(2026, 5, 1)
    return [{
        "date": start + timedelta(days=i),
        "open": 100.5,
        "prev_close": 100.0,
        "close": 100.0,
        "completed": True,
    } for i in range(60)]


def _quote(price=101.0):
    return {
        "code": "600519",
        "price": price,
        "open": 100.5,
        "prev_close": 100.0,
        "quote_asof": (NOW - timedelta(seconds=5)).isoformat(),
        "received_at": NOW.isoformat(),
        "tradable": True,
        "source": "test-double-source",
    }


def _plan(
    db, model_a, *, news_fingerprint="baseline", action="buy",
    target_pct=0.2, code="600519",
):
    model_a.type = "ensemble"
    model_a.is_official_strategy = True
    if db.query(Account).filter(Account.model_pk == model_a.id).first() is None:
        db.add(Account(model_pk=model_a.id, cash=100_000, initial_cash=100_000))
    run = Run(trigger="schedule")
    db.add(run)
    db.flush()
    decision = Decision(
        run_id=run.id, model_pk=model_a.id, code=code, name="测试股",
        action=action, target_position_pct=target_pct, confidence=0.8,
        reason="只使用冻结事实形成的候选逻辑",
    )
    db.add(decision)
    db.flush()
    return create_plan_from_decision(
        db, decision,
        reference_price=100.0,
        max_buy_price=103.0 if action == "buy" else None,
        data_cutoff_at=NOW - timedelta(hours=10),
        valid_from_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(hours=1),
        invalidation_conditions={"material_news": "review_required"},
        policy_snapshot={
            **({"news_fingerprint": news_fingerprint}
               if news_fingerprint is not None else {}),
            "gap_lookback_days": 60,
            "gap_percentile": 0.95,
            "gap_min_samples": 40,
            "hard_price_deviation_pct": 0.05,
        },
    )


def _news(fingerprint="baseline", *, rss_ok=True):
    return {
        "fingerprint": fingerprint,
        "rss_coverage_ok": rss_ok,
        "official_coverage": False,
        "source_results": [{"id": "rss", "ok": rss_ok}],
        "items": [] if fingerprint == "baseline" else [{
            "title": "新消息", "content_hash": fingerprint,
        }],
    }


def test_official_disclosure_check_is_explicitly_fail_closed(db, model_a):
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ), patch(
        "app.trading.plan_service.get_setting",
        side_effect=lambda key, db=None: (
            True if key == "execution.require_human_information_check"
            else False if key == "execution.require_manual_confirmation"
            else 60 if key.endswith("seconds") else 0
        ),
    ):
        blocked = review_preopen_information(db, plan, human_official_confirmed=False)
    assert blocked.outcome == "blocked_information"
    assert blocked.reason_code == "official_disclosure_unverified"
    assert plan.status == "blocked_information"


def test_information_gate_passes_without_human_check_when_disabled(db, model_a):
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ):
        check = review_preopen_information(db, plan, human_official_confirmed=False)
    assert check.outcome == "pass"
    assert plan.status == "preopen_validated"


def test_new_direct_news_requires_reanalysis_even_after_human_check(db, model_a):
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news("changed"),
    ):
        check = review_preopen_information(db, plan, human_official_confirmed=True)
    assert check.outcome == "review_required"
    assert plan.status == "review_required"


def test_missing_analysis_news_fingerprint_requires_reanalysis(db, model_a):
    plan = _plan(db, model_a, news_fingerprint=None)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ):
        check = review_preopen_information(
            db, plan, human_official_confirmed=True)
    assert check.outcome == "review_required"
    assert check.reason_code == "missing_analysis_news_fingerprint"
    assert plan.status == "review_required"


def test_human_confirmation_cannot_approve_plan_without_analysis_news_fingerprint(
    db, model_a,
):
    plan = _plan(db, model_a, news_fingerprint=None)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ), pytest.raises(PlanBlocked) as err:
        approve_plan(
            db, plan,
            expected_lock_version=plan.lock_version,
            idempotency_key="approve-missing-analysis-news-fingerprint",
            confirmed=True,
            human_official_confirmed=True,
            quote=_quote(),
            daily_bars=_bars(),
            now=NOW,
            require_session=False,
        )
    assert err.value.status == "review_required"
    assert plan.status_reason_code == "missing_analysis_news_fingerprint"
    assert db.query(ExecutionIntent).count() == 0
    assert db.query(Order).count() == 0


def test_human_approval_creates_ticket_but_no_order_or_position(db, model_a):
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ):
        intent = approve_plan(
            db, plan,
            expected_lock_version=plan.lock_version,
            idempotency_key="approve-plan-1",
            confirmed=True,
            human_official_confirmed=True,
            quote=_quote(),
            daily_bars=_bars(),
            now=NOW,
            require_session=False,
        )
    assert intent.status == "ticket_ready"
    assert plan.status == "ticket_ready"
    assert db.query(ExecutionIntent).count() == 1
    assert db.query(Order).count() == 0
    assert db.query(Position).count() == 0
    assert db.query(GateCheck).count() == 3
    assert intent.authorized_qty == 100
    assert intent.authorized_notional == 10_100
    assert intent.authorized_target_position_pct == pytest.approx(0.101)
    assert intent.estimated_fee > 0
    assert "canary_status" in intent.risk_snapshot_json


def test_auto_issue_ticket_skips_human_confirmation(db, model_a):
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ), patch(
        "app.trading.plan_service.market.get_execution_quote",
        return_value=_quote(),
    ), patch(
        "app.trading.plan_service.market.get_daily_kline",
        return_value=_bars(),
    ):
        intent = maybe_auto_issue_ticket(db, plan, now=NOW)

    assert intent is not None
    assert intent.status == "ticket_ready"
    assert intent.approved_by == "system"
    assert plan.status == "ticket_ready"
    assert plan.status_reason_code == "auto_approved"
    assert db.query(Order).count() == 0


def test_auto_fill_ticket_creates_local_order_and_position(db, model_a):
    plan = _plan(db, model_a)
    quote = _quote(101.0)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ), patch(
        "app.trading.plan_service.market.get_execution_quote",
        return_value=quote,
    ), patch(
        "app.trading.plan_service.market.get_daily_kline",
        return_value=_bars(),
    ), patch(
        "app.trading.plan_service.market.get_trade_quote",
        return_value={**quote, "pct_change": 0.5},
    ):
        intent = maybe_auto_issue_ticket(db, plan, now=NOW)
        filled = maybe_auto_fill_ticket(db, plan, intent)

    assert filled.status == "executed"
    assert plan.status == "executed"
    assert db.query(Order).filter(Order.status == "filled").count() == 1
    pos = db.query(Position).filter_by(model_pk=model_a.id, code="600519").one()
    assert pos.total_qty > 0


def test_auto_fill_uses_emt_inbox_when_broker_reference_required(db, model_a, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "broker_reference_required", True)
    plan = _plan(db, model_a)
    quote = _quote(101.0)
    from app.trading.broker_snapshot import BrokerSnapshot
    snap = BrokerSnapshot(
        provider="emt", mode="simulation", read_only=True, connected=True,
        reconciled=True, account_ref="emt-sim-primary",
        observed_at=NOW,
        asset={"total_asset": 200000.0, "buying_power": 189900.0},
        positions=({"ticker": "600519", "total_qty": 100, "sellable_qty": 0,
                    "avg_price": 101.0, "unrealized_pnl": 0.0},),
        orders=(), trades=(), query_errors=(),
    )
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ), patch(
        "app.trading.plan_service.market.get_execution_quote",
        return_value=quote,
    ), patch(
        "app.trading.plan_service.market.get_daily_kline",
        return_value=_bars(),
    ), patch(
        "app.trading.emt_orders.submit_simulation_order",
        return_value={"accepted": True, "order_emt_id": 88, "error": ""},
    ) as submit, patch(
        "app.trading.broker_snapshot.load_broker_snapshot",
        return_value=snap,
    ), patch(
        "app.trading.broker_snapshot.reconcile_snapshot_projection",
        return_value={"position_count": 1},
    ):
        intent = maybe_auto_issue_ticket(db, plan, now=NOW)
        filled = maybe_auto_fill_ticket(db, plan, intent)

    assert submit.called
    assert filled.status == "executed"
    assert plan.status_reason_code == "emt_filled"
    assert db.query(Order).count() == 0


def test_approval_idempotency_returns_same_ticket(db, model_a):
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ):
        first = approve_plan(
            db, plan, expected_lock_version=plan.lock_version,
            idempotency_key="approve-plan-idempotent", confirmed=True,
            human_official_confirmed=True, quote=_quote(), daily_bars=_bars(),
            now=NOW, require_session=False,
        )
        second = approve_plan(
            db, plan, expected_lock_version=1,
            idempotency_key="approve-plan-idempotent", confirmed=True,
            human_official_confirmed=True, quote=_quote(), daily_bars=_bars(),
            now=NOW, require_session=False,
        )
    assert first.id == second.id
    assert db.query(ExecutionIntent).count() == 1


def test_ticket_authorization_is_immutable_but_status_can_advance(db, model_a):
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ):
        intent = approve_plan(
            db, plan,
            expected_lock_version=plan.lock_version,
            idempotency_key="approve-immutable-ticket",
            confirmed=True,
            human_official_confirmed=True,
            quote=_quote(),
            daily_bars=_bars(),
            now=NOW,
            require_session=False,
        )

    db.execute(text(
        "UPDATE execution_intents SET status='cancelled' WHERE id=:intent_id"
    ), {"intent_id": intent.id})
    db.commit()
    db.refresh(intent)
    assert intent.status == "cancelled"

    with pytest.raises(DBAPIError):
        db.execute(text(
            "UPDATE execution_intents SET authorized_qty=authorized_qty+100 "
            "WHERE id=:intent_id"
        ), {"intent_id": intent.id})
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text(
            "DELETE FROM execution_intents WHERE id=:intent_id"
        ), {"intent_id": intent.id})
    db.rollback()


def test_price_change_at_hard_line_blocks_approval(db, model_a):
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ), pytest.raises(PlanBlocked) as err:
        approve_plan(
            db, plan, expected_lock_version=plan.lock_version,
            idempotency_key="approve-plan-invalid-price", confirmed=True,
            human_official_confirmed=True, quote=_quote(105.0), daily_bars=_bars(),
            now=NOW, require_session=False,
        )
    assert err.value.status == "invalidated_price"
    assert db.query(ExecutionIntent).count() == 0
    assert db.query(Order).count() == 0


def test_approval_rechecks_canary_after_plan_creation(db, model_a):
    plan = _plan(db, model_a)
    account = db.query(Account).filter(Account.model_pk == model_a.id).one()
    account.cash = 84_999
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ), pytest.raises(PlanBlocked) as err:
        approve_plan(
            db, plan,
            expected_lock_version=plan.lock_version,
            idempotency_key="approve-canary-recheck",
            confirmed=True,
            human_official_confirmed=True,
            quote=_quote(),
            daily_bars=_bars(),
            now=NOW,
            require_session=False,
        )
    assert err.value.status == "blocked_capital"
    assert "Canary" in err.value.reason
    assert db.query(ExecutionIntent).count() == 0
    assert db.query(Order).count() == 0
    assert db.query(CanaryState).one().status == "stopped"
    capital_gate = db.query(GateCheck).filter_by(gate_type="pretrade_capital").one()
    assert capital_gate.outcome == "blocked_capital"


def test_approval_rechecks_position_slots_with_fresh_quotes(db, model_a):
    plan = _plan(db, model_a)
    for index, code in enumerate(("000001", "000002", "000003"), start=1):
        db.add(Position(
            model_pk=model_a.id,
            code=code,
            name=f"持仓{index}",
            total_qty=200,
            available_qty=200,
            avg_cost=100.0,
        ))
    account = db.query(Account).filter(Account.model_pk == model_a.id).one()
    account.cash = 40_000
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ), patch(
        "app.trading.portfolio.market.get_execution_quote",
        side_effect=lambda code, **_: {**_quote(100.0), "code": code},
    ), pytest.raises(PlanBlocked) as err:
        approve_plan(
            db, plan,
            expected_lock_version=plan.lock_version,
            idempotency_key="approve-position-slots-recheck",
            confirmed=True,
            human_official_confirmed=True,
            quote=_quote(),
            daily_bars=_bars(),
            now=NOW,
            require_session=False,
        )
    assert err.value.status == "blocked_capital"
    assert "3 个持仓名额" in err.value.reason
    assert db.query(ExecutionIntent).count() == 0


def test_approval_rechecks_absolute_exposure_after_plan_creation(db, model_a):
    plan = _plan(db, model_a)
    db.add(Position(
        model_pk=model_a.id,
        code="000001",
        name="已有持仓",
        total_qty=790,
        available_qty=790,
        avg_cost=100.0,
    ))
    account = db.query(Account).filter(Account.model_pk == model_a.id).one()
    account.cash = 21_000
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ), patch(
        "app.trading.portfolio.market.get_execution_quote",
        return_value={**_quote(100.0), "code": "000001"},
    ), pytest.raises(PlanBlocked) as err:
        approve_plan(
            db, plan,
            expected_lock_version=plan.lock_version,
            idempotency_key="approve-exposure-recheck",
            confirmed=True,
            human_official_confirmed=True,
            quote=_quote(),
            daily_bars=_bars(),
            now=NOW,
            require_session=False,
        )
    assert err.value.status == "blocked_capital"
    assert "没有可批准的买入数量" in err.value.reason
    assert db.query(ExecutionIntent).count() == 0


def test_stopped_canary_does_not_block_sell_ticket(db, model_a):
    plan = _plan(db, model_a, action="sell", target_pct=0.0)
    db.add(Position(
        model_pk=model_a.id,
        code="600519",
        name="测试股",
        total_qty=1_000,
        available_qty=1_000,
        avg_cost=100.0,
    ))
    db.add(CanaryState(
        model_pk=model_a.id,
        status="stopped",
        high_water=100_000,
        risk_equity=84_000,
        drawdown=16_000,
        alert_level=3,
    ))
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ):
        intent = approve_plan(
            db, plan,
            expected_lock_version=plan.lock_version,
            idempotency_key="approve-sell-while-stopped",
            confirmed=True,
            human_official_confirmed=True,
            quote=_quote(),
            daily_bars=_bars(),
            now=NOW,
            require_session=False,
        )
    assert intent.status == "ticket_ready"
    assert intent.authorized_qty == 1_000
    assert intent.authorized_notional == 101_000
    assert db.query(Order).count() == 0


def test_unexpired_sell_ticket_reserves_available_quantity(db, model_a):
    first_plan = _plan(db, model_a, action="sell", target_pct=0.0)
    second_plan = _plan(db, model_a, action="sell", target_pct=0.0)
    db.add(Position(
        model_pk=model_a.id,
        code="600519",
        name="测试股",
        total_qty=1_000,
        available_qty=1_000,
        avg_cost=100.0,
    ))
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ):
        first = approve_plan(
            db, first_plan,
            expected_lock_version=first_plan.lock_version,
            idempotency_key="approve-first-sell-reservation",
            confirmed=True,
            human_official_confirmed=True,
            quote=_quote(),
            daily_bars=_bars(),
            now=NOW,
            require_session=False,
        )
        with pytest.raises(PlanBlocked) as err:
            approve_plan(
                db, second_plan,
                expected_lock_version=second_plan.lock_version,
                idempotency_key="approve-second-sell-reservation",
                confirmed=True,
                human_official_confirmed=True,
                quote=_quote(),
                daily_bars=_bars(),
                now=NOW,
                require_session=False,
            )
    assert first.authorized_qty == 1_000
    assert err.value.status == "blocked_capital"
    assert "全部预留" in err.value.reason
    assert db.query(ExecutionIntent).count() == 1
    assert db.query(Order).count() == 0


def test_disabled_official_strategy_cannot_approve_old_plan(db, model_a):
    plan = _plan(db, model_a)
    model_a.enabled = False
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ), pytest.raises(PlanBlocked) as err:
        approve_plan(
            db, plan,
            expected_lock_version=plan.lock_version,
            idempotency_key="approve-disabled-official",
            confirmed=True,
            human_official_confirmed=True,
            quote=_quote(),
            daily_bars=_bars(),
            now=NOW,
            require_session=False,
        )
    assert err.value.status == "blocked_capital"
    assert "已启用" in err.value.reason
    assert db.query(ExecutionIntent).count() == 0
