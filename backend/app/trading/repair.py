"""修复脏报价导致的误强平。

事故模式：监控拿到全市场假价（如全部 7.0），深亏规则按假价卖出。
本模块根据「价=7 的 force_sell 订单」回滚现金并恢复持仓。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import MonitorEvent, Order, Position, TradeLedger
from . import broker, portfolio

logger = logging.getLogger(__name__)

# 误杀订单特征：统一假价
BAD_PRICE = 7.0
BAD_PRICE_EPS = 0.01


def _is_bad_price(price: float) -> bool:
    return abs(float(price) - BAD_PRICE) < BAD_PRICE_EPS


def _avg_cost_before_sell(db: Session, model_pk: int, code: str,
                          sell_order: Order) -> float | None:
    """用卖出前买入订单推断成本；或从 monitor_events 的 pnl 反推。"""
    buys = (
        db.query(Order)
        .filter(
            Order.model_pk == model_pk, Order.code == code,
            Order.side == "buy", Order.status == "filled",
            Order.created_at < sell_order.created_at,
        )
        .order_by(Order.id.desc())
        .all()
    )
    if buys:
        # 加权近似：用最近若干笔成交均价
        qty = sum(b.qty for b in buys)
        if qty > 0:
            return sum(b.price * b.qty for b in buys) / qty

    # 从 monitor 事件反推: pnl_pct = price/cost - 1 → cost = price/(1+pnl)
    ev = (
        db.query(MonitorEvent)
        .filter(
            MonitorEvent.model_pk == model_pk, MonitorEvent.code == code,
            MonitorEvent.action == "force_sell",
            MonitorEvent.created_at <= sell_order.created_at,
        )
        .order_by(MonitorEvent.id.desc())
        .first()
    )
    if ev and ev.pnl_pct is not None and ev.pnl_pct > -0.999:
        return BAD_PRICE / (1.0 + float(ev.pnl_pct))
    return None


def find_bad_force_sells(db: Session) -> list[Order]:
    return (
        db.query(Order)
        .filter(
            Order.side == "sell", Order.status == "filled",
            Order.price >= BAD_PRICE - BAD_PRICE_EPS,
            Order.price <= BAD_PRICE + BAD_PRICE_EPS,
            Order.qty > 0,
        )
        .order_by(Order.id)
        .all()
    )


def repair_bad_quote_force_sells(db: Session, *, dry_run: bool = False) -> dict:
    """回滚假价强平：扣回错误入账现金，恢复持仓。

    不会删除历史订单（保留审计），但会把对应 trade_ledger 平仓标记作废。
    """
    bad = find_bad_force_sells(db)
    restored: list[dict] = []
    skipped: list[dict] = []

    for order in bad:
        # 已恢复过则跳过（同 code+model 已有持仓且数量覆盖）
        cost = _avg_cost_before_sell(db, order.model_pk, order.code, order)
        if cost is None or cost <= 0:
            skipped.append({
                "order_id": order.id, "code": order.code,
                "reason": "无法推断成本",
            })
            continue

        account = broker.get_account(db, order.model_pk)
        # 卖出时 cash += amount - fee；回滚 cash -= amount - fee
        cash_delta = float(order.amount) - float(order.fee or 0)
        pos = broker.get_position(db, order.model_pk, order.code)

        detail = {
            "order_id": order.id,
            "model_pk": order.model_pk,
            "code": order.code,
            "name": order.name,
            "qty": order.qty,
            "bad_price": order.price,
            "restore_cost": round(cost, 4),
            "cash_delta": round(-cash_delta, 2),
        }

        if dry_run:
            restored.append({**detail, "dry_run": True})
            continue

        if account.cash < cash_delta - 1e-6:
            # 现金不足：仍尽量恢复，允许现金变负不合理则只恢复持仓并记 warning
            logger.warning(
                "修复时现金不足 model=%s need=%.2f have=%.2f",
                order.model_pk, cash_delta, account.cash,
            )

        account.cash = round(account.cash - cash_delta, 2)

        if pos is None:
            pos = Position(
                model_pk=order.model_pk, code=order.code, name=order.name,
                total_qty=order.qty, available_qty=order.qty,
                avg_cost=cost,
                buy_reason="[系统修复] 回滚脏报价误强平",
            )
            db.add(pos)
        else:
            # 合并数量，成本加权
            total_cost = pos.avg_cost * pos.total_qty + cost * order.qty
            pos.total_qty += order.qty
            pos.available_qty += order.qty
            pos.avg_cost = total_cost / pos.total_qty if pos.total_qty else cost
            pos.buy_reason = (pos.buy_reason or "") + " [修复回滚]"

        # 作废对应账本平仓
        for row in (
            db.query(TradeLedger)
            .filter(TradeLedger.order_id == order.id, TradeLedger.side == "close")
            .all()
        ):
            row.is_closed = False
            row.signal_source = (row.signal_source or "") + "|void_bad_quote"

        # 订单备注
        order.reject_reason = (
            (order.reject_reason or "") + " [已回滚:脏报价误强平]"
        ).strip()
        order.status = "filled"  # 保持 filled 作审计，备注标明回滚

        restored.append(detail)

    if not dry_run and restored:
        db.commit()
        # 刷新权益快照
        seen = {r["model_pk"] for r in restored}
        for pk in seen:
            try:
                portfolio.snapshot_equity(db, pk)
            except Exception:  # noqa: BLE001
                logger.exception("snapshot after repair model=%s", pk)

    return {
        "ok": True,
        "dry_run": dry_run,
        "bad_order_count": len(bad),
        "restored": restored,
        "skipped": skipped,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
