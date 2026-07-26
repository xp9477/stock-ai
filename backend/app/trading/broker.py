"""模拟撮合引擎:市价单即时成交,遵守 A 股 T+1 / 整手 / 涨跌停 / 费用规则。"""
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Account, Order, Position

LIMIT_PCT = 9.8  # 涨跌停近似阈值(%)


@dataclass
class FillResult:
    ok: bool
    order: Order | None = None
    reason: str = ""


def get_account(db: Session) -> Account:
    account = db.query(Account).first()
    if account is None:
        account = Account(cash=settings.initial_cash, initial_cash=settings.initial_cash)
        db.add(account)
        db.commit()
    return account


def calc_buy_fee(amount: float) -> float:
    commission = max(amount * settings.commission_rate, settings.commission_min)
    transfer = amount * settings.transfer_fee_rate
    return round(commission + transfer, 2)


def calc_sell_fee(amount: float) -> float:
    commission = max(amount * settings.commission_rate, settings.commission_min)
    stamp = amount * settings.stamp_tax_rate
    transfer = amount * settings.transfer_fee_rate
    return round(commission + stamp + transfer, 2)


def settle_t1(db: Session):
    """每轮运行前调用:可卖数量 = 总数量 - 当日已买入数量(T+1)。"""
    from datetime import datetime

    today_start = datetime.combine(date.today(), datetime.min.time())
    for pos in db.query(Position).all():
        bought_today = (
            db.query(Order)
            .filter(Order.code == pos.code, Order.side == "buy",
                    Order.status == "filled", Order.created_at >= today_start)
            .all()
        )
        frozen = sum(order.qty for order in bought_today)
        pos.available_qty = max(pos.total_qty - frozen, 0)
    db.commit()


def buy(db: Session, run_id: int | None, code: str, name: str,
        price: float, pct_change: float, target_amount: float) -> FillResult:
    """按目标金额买入,向下取整为 100 股整手。"""

    def reject(reason: str) -> FillResult:
        order = Order(run_id=run_id, code=code, name=name, side="buy", price=price,
                      qty=0, amount=0, fee=0, status="rejected", reject_reason=reason)
        db.add(order)
        db.commit()
        return FillResult(False, order, reason)

    if pct_change >= LIMIT_PCT:
        return reject("涨停,不追买")

    account = get_account(db)
    qty = int(target_amount / price / 100) * 100
    if qty <= 0:
        return reject("目标金额不足一手")

    while qty > 0:
        amount = qty * price
        fee = calc_buy_fee(amount)
        if amount + fee <= account.cash:
            break
        qty -= 100
    if qty <= 0:
        return reject("可用资金不足")

    amount = qty * price
    fee = calc_buy_fee(amount)
    account.cash -= amount + fee

    pos = db.query(Position).filter(Position.code == code).first()
    if pos is None:
        pos = Position(code=code, name=name, total_qty=qty, available_qty=0,
                       avg_cost=(amount + fee) / qty)
        db.add(pos)
    else:
        total_cost = pos.avg_cost * pos.total_qty + amount + fee
        pos.total_qty += qty
        pos.avg_cost = total_cost / pos.total_qty

    order = Order(run_id=run_id, code=code, name=name, side="buy",
                  price=price, qty=qty, amount=round(amount, 2), fee=fee)
    db.add(order)
    db.commit()
    return FillResult(True, order)


def sell(db: Session, run_id: int | None, code: str, name: str,
         price: float, pct_change: float, qty: int) -> FillResult:
    """卖出指定数量(受 T+1 可卖数量限制,自动取整手;清仓时允许零股)。"""

    def reject(reason: str) -> FillResult:
        order = Order(run_id=run_id, code=code, name=name, side="sell", price=price,
                      qty=0, amount=0, fee=0, status="rejected", reject_reason=reason)
        db.add(order)
        db.commit()
        return FillResult(False, order, reason)

    if pct_change <= -LIMIT_PCT:
        return reject("跌停,不杀跌")

    pos = db.query(Position).filter(Position.code == code).first()
    if pos is None or pos.total_qty <= 0:
        return reject("无持仓")
    if pos.available_qty <= 0:
        return reject("T+1 限制,今日买入不可卖")

    qty = min(qty, pos.available_qty)
    if qty < pos.available_qty:
        qty = int(qty / 100) * 100  # 部分卖出取整手
    if qty <= 0:
        return reject("卖出数量不足一手")

    amount = qty * price
    fee = calc_sell_fee(amount)
    account = get_account(db)
    account.cash += amount - fee

    pos.total_qty -= qty
    pos.available_qty -= qty
    if pos.total_qty <= 0:
        db.delete(pos)

    order = Order(run_id=run_id, code=code, name=name, side="sell",
                  price=price, qty=qty, amount=round(amount, 2), fee=fee)
    db.add(order)
    db.commit()
    return FillResult(True, order)
