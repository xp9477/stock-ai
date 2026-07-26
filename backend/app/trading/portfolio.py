"""账户估值与决策执行(含硬性风控)。"""
import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..data import market
from ..models import EquitySnapshot, Position
from . import broker

logger = logging.getLogger(__name__)


def position_value(pos: Position) -> float:
    quote = market.get_quote(pos.code)
    price = quote["price"] if quote else pos.avg_cost
    return pos.total_qty * price


def total_equity(db: Session) -> dict:
    account = broker.get_account(db)
    market_value = 0.0
    for pos in db.query(Position).all():
        market_value += position_value(pos)
    return {
        "cash": round(account.cash, 2),
        "market_value": round(market_value, 2),
        "total_equity": round(account.cash + market_value, 2),
        "initial_cash": account.initial_cash,
    }


def snapshot_equity(db: Session):
    eq = total_equity(db)
    db.add(EquitySnapshot(total_equity=eq["total_equity"], cash=eq["cash"],
                          market_value=eq["market_value"]))
    db.commit()


def apply_risk_limits(db: Session, code: str, action: str,
                      target_pct: float) -> tuple[str, float, str]:
    """代码层硬性风控,返回 (action, adjusted_target_pct, note)。"""
    notes = []
    if action == "buy":
        if target_pct > settings.max_position_pct:
            notes.append(f"单票仓位上限 {settings.max_position_pct:.0%},目标 {target_pct:.0%} 已压缩")
            target_pct = settings.max_position_pct

        eq = total_equity(db)
        current_market_pct = eq["market_value"] / eq["total_equity"] if eq["total_equity"] > 0 else 0
        pos = db.query(Position).filter(Position.code == code).first()
        current_pct = position_value(pos) / eq["total_equity"] if pos and eq["total_equity"] > 0 else 0
        add_pct = max(target_pct - current_pct, 0)

        # 总仓位不超过上限
        room = settings.max_total_position_pct - current_market_pct
        if add_pct > room:
            notes.append(f"总仓位上限 {settings.max_total_position_pct:.0%},加仓额度已压缩")
            add_pct = max(room, 0)

        # 单次买入不超过可用资金的一定比例
        max_cash = eq["cash"] * settings.max_buy_cash_pct
        add_amount = add_pct * eq["total_equity"]
        if add_amount > max_cash:
            notes.append(f"单次买入不超过可用资金 {settings.max_buy_cash_pct:.0%}")
            add_amount = max_cash
        if add_amount < 100:  # 无实际可买空间
            return "hold", current_pct, ";".join(notes) or "无加仓空间,转为持有"
        return "buy", current_pct + add_amount / eq["total_equity"], ";".join(notes)
    return action, target_pct, ";".join(notes)


def execute_decision(db: Session, run_id: int | None, code: str, name: str,
                     action: str, target_pct: float) -> broker.FillResult | None:
    """将最终决策(目标仓位)换算成订单并撮合。hold 返回 None。"""
    if action == "hold":
        return None
    quote = market.get_quote(code)
    if quote is None:
        logger.warning("无法获取 %s 行情,跳过执行", code)
        return None
    price, pct_change = quote["price"], quote["pct_change"]
    eq = total_equity(db)
    pos = db.query(Position).filter(Position.code == code).first()
    current_value = position_value(pos) if pos else 0.0
    target_value = target_pct * eq["total_equity"]

    if action == "buy":
        delta = target_value - current_value
        if delta < price * 100:
            return None
        return broker.buy(db, run_id, code, name, price, pct_change, delta)

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
        return broker.sell(db, run_id, code, name, price, pct_change, qty)
    return None
