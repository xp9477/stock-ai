"""盘中监控：分层3（浅线告警 / 深亏强制砍）。"""
from unittest.mock import patch

from app.agents import monitor
from app.config import settings
from app.models import MonitorEvent, Position
from app.trading import broker


def make_position(db, model_pk, cost=10.0, qty=1000):
    pos = Position(model_pk=model_pk, code="000001", name="测试股",
                   total_qty=qty, available_qty=qty, avg_cost=cost,
                   buy_reason="测试买入")
    db.add(pos)
    db.commit()
    return pos


def test_no_trigger_in_normal_range(db, model_a):
    assert monitor.should_review(db, model_a.id, "000001", -0.05) is None
    assert monitor.should_review(db, model_a.id, "000001", 0.10) is None


def test_shallow_stop_loss_alerts_once_per_day(db, model_a):
    assert settings.shallow_line_alert_only is True
    assert monitor.should_review(db, model_a.id, "000001", -0.09) == "stop_loss"
    db.add(MonitorEvent(model_pk=model_a.id, code="000001", pnl_pct=-0.09,
                        trigger="stop_loss", action="alert"))
    db.commit()
    assert monitor.should_review(db, model_a.id, "000001", -0.10) is None


def test_shallow_take_profit_alerts_once_per_day(db, model_a):
    assert monitor.should_review(db, model_a.id, "000001", 0.16) == "take_profit"
    db.add(MonitorEvent(model_pk=model_a.id, code="000001", pnl_pct=0.16,
                        trigger="take_profit", action="alert"))
    db.commit()
    assert monitor.should_review(db, model_a.id, "000001", 0.20) is None


def test_deep_loss_once_per_day_after_force_sell(db, model_a):
    assert monitor.should_review(db, model_a.id, "000001", -0.18) == "deep_loss"
    db.add(MonitorEvent(model_pk=model_a.id, code="000001", pnl_pct=-0.18,
                        trigger="deep_loss", action="force_sell"))
    db.commit()
    assert monitor.should_review(db, model_a.id, "000001", -0.20) is None


def test_shallow_alert_does_not_sell(db, model_a):
    broker.get_account(db, model_a.id)
    pos = make_position(db, model_a.id)
    event = monitor.review_position(db, pos, price=9.0, pct_change=-2.0,
                                    pnl_pct=-0.10, trigger="stop_loss")
    assert event.action == "alert"
    assert broker.get_position(db, model_a.id, "000001") is not None


def test_deep_loss_force_sells(db, model_a):
    broker.get_account(db, model_a.id)
    pos = make_position(db, model_a.id)
    event = monitor.review_position(db, pos, price=8.0, pct_change=-5.0,
                                    pnl_pct=-0.20, trigger="deep_loss")
    assert event.action == "force_sell"
    assert broker.get_position(db, model_a.id, "000001") is None
