"""Durable capital contract for the real-money canary stage.

The simulated broker may still contain legacy accounts whose bookkeeping cash
is larger than the capital the user authorized.  This module deliberately
normalizes P&L onto the authorized-capital baseline and makes the stop latch
durable.  Alert levels never liquidate or resize positions; the stop only
prevents new buy risk.
"""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import CanaryState, now
from ..runtime_settings import get_setting


@dataclass(frozen=True)
class CapitalContract:
    authorized_capital: float
    max_stock_exposure: float
    drawdown_alert_1: float
    drawdown_alert_2: float
    canary_stop_drawdown: float


def load_capital_contract() -> CapitalContract:
    """Read and validate the user-owned hard capital boundary."""
    contract = CapitalContract(
        authorized_capital=float(get_setting("capital.authorized_capital")),
        max_stock_exposure=float(get_setting("capital.max_stock_exposure")),
        drawdown_alert_1=float(get_setting("capital.drawdown_alert_1")),
        drawdown_alert_2=float(get_setting("capital.drawdown_alert_2")),
        canary_stop_drawdown=float(get_setting("capital.canary_stop_drawdown")),
    )
    if contract.authorized_capital <= 0:
        raise ValueError("capital.authorized_capital must be positive")
    if not (
        0 <= contract.drawdown_alert_1
        <= contract.drawdown_alert_2
        <= contract.canary_stop_drawdown
    ):
        raise ValueError("capital drawdown thresholds must be ordered")
    if contract.max_stock_exposure < 0:
        raise ValueError("capital.max_stock_exposure cannot be negative")
    return contract


def normalized_risk_equity(
    *, authorized_capital: float, actual_total_equity: float,
    account_initial_cash: float,
) -> float:
    """Map actual P&L onto the authorized capital, excluding legacy excess cash."""
    return authorized_capital + (actual_total_equity - account_initial_cash)


def _alert_level(drawdown: float, contract: CapitalContract) -> int:
    if drawdown >= contract.canary_stop_drawdown:
        return 3
    if drawdown >= contract.drawdown_alert_2:
        return 2
    if drawdown >= contract.drawdown_alert_1:
        return 1
    return 0


def refresh_canary_state(
    db: Session,
    model_pk: int,
    *,
    actual_total_equity: float,
    account_initial_cash: float,
    contract: CapitalContract | None = None,
) -> CanaryState:
    """Refresh P&L state and latch the stop without committing the caller's tx.

    ``high_water`` starts no lower than authorized capital, so introducing this
    table to an existing losing account does not erase its current drawdown.
    Alert history is monotonic.  Once stopped, recovery cannot silently resume
    buying; an explicit future reset workflow is required.
    """
    contract = contract or load_capital_contract()
    risk_equity = normalized_risk_equity(
        authorized_capital=contract.authorized_capital,
        actual_total_equity=actual_total_equity,
        account_initial_cash=account_initial_cash,
    )
    state = (
        db.query(CanaryState)
        .filter(CanaryState.model_pk == model_pk)
        .with_for_update()
        .first()
    )
    if state is None:
        state = CanaryState(
            model_pk=model_pk,
            status="active",
            high_water=max(contract.authorized_capital, risk_equity),
            risk_equity=risk_equity,
            drawdown=0.0,
            alert_level=0,
        )
        db.add(state)

    state.risk_equity = risk_equity
    state.high_water = max(
        float(state.high_water or 0.0), contract.authorized_capital, risk_equity)
    state.drawdown = max(state.high_water - risk_equity, 0.0)
    state.alert_level = max(
        int(state.alert_level or 0), _alert_level(state.drawdown, contract))

    if state.status == "stopped" or state.drawdown >= contract.canary_stop_drawdown:
        if state.status != "stopped":
            state.stopped_at = now()
        state.status = "stopped"
    else:
        state.status = "active"

    db.flush()
    return state


def alert_note(state: CanaryState) -> str:
    """Human-readable state note; alerts are informational, never allocations."""
    if state.status == "stopped":
        return f"Canary已停止新增风险（回撤 {state.drawdown:.0f} 元）"
    if state.alert_level >= 2:
        return f"Canary二级回撤告警 {state.drawdown:.0f} 元（仅告警）"
    if state.alert_level >= 1:
        return f"Canary一级回撤告警 {state.drawdown:.0f} 元（仅告警）"
    return ""
