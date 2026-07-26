from app.config import settings
from app.models import Order, Position
from app.trading import broker


def test_buy_rounds_to_lot_and_deducts_fee(db, model_a):
    pk = model_a.id
    result = broker.buy(db, pk, None, "600519", "贵州茅台", price=1500.0,
                        pct_change=1.0, target_amount=160000)
    assert result.ok
    assert result.order.qty == 100
    amount = 100 * 1500.0
    fee = broker.calc_buy_fee(amount)
    account = broker.get_account(db, pk)
    assert abs(account.cash - (settings.initial_cash - amount - fee)) < 0.01
    pos = broker.get_position(db, pk, "600519")
    assert pos.total_qty == 100
    assert pos.available_qty == 0  # T+1 当日不可卖


def test_buy_rejected_at_limit_up(db, model_a):
    result = broker.buy(db, model_a.id, None, "300001", "测试", price=10.0,
                        pct_change=10.0, target_amount=10000)
    assert not result.ok
    assert "涨停" in result.reason


def test_buy_rejected_insufficient_cash(db, model_a):
    account = broker.get_account(db, model_a.id)
    account.cash = 500.0
    db.commit()
    result = broker.buy(db, model_a.id, None, "300001", "测试", price=10.0,
                        pct_change=0.0, target_amount=10000)
    assert not result.ok
    assert "资金不足" in result.reason


def test_sell_blocked_by_t1(db, model_a):
    pk = model_a.id
    broker.buy(db, pk, None, "600519", "贵州茅台", price=1500.0,
               pct_change=0.0, target_amount=160000)
    result = broker.sell(db, pk, None, "600519", "贵州茅台", price=1510.0,
                         pct_change=0.5, qty=100)
    assert not result.ok
    assert "T+1" in result.reason


def test_sell_after_t1_settle(db, model_a):
    pk = model_a.id
    broker.buy(db, pk, None, "600519", "贵州茅台", price=1500.0,
               pct_change=0.0, target_amount=160000)
    pos = broker.get_position(db, pk, "600519")
    pos.available_qty = pos.total_qty
    db.commit()
    result = broker.sell(db, pk, None, "600519", "贵州茅台", price=1600.0,
                         pct_change=1.0, qty=100)
    assert result.ok
    fee = broker.calc_sell_fee(100 * 1600.0)
    assert abs(result.order.fee - fee) < 0.01
    assert broker.get_position(db, pk, "600519") is None


def test_sell_rejected_at_limit_down(db, model_a):
    pk = model_a.id
    broker.buy(db, pk, None, "300001", "测试", price=10.0, pct_change=0.0,
               target_amount=10000)
    pos = broker.get_position(db, pk, "300001")
    pos.available_qty = pos.total_qty
    db.commit()
    result = broker.sell(db, pk, None, "300001", "测试", price=9.0,
                         pct_change=-10.0, qty=100)
    assert not result.ok
    assert "跌停" in result.reason


def test_sell_fee_includes_stamp_tax():
    amount = 100000.0
    fee = broker.calc_sell_fee(amount)
    expected = max(amount * 0.00025, 5.0) + amount * 0.0005 + amount * 0.00001
    assert abs(fee - expected) < 0.01


def test_buy_fee_minimum_commission():
    assert broker.calc_buy_fee(1000.0) >= 5.0


def test_settle_t1_respects_today_buys(db, model_a):
    pk = model_a.id
    broker.buy(db, pk, None, "600519", "贵州茅台", price=1500.0,
               pct_change=0.0, target_amount=320000)
    broker.settle_t1(db)
    pos = broker.get_position(db, pk, "600519")
    assert pos.available_qty == 0

    from datetime import datetime, timedelta
    for order in db.query(Order).all():
        order.created_at = datetime.now() - timedelta(days=1)
    db.commit()
    broker.settle_t1(db)
    pos = broker.get_position(db, pk, "600519")
    assert pos.available_qty == pos.total_qty


def test_multi_account_isolation(db, model_a, model_b):
    """A 模型买入不影响 B 模型现金与持仓。"""
    broker.buy(db, model_a.id, None, "600519", "贵州茅台", price=1500.0,
               pct_change=0.0, target_amount=160000)
    account_b = broker.get_account(db, model_b.id)
    assert account_b.cash == settings.initial_cash
    assert broker.get_position(db, model_b.id, "600519") is None
    assert broker.get_position(db, model_a.id, "600519") is not None
