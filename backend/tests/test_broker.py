from app.config import settings
from app.models import Order, Position
from app.trading import broker


def test_buy_rounds_to_lot_and_deducts_fee(db):
    result = broker.buy(db, None, "600519", "贵州茅台", price=1500.0,
                        pct_change=1.0, target_amount=160000)
    assert result.ok
    assert result.order.qty == 100  # 160000/1500=106.7 -> 100 股整手
    amount = 100 * 1500.0
    fee = broker.calc_buy_fee(amount)
    account = broker.get_account(db)
    assert abs(account.cash - (settings.initial_cash - amount - fee)) < 0.01
    pos = db.query(Position).filter(Position.code == "600519").first()
    assert pos.total_qty == 100
    assert pos.available_qty == 0  # T+1 当日不可卖


def test_buy_rejected_at_limit_up(db):
    result = broker.buy(db, None, "300001", "测试", price=10.0,
                        pct_change=10.0, target_amount=10000)
    assert not result.ok
    assert "涨停" in result.reason


def test_buy_rejected_insufficient_cash(db):
    account = broker.get_account(db)
    account.cash = 500.0
    db.commit()
    result = broker.buy(db, None, "300001", "测试", price=10.0,
                        pct_change=0.0, target_amount=10000)
    assert not result.ok
    assert "资金不足" in result.reason


def test_sell_blocked_by_t1(db):
    broker.buy(db, None, "600519", "贵州茅台", price=1500.0,
               pct_change=0.0, target_amount=160000)
    result = broker.sell(db, None, "600519", "贵州茅台", price=1510.0,
                         pct_change=0.5, qty=100)
    assert not result.ok
    assert "T+1" in result.reason


def test_sell_after_t1_settle(db):
    broker.buy(db, None, "600519", "贵州茅台", price=1500.0,
               pct_change=0.0, target_amount=160000)
    pos = db.query(Position).filter(Position.code == "600519").first()
    pos.available_qty = pos.total_qty  # 模拟隔日
    db.commit()
    result = broker.sell(db, None, "600519", "贵州茅台", price=1600.0,
                         pct_change=1.0, qty=100)
    assert result.ok
    amount = 100 * 1600.0
    fee = broker.calc_sell_fee(amount)
    assert abs(result.order.fee - fee) < 0.01
    assert db.query(Position).filter(Position.code == "600519").first() is None


def test_sell_rejected_at_limit_down(db):
    broker.buy(db, None, "300001", "测试", price=10.0, pct_change=0.0,
               target_amount=10000)
    pos = db.query(Position).filter(Position.code == "300001").first()
    pos.available_qty = pos.total_qty
    db.commit()
    result = broker.sell(db, None, "300001", "测试", price=9.0,
                         pct_change=-10.0, qty=100)
    assert not result.ok
    assert "跌停" in result.reason


def test_sell_fee_includes_stamp_tax():
    amount = 100000.0
    fee = broker.calc_sell_fee(amount)
    expected = max(amount * 0.00025, 5.0) + amount * 0.0005 + amount * 0.00001
    assert abs(fee - expected) < 0.01


def test_buy_fee_minimum_commission():
    fee = broker.calc_buy_fee(1000.0)
    assert fee >= 5.0


def test_settle_t1_respects_today_buys(db):
    broker.buy(db, None, "600519", "贵州茅台", price=1500.0,
               pct_change=0.0, target_amount=320000)
    broker.settle_t1(db)
    pos = db.query(Position).filter(Position.code == "600519").first()
    assert pos.available_qty == 0  # 今日买入仍冻结

    # 把订单改成昨天 -> 解冻
    from datetime import datetime, timedelta
    for order in db.query(Order).all():
        order.created_at = datetime.now() - timedelta(days=1)
    db.commit()
    broker.settle_t1(db)
    pos = db.query(Position).filter(Position.code == "600519").first()
    assert pos.available_qty == pos.total_qty
