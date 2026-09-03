from unittest.mock import patch

import pytest

from app.models import Account, CanaryState, Position
from app.runtime_settings import get_setting as real_get_setting
from app.trading import portfolio, risk_contract


def _legacy_account(db, model_pk: int, *, cash: float) -> Account:
    account = Account(
        model_pk=model_pk,
        cash=cash,
        initial_cash=1_000_000.0,
    )
    db.add(account)
    db.commit()
    return account


@pytest.mark.parametrize(
    ("loss", "expected_level", "expected_stopped"),
    [
        (4_999.0, 0, False),
        (5_000.0, 1, False),
        (10_000.0, 2, False),
        (15_000.0, 3, True),
    ],
)
def test_canary_drawdown_boundaries_are_inclusive(
    db, model_a, loss, expected_level, expected_stopped,
):
    state = risk_contract.refresh_canary_state(
        db,
        model_a.id,
        actual_total_equity=1_000_000.0 - loss,
        account_initial_cash=1_000_000.0,
    )
    db.commit()

    persisted = db.query(CanaryState).filter_by(model_pk=model_a.id).one()
    assert persisted.id == state.id
    assert persisted.risk_equity == pytest.approx(100_000.0 - loss)
    assert persisted.high_water == pytest.approx(100_000.0)
    assert persisted.drawdown == pytest.approx(loss)
    assert persisted.alert_level == expected_level
    assert persisted.status == ("stopped" if expected_stopped else "active")
    assert (persisted.stopped_at is not None) is expected_stopped


@pytest.mark.parametrize("loss", [4_999.0, 5_000.0, 10_000.0])
def test_alert_boundaries_do_not_resize_a_buy(db, model_a, loss):
    _legacy_account(db, model_a.id, cash=1_000_000.0 - loss)

    action, target, note = portfolio.apply_risk_limits(
        db, model_a.id, "600519", "buy", 0.20)

    assert action == "buy"
    assert target == pytest.approx(0.20)
    if loss == 5_000.0:
        assert "一级回撤告警" in note
    elif loss == 10_000.0:
        assert "二级回撤告警" in note
    else:
        assert "回撤告警" not in note


def test_stop_is_durable_does_not_auto_sell_and_explicit_sell_still_works(
    db, model_a,
):
    account = _legacy_account(db, model_a.id, cash=975_000.0)
    position = Position(
        model_pk=model_a.id,
        code="600519",
        name="贵州茅台",
        total_qty=1_000,
        available_qty=1_000,
        avg_cost=10.0,
    )
    db.add(position)
    db.commit()
    quote = {"price": 10.0, "pct_change": 0.0}

    with patch("app.trading.portfolio.market.get_trade_quote", return_value=quote):
        action, _, note = portfolio.apply_risk_limits(
            db, model_a.id, "000001", "buy", 0.10)
        db.commit()
        assert action == "hold"
        assert "停止新增风险" in note
        assert db.query(Position).filter_by(model_pk=model_a.id).count() == 1

        # Recover above the original account baseline.  A stopped canary must
        # never resume merely because mark-to-market equity recovered.
        account.cash = 1_000_000.0
        db.commit()
        action, _, _ = portfolio.apply_risk_limits(
            db, model_a.id, "000001", "buy", 0.10)
        db.commit()
        assert action == "hold"
        assert db.query(CanaryState).filter_by(model_pk=model_a.id).one().status == "stopped"

        result = portfolio.execute_decision(
            db,
            model_a.id,
            None,
            "600519",
            "贵州茅台",
            "sell",
            0.0,
        )

    assert result is not None and result.ok
    assert db.query(Position).filter_by(
        model_pk=model_a.id, code="600519").first() is None
    assert db.query(CanaryState).filter_by(model_pk=model_a.id).one().status == "stopped"


def _permissive_candidate_settings(key: str):
    if key in {
        "risk.max_position_pct",
        "risk.max_total_position_pct",
        "risk.max_buy_cash_pct",
    }:
        return 1.0
    if key == "signal.max_positions":
        return 10
    return real_get_setting(key)


def test_legacy_million_account_targets_100k_and_never_exceeds_80k(
    db, model_a,
):
    _legacy_account(db, model_a.id, cash=1_000_000.0)
    quote = {"price": 10.0, "pct_change": 0.0}

    with patch(
        "app.trading.portfolio.get_setting",
        side_effect=_permissive_candidate_settings,
    ), patch(
        "app.trading.portfolio.market.get_trade_quote", return_value=quote,
    ):
        first = portfolio.execute_decision(
            db, model_a.id, None, "600001", "第一只", "buy", 0.25)
        second = portfolio.execute_decision(
            db, model_a.id, None, "600002", "第二只", "buy", 1.0)
        blocked = portfolio.execute_decision(
            db, model_a.id, None, "600003", "第三只", "buy", 1.0)

    assert first is not None and first.ok
    assert first.order.amount == pytest.approx(25_000.0)
    assert second is not None and second.ok
    assert second.order.amount == pytest.approx(55_000.0)
    assert blocked is not None and blocked.ok is False
    positions = db.query(Position).filter(Position.model_pk == model_a.id).all()
    assert sum(pos.total_qty * 10.0 for pos in positions) == pytest.approx(80_000.0)
    state = db.query(CanaryState).filter_by(model_pk=model_a.id).one()
    assert state.risk_equity < 100_000.0  # fees are P&L; excess legacy cash is not capital


def test_position_count_and_per_trade_limits_are_both_enforced(db, model_a):
    _legacy_account(db, model_a.id, cash=1_000_000.0)
    for idx in range(3):
        db.add(Position(
            model_pk=model_a.id,
            code=f"60000{idx}",
            name=f"持仓{idx}",
            total_qty=100,
            available_qty=100,
            avg_cost=10.0,
        ))
    db.commit()

    with patch(
        "app.trading.portfolio.market.get_trade_quote",
        return_value={"price": 10.0, "pct_change": 0.0},
    ):
        action, _, note = portfolio.apply_risk_limits(
            db, model_a.id, "600099", "buy", 0.20)
    assert action == "hold"
    assert "最大持仓只数 3" in note

    def tight_trade_setting(key: str):
        if key == "risk.max_position_pct":
            return 0.25
        if key == "risk.max_total_position_pct":
            return 1.0
        if key == "risk.max_buy_cash_pct":
            return 0.10
        if key == "signal.max_positions":
            return 10
        return real_get_setting(key)

    with patch(
        "app.trading.portfolio.get_setting", side_effect=tight_trade_setting,
    ), patch(
        "app.trading.portfolio.market.get_trade_quote",
        return_value={"price": 10.0, "pct_change": 0.0},
    ):
        action, target, note = portfolio.apply_risk_limits(
            db, model_a.id, "600099", "buy", 0.80)
    assert action == "buy"
    assert target == pytest.approx(0.10)
    assert "单票仓位上限" in note
    assert "单次买入" in note
