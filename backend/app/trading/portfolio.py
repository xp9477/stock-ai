"""账户估值与决策执行(多账户,含硬性风控)。"""
import logging

from sqlalchemy.orm import Session

from ..data import market
from ..models import EquitySnapshot, Position
from ..runtime_settings import get_setting
from . import broker

logger = logging.getLogger(__name__)


def position_value(pos: Position) -> float:
    quote = market.get_quote(pos.code)
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


def apply_risk_limits(db: Session, model_pk: int, code: str, action: str,
                      target_pct: float) -> tuple[str, float, str]:
    """代码层硬性风控,返回 (action, adjusted_target_pct, note)。"""
    notes = []
    if action == "buy":
        max_pos = float(get_setting("risk.max_position_pct"))
        max_total = float(get_setting("risk.max_total_position_pct"))
        max_buy_cash = float(get_setting("risk.max_buy_cash_pct"))
        if target_pct > max_pos:
            notes.append(f"单票仓位上限 {max_pos:.0%},目标 {target_pct:.0%} 已压缩")
            target_pct = max_pos

        eq = total_equity(db, model_pk)
        current_market_pct = eq["market_value"] / eq["total_equity"] if eq["total_equity"] > 0 else 0
        pos = broker.get_position(db, model_pk, code)
        current_pct = position_value(pos) / eq["total_equity"] if pos and eq["total_equity"] > 0 else 0
        add_pct = max(target_pct - current_pct, 0)

        room = max_total - current_market_pct
        if add_pct > room:
            notes.append(f"总仓位上限 {max_total:.0%},加仓额度已压缩")
            add_pct = max(room, 0)

        max_cash = eq["cash"] * max_buy_cash
        add_amount = add_pct * eq["total_equity"]
        if add_amount > max_cash:
            notes.append(f"单次买入不超过可用资金 {max_buy_cash:.0%}")
            add_amount = max_cash
        if add_amount < 100:
            return "hold", current_pct, ";".join(notes) or "无加仓空间,转为持有"
        return "buy", current_pct + add_amount / eq["total_equity"], ";".join(notes)
    return action, target_pct, ";".join(notes)


def execute_decision(db: Session, model_pk: int, run_id: int | None, code: str,
                     name: str, action: str, target_pct: float,
                     reason: str = "") -> broker.FillResult | None:
    """将最终决策(目标仓位)换算成订单并撮合。hold 返回 None。"""
    if action == "hold":
        return None
    quote = market.get_quote(code)
    if quote is None:
        logger.warning("无法获取 %s 行情,跳过执行", code)
        return None
    price, pct_change = quote["price"], quote["pct_change"]
    eq = total_equity(db, model_pk)
    pos = broker.get_position(db, model_pk, code)
    current_value = position_value(pos) if pos else 0.0
    target_value = target_pct * eq["total_equity"]

    if action == "buy":
        delta = target_value - current_value
        return broker.buy(db, model_pk, run_id, code, name, price, pct_change,
                          delta, reason)

    if action == "sell":
        if pos is None:
            return None
        delta_value = current_value - target_value
        if target_pct <= 0.005:
            qty = pos.available_qty  # 清仓
        else:
            qty = int(delta_value / price / 100) * 100
        if qty <= 0:
            return None
        return broker.sell(db, model_pk, run_id, code, name, price, pct_change, qty)
    return None
