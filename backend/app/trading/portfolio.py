"""账户估值、票据授权与决策执行（含硬性资金契约）。"""
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy.orm import Session

from ..data import market
from ..ledger import record_close_from_sell, record_open, strategy_key_for_model
from ..models import Account, EquitySnapshot, ExecutionIntent, Model, Position, TradePlan
from ..runtime_settings import get_setting
from . import broker, risk_contract

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TicketAuthorization:
    """Deterministic authorization frozen into a human order ticket."""

    allowed: bool
    reason_code: str
    reason: str
    side: str
    authorized_target_position_pct: float
    authorized_notional: float
    authorized_qty: int
    estimated_fee: float
    risk_snapshot: dict[str, Any]


def _ticket_result(
    *, allowed: bool, reason_code: str, reason: str, side: str,
    target_pct: float = 0.0, notional: float = 0.0, qty: int = 0,
    fee: float = 0.0, snapshot: dict[str, Any] | None = None,
) -> TicketAuthorization:
    return TicketAuthorization(
        allowed=allowed,
        reason_code=reason_code,
        reason=reason,
        side=side,
        authorized_target_position_pct=round(float(target_pct), 8),
        authorized_notional=round(float(notional), 2),
        authorized_qty=int(qty),
        estimated_fee=round(float(fee), 2),
        risk_snapshot=snapshot or {},
    )


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def position_value(pos: Position) -> float:
    quote = market.get_trade_quote(pos.code, avg_cost=pos.avg_cost)
    price = quote["price"] if quote else pos.avg_cost
    return pos.total_qty * price


def total_equity(db: Session, model_pk: int) -> dict:
    account = broker.get_account(db, model_pk)
    market_value = 0.0
    for pos in db.query(Position).filter(Position.model_pk == model_pk).all():
        market_value += position_value(pos)
    return {
        "cash": round(account.cash, 2),
        "market_value": round(market_value, 2),
        "total_equity": round(account.cash + market_value, 2),
        "initial_cash": account.initial_cash,
    }


def snapshot_equity(db: Session, model_pk: int):
    eq = total_equity(db, model_pk)
    db.add(EquitySnapshot(model_pk=model_pk, total_equity=eq["total_equity"],
                          cash=eq["cash"], market_value=eq["market_value"]))
    db.commit()


def _capital_state(db: Session, model_pk: int):
    """Return current bookkeeping equity plus normalized canary state."""
    eq = total_equity(db, model_pk)
    contract = risk_contract.load_capital_contract()
    state = risk_contract.refresh_canary_state(
        db,
        model_pk,
        actual_total_equity=eq["total_equity"],
        account_initial_cash=eq["initial_cash"],
        contract=contract,
    )
    return eq, contract, state


def _append_note(notes: list[str], note: str) -> None:
    if note and note not in notes:
        notes.append(note)


def authorize_execution_intent(
    db: Session,
    *,
    model_pk: int,
    code: str,
    side: str,
    target_position_pct: float,
    approval_quote: dict[str, Any],
    authorization_time: datetime | None = None,
    require_session: bool = True,
) -> TicketAuthorization:
    """Re-run the hard capital contract immediately before ticket creation.

    This function is deliberately stricter than the analytical risk pass.  It
    values every open position with a fresh execution-grade quote, reserves
    capacity already promised by unexpired tickets, and never creates an
    account.  A missing quote or account therefore blocks new risk instead of
    falling back to cost or silently capitalizing another model.

    The result only authorizes a *manual ticket*.  It never mutates cash,
    positions or orders.  Canary stop-buy applies to BUY only; SELL remains
    available so risk can still be reduced.
    """
    checked_at = _utc(authorization_time)
    normalized_side = str(side or "").lower()
    base_snapshot: dict[str, Any] = {
        "checked_at": checked_at.isoformat(),
        "model_pk": model_pk,
        "code": code,
        "side": normalized_side,
        "requested_target_position_pct": float(target_position_pct or 0.0),
    }
    if normalized_side not in {"buy", "sell"}:
        return _ticket_result(
            allowed=False, reason_code="unsupported_side",
            reason="票据方向不是 buy/sell，不能授权", side=normalized_side,
            snapshot=base_snapshot,
        )

    model = db.get(Model, model_pk)
    if (
        model is None
        or model.type != "ensemble"
        or not model.is_official_strategy
        or not model.enabled
    ):
        return _ticket_result(
            allowed=False, reason_code="not_official_strategy",
            reason="只有已启用且数据库唯一标记的官方 ensemble 策略可以生成资金票据",
            side=normalized_side, snapshot=base_snapshot,
        )
    account = (
        db.query(Account)
        .filter(Account.model_pk == model_pk)
        .with_for_update()
        .first()
    )
    if account is None:
        return _ticket_result(
            allowed=False, reason_code="missing_strategy_account",
            reason="官方策略账户不存在；禁止在审批时隐式创建资金账户",
            side=normalized_side, snapshot=base_snapshot,
        )

    try:
        price = float(approval_quote.get("price"))
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0 or not bool(approval_quote.get("tradable", True)):
        return _ticket_result(
            allowed=False, reason_code="invalid_approval_quote",
            reason="审批报价不可交易或价格无效", side=normalized_side,
            snapshot=base_snapshot,
        )

    contract = risk_contract.load_capital_contract()
    positions = (
        db.query(Position)
        .filter(Position.model_pk == model_pk, Position.total_qty > 0)
        .with_for_update()
        .all()
    )
    position = next((item for item in positions if item.code == code), None)

    # SELL is intentionally independent of the Canary stop.  It still freezes
    # an executable whole-lot quantity so a stale plan cannot sell shares that
    # are not currently available under T+1.
    if normalized_side == "sell":
        if position is None or position.available_qty <= 0:
            return _ticket_result(
                allowed=False, reason_code="no_available_position",
                reason="当前没有可卖持仓（含 T+1 可用数量检查）",
                side=normalized_side, snapshot=base_snapshot,
            )
        reserved_sell_qty = 0
        outstanding_sells = (
            db.query(ExecutionIntent, TradePlan)
            .join(TradePlan, TradePlan.id == ExecutionIntent.plan_id)
            .filter(
                ExecutionIntent.status == "ticket_ready",
                TradePlan.model_pk == model_pk,
                TradePlan.code == code,
                TradePlan.side == "sell",
            )
            .all()
        )
        for intent, _existing_plan in outstanding_sells:
            if _utc(intent.expires_at) > checked_at:
                reserved_sell_qty += max(int(intent.authorized_qty or 0), 0)
        remaining_available = max(position.available_qty - reserved_sell_qty, 0)
        projected_total_qty = max(position.total_qty - reserved_sell_qty, 0)
        if remaining_available <= 0:
            return _ticket_result(
                allowed=False, reason_code="sell_quantity_already_reserved",
                reason="当前可卖数量已被其他未过期卖出票据全部预留",
                side=normalized_side,
                snapshot={
                    **base_snapshot,
                    "available_qty": position.available_qty,
                    "reserved_sell_qty": reserved_sell_qty,
                },
            )
        current_notional = projected_total_qty * price
        requested_target = max(float(target_position_pct or 0.0), 0.0)
        target_notional = requested_target * contract.authorized_capital
        if requested_target <= 0.005:
            qty = int(remaining_available / 100) * 100
        else:
            reduction = max(current_notional - target_notional, 0.0)
            qty = min(
                int(remaining_available / 100) * 100,
                int(reduction / price / 100) * 100,
            )
        if qty <= 0:
            return _ticket_result(
                allowed=False, reason_code="no_sell_quantity",
                reason="按当前持仓与目标仓位计算后没有可卖整手数量",
                side=normalized_side, snapshot=base_snapshot,
            )
        notional = qty * price
        fee = broker.calc_sell_fee(notional)
        snapshot = {
            **base_snapshot,
            "authorized_capital": contract.authorized_capital,
            "canary_applies": False,
            "current_position_qty": position.total_qty,
            "available_qty": position.available_qty,
            "reserved_sell_qty": reserved_sell_qty,
            "remaining_available_qty": remaining_available,
            "projected_position_qty_before_ticket": projected_total_qty,
            "current_position_notional": round(current_notional, 2),
            "approval_quote_price": price,
            "approval_quote_asof": approval_quote.get("quote_asof"),
        }
        return _ticket_result(
            allowed=True, reason_code="sell_ticket_authorized",
            reason="卖出票据已按当前可用持仓冻结；Canary 停买不阻断卖出",
            side=normalized_side, target_pct=requested_target,
            notional=notional, qty=qty, fee=fee, snapshot=snapshot,
        )

    # BUY requires strict point-in-time valuation of the whole strategy book.
    quote_evidence: list[dict[str, Any]] = []
    market_value = 0.0
    current_value = 0.0
    for item in positions:
        if item.code == code:
            item_quote = approval_quote
        else:
            try:
                item_quote = market.get_execution_quote(
                    item.code,
                    avg_cost=item.avg_cost,
                    force_refresh=True,
                    require_session=require_session,
                )
            except Exception:  # noqa: BLE001 - valuation must fail closed
                logger.exception("审批前持仓估值失败 code=%s", item.code)
                item_quote = None
        if item_quote is None:
            snapshot = {**base_snapshot, "unpriced_position_code": item.code}
            return _ticket_result(
                allowed=False, reason_code="incomplete_portfolio_valuation",
                reason=f"持仓 {item.code} 缺少执行级新鲜报价，禁止新增风险",
                side=normalized_side, snapshot=snapshot,
            )
        try:
            item_price = float(item_quote.get("price"))
        except (TypeError, ValueError):
            item_price = 0.0
        if item_price <= 0 or not bool(item_quote.get("tradable", True)):
            snapshot = {**base_snapshot, "unpriced_position_code": item.code}
            return _ticket_result(
                allowed=False, reason_code="incomplete_portfolio_valuation",
                reason=f"持仓 {item.code} 报价无效或不可交易，禁止新增风险",
                side=normalized_side, snapshot=snapshot,
            )
        value = item.total_qty * item_price
        market_value += value
        if item.code == code:
            current_value = value
        quote_evidence.append({
            "code": item.code,
            "qty": item.total_qty,
            "price": item_price,
            "market_value": round(value, 2),
            "quote_asof": item_quote.get("quote_asof"),
            "source": item_quote.get("source"),
        })

    # Ticket capacity is a reservation.  Without this, two independently
    # approved tickets could each pass against the same cash and jointly break
    # the 80k exposure boundary before either one is recorded as a fill.
    reserved_notional = 0.0
    reserved_cash = 0.0
    reserved_new_codes: set[str] = set()
    outstanding = (
        db.query(ExecutionIntent, TradePlan)
        .join(TradePlan, TradePlan.id == ExecutionIntent.plan_id)
        .filter(
            ExecutionIntent.status == "ticket_ready",
            TradePlan.model_pk == model_pk,
            TradePlan.side == "buy",
        )
        .all()
    )
    held_codes = {item.code for item in positions}
    for intent, existing_plan in outstanding:
        if _utc(intent.expires_at) <= checked_at:
            continue
        if existing_plan.code == code:
            snapshot = {
                **base_snapshot,
                "conflicting_intent_id": intent.id,
                "conflicting_plan_id": existing_plan.id,
            }
            return _ticket_result(
                allowed=False, reason_code="outstanding_buy_ticket",
                reason="同一股票已有未过期买入票据，不能重复占用资金",
                side=normalized_side, snapshot=snapshot,
            )
        reserved_notional += float(intent.authorized_notional or 0.0)
        reserved_cash += (
            float(intent.authorized_notional or 0.0)
            + float(intent.estimated_fee or 0.0)
        )
        if existing_plan.code not in held_codes:
            reserved_new_codes.add(existing_plan.code)

    actual_total_equity = float(account.cash) + market_value
    state = risk_contract.refresh_canary_state(
        db,
        model_pk,
        actual_total_equity=actual_total_equity,
        account_initial_cash=float(account.initial_cash),
        contract=contract,
    )
    max_positions = int(get_setting("signal.max_positions"))
    max_pos = max(float(get_setting("risk.max_position_pct")), 0.0)
    max_total = max(float(get_setting("risk.max_total_position_pct")), 0.0)
    max_buy_cash = max(float(get_setting("risk.max_buy_cash_pct")), 0.0)
    open_slots_used = len(held_codes | reserved_new_codes)

    snapshot = {
        **base_snapshot,
        "authorized_capital": contract.authorized_capital,
        "max_stock_exposure": contract.max_stock_exposure,
        "account_cash": round(float(account.cash), 2),
        "account_initial_cash": round(float(account.initial_cash), 2),
        "market_value": round(market_value, 2),
        "actual_total_equity": round(actual_total_equity, 2),
        "risk_equity": round(float(state.risk_equity), 2),
        "high_water": round(float(state.high_water), 2),
        "drawdown": round(float(state.drawdown), 2),
        "canary_status": state.status,
        "canary_alert_level": int(state.alert_level),
        "open_position_codes": sorted(held_codes),
        "reserved_new_position_codes": sorted(reserved_new_codes),
        "position_slots_used": open_slots_used,
        "max_positions": max_positions,
        "reserved_buy_notional": round(reserved_notional, 2),
        "reserved_buy_cash": round(reserved_cash, 2),
        "position_quotes": quote_evidence,
        "approval_quote_price": price,
        "approval_quote_asof": approval_quote.get("quote_asof"),
        "risk_max_position_pct": max_pos,
        "risk_max_total_position_pct": max_total,
        "risk_max_buy_cash_pct": max_buy_cash,
    }
    if state.status == "stopped":
        return _ticket_result(
            allowed=False, reason_code="canary_stop_buy",
            reason=f"Canary 已永久停止新增买入风险（当前回撤 {state.drawdown:.0f} 元）",
            side=normalized_side, snapshot=snapshot,
        )
    if code not in held_codes and open_slots_used >= max_positions:
        return _ticket_result(
            allowed=False, reason_code="max_positions_reached",
            reason=f"当前持仓及已批准票据已占满 {max_positions} 个持仓名额",
            side=normalized_side, snapshot=snapshot,
        )

    requested_target = max(float(target_position_pct or 0.0), 0.0)
    requested_target_value = requested_target * contract.authorized_capital
    single_cap = max_pos * contract.authorized_capital
    target_value = min(requested_target_value, single_cap)
    desired_add = max(target_value - current_value, 0.0)
    percentage_cap = max_total * contract.authorized_capital
    percentage_room = max(
        percentage_cap - market_value - reserved_notional, 0.0)
    absolute_room = max(
        contract.max_stock_exposure - market_value - reserved_notional, 0.0)

    # Exclude all cash above the authorized capital, including legacy account
    # balances, then reserve both notional and estimated fees of earlier tickets.
    normalized_cash = max(float(state.risk_equity) - market_value, 0.0)
    strategy_cash = max(
        min(float(account.cash), normalized_cash) - reserved_cash, 0.0)
    per_buy_room = strategy_cash * max_buy_cash
    add_room = min(desired_add, percentage_room, absolute_room, per_buy_room)
    qty = int(add_room / price / 100) * 100
    while qty > 0:
        notional = qty * price
        fee = broker.calc_buy_fee(notional)
        if notional + fee <= strategy_cash + 1e-9:
            break
        qty -= 100
    if qty <= 0:
        snapshot.update({
            "requested_target_value": round(requested_target_value, 2),
            "current_position_value": round(current_value, 2),
            "percentage_exposure_room": round(percentage_room, 2),
            "absolute_exposure_room": round(absolute_room, 2),
            "strategy_cash_after_reservations": round(strategy_cash, 2),
            "per_buy_room": round(per_buy_room, 2),
        })
        return _ticket_result(
            allowed=False, reason_code="no_buy_capacity",
            reason="当前资金、敞口或整手约束下没有可批准的买入数量",
            side=normalized_side, snapshot=snapshot,
        )

    notional = qty * price
    fee = broker.calc_buy_fee(notional)
    authorized_target = (current_value + notional) / contract.authorized_capital
    snapshot.update({
        "requested_target_value": round(requested_target_value, 2),
        "single_position_cap": round(single_cap, 2),
        "current_position_value": round(current_value, 2),
        "percentage_exposure_room": round(percentage_room, 2),
        "absolute_exposure_room": round(absolute_room, 2),
        "strategy_cash_after_reservations": round(strategy_cash, 2),
        "per_buy_room": round(per_buy_room, 2),
        "authorized_qty": qty,
        "authorized_notional": round(notional, 2),
        "estimated_fee": fee,
        "authorized_target_position_pct": round(authorized_target, 8),
    })
    return _ticket_result(
        allowed=True, reason_code="buy_ticket_authorized",
        reason="资金、敞口、持仓数、整手和 Canary 门禁均已重新通过",
        side=normalized_side, target_pct=authorized_target,
        notional=notional, qty=qty, fee=fee, snapshot=snapshot,
    )


def apply_risk_limits(db: Session, model_pk: int, code: str, action: str,
                      target_pct: float) -> tuple[str, float, str]:
    """Apply the capital contract and candidate allocation constraints.

    All target percentages are interpreted against ``authorized_capital``.
    Drawdown levels 1 and 2 only annotate the decision.  The durable level-3
    stop converts a buy to hold, while sell decisions always pass through.
    """
    notes: list[str] = []
    eq, contract, state = _capital_state(db, model_pk)
    _append_note(notes, risk_contract.alert_note(state))

    if action != "buy":
        return action, target_pct, ";".join(notes)

    pos = broker.get_position(db, model_pk, code)
    current_value = position_value(pos) if pos else 0.0
    current_pct = current_value / contract.authorized_capital
    if state.status == "stopped":
        return "hold", current_pct, ";".join(notes) or "Canary已停止新增风险"

    max_positions = int(get_setting("signal.max_positions"))
    open_positions = (
        db.query(Position)
        .filter(Position.model_pk == model_pk, Position.total_qty > 0)
        .count()
    )
    if (pos is None or pos.total_qty <= 0) and open_positions >= max_positions:
        _append_note(notes, f"最大持仓只数 {max_positions}，不再新增标的")
        return "hold", current_pct, ";".join(notes)

    max_pos = float(get_setting("risk.max_position_pct"))
    max_total = float(get_setting("risk.max_total_position_pct"))
    max_buy_cash = float(get_setting("risk.max_buy_cash_pct"))
    requested_value = max(float(target_pct), 0.0) * contract.authorized_capital
    single_position_cap = max(max_pos, 0.0) * contract.authorized_capital
    target_value = min(requested_value, single_position_cap)
    if requested_value > single_position_cap:
        _append_note(
            notes, f"单票仓位上限 {max_pos:.0%}，目标 {target_pct:.0%} 已压缩")

    desired_add = max(target_value - current_value, 0.0)
    percentage_exposure_cap = max(max_total, 0.0) * contract.authorized_capital
    percentage_room = max(percentage_exposure_cap - eq["market_value"], 0.0)
    absolute_room = max(contract.max_stock_exposure - eq["market_value"], 0.0)
    exposure_room = min(percentage_room, absolute_room)
    if desired_add > percentage_room:
        _append_note(notes, f"总仓位上限 {max_total:.0%}，加仓额度已压缩")
    if desired_add > absolute_room:
        _append_note(
            notes,
            f"股票绝对敞口上限 {contract.max_stock_exposure:.0f} 元，加仓额度已压缩",
        )

    # Cash usable by the strategy excludes every yuan above the authorized
    # capital, even when a legacy broker account still holds one million yuan.
    normalized_cash = max(state.risk_equity - eq["market_value"], 0.0)
    strategy_cash = min(max(eq["cash"], 0.0), normalized_cash)
    per_buy_cap = strategy_cash * max(max_buy_cash, 0.0)
    if desired_add > per_buy_cap:
        _append_note(notes, f"单次买入不超过授权可用资金 {max_buy_cash:.0%}")

    add_amount = min(desired_add, exposure_room, per_buy_cap)
    if add_amount < 100:
        _append_note(notes, "无加仓空间，转为持有")
        return "hold", current_pct, ";".join(notes)
    return (
        "buy",
        (current_value + add_amount) / contract.authorized_capital,
        ";".join(notes),
    )


def execute_decision(db: Session, model_pk: int, run_id: int | None, code: str,
                     name: str, action: str, target_pct: float,
                     reason: str = "", *, autocommit: bool = True) -> broker.FillResult | None:
    """将最终决策(目标仓位)换算成订单并撮合。hold 返回 None。

    成交后写入 trade_ledger，保证 AI / 合议臂与规则臂共享同一套样本门槛。
    """
    try:
        result = _execute_decision(
            db, model_pk, run_id, code, name, action, target_pct, reason)
        if autocommit:
            db.commit()
        return result
    except Exception:
        if autocommit:
            db.rollback()
        raise


def _execute_decision(db: Session, model_pk: int, run_id: int | None, code: str,
                      name: str, action: str, target_pct: float,
                      reason: str) -> broker.FillResult | None:
    """Pure orchestration for one caller-owned database transaction."""
    requested_action = action
    action, target_pct, risk_note = apply_risk_limits(
        db, model_pk, code, action, target_pct)
    if action == "hold":
        if requested_action == "buy":
            return broker.FillResult(False, reason=risk_note or "买入被资金契约拒绝")
        return None
    pos = broker.get_position(db, model_pk, code)
    quote = market.get_trade_quote(code, avg_cost=pos.avg_cost if pos else None)
    if quote is None:
        reason_text = "交易行情未通过双源/历史价格安全校验"
        logger.warning("%s: %s", code, reason_text)
        return broker.FillResult(False, reason=reason_text)
    price, pct_change = quote["price"], quote["pct_change"]
    current_value = position_value(pos) if pos else 0.0
    contract = risk_contract.load_capital_contract()
    target_value = target_pct * contract.authorized_capital
    model = db.get(Model, model_pk)
    sk = strategy_key_for_model(model_pk, model.type if model else "llm")

    if action == "buy":
        delta = target_value - current_value
        result = broker.buy(db, model_pk, run_id, code, name, price, pct_change,
                            delta, reason, autocommit=False)
        if result.ok and result.order:
            record_open(
                db, strategy_key=sk, model_pk=model_pk, code=code, name=name,
                qty=result.order.qty, price=price, signal_source="ai",
                reason=reason or "", order_id=result.order.id, autocommit=False,
            )
        return result

    if action == "sell":
        if pos is None:
            return None
        avg_cost = pos.avg_cost
        delta_value = current_value - target_value
        if target_pct <= 0.005:
            qty = pos.available_qty  # 清仓
        else:
            qty = int(delta_value / price / 100) * 100
        if qty <= 0:
            return None
        result = broker.sell(
            db, model_pk, run_id, code, name, price, pct_change, qty, autocommit=False)
        if result.ok and result.order:
            record_close_from_sell(
                db, strategy_key=sk, model_pk=model_pk, code=code, name=name,
                qty=result.order.qty, price=price, signal_source="ai",
                order_id=result.order.id, avg_cost=avg_cost, autocommit=False,
            )
        return result
    return None
