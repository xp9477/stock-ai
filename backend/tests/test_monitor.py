"""盘中监控触发规则与复审执行测试。"""
from datetime import datetime, timedelta
from unittest.mock import patch

from app.agents import monitor
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


def test_stop_loss_trigger_once_per_day(db, model_a):
    assert monitor.should_review(db, model_a.id, "000001", -0.09) == "stop_loss"
    db.add(MonitorEvent(model_pk=model_a.id, code="000001", pnl_pct=-0.09,
                        trigger="stop_loss", action="review_hold"))
    db.commit()
    assert monitor.should_review(db, model_a.id, "000001", -0.10) is None


def test_take_profit_trigger_once_per_day(db, model_a):
    assert monitor.should_review(db, model_a.id, "000001", 0.16) == "take_profit"
    db.add(MonitorEvent(model_pk=model_a.id, code="000001", pnl_pct=0.16,
                        trigger="take_profit", action="review_hold"))
    db.commit()
    assert monitor.should_review(db, model_a.id, "000001", 0.20) is None


def test_deep_loss_bypasses_daily_limit_with_hour_gap(db, model_a):
    event = MonitorEvent(model_pk=model_a.id, code="000001", pnl_pct=-0.16,
                         trigger="deep_loss", action="review_hold")
    db.add(event)
    db.commit()
    # 1 小时内不重复触发
    assert monitor.should_review(db, model_a.id, "000001", -0.18) is None
    event.created_at = datetime.now() - timedelta(hours=2)
    db.commit()
    assert monitor.should_review(db, model_a.id, "000001", -0.18) == "deep_loss"


@patch("app.agents.monitor.engine.recent_reflections", return_value="(无)")
@patch("app.agents.monitor.indicators_text", side_effect=Exception("skip"))
@patch("app.agents.monitor.market.get_daily_kline", side_effect=Exception("skip"))
@patch("app.agents.monitor.llm.chat",
       return_value='推理\n{"action": "sell", "target_position_pct": 0, "confidence": 0.9, "reason": "止损"}')
def test_review_sell_executes(_chat, _k, _t, _r, db, model_a):
    broker.get_account(db, model_a.id)
    pos = make_position(db, model_a.id)
    event = monitor.review_position(db, pos, price=9.0, pct_change=-2.0,
                                    pnl_pct=-0.10, trigger="stop_loss")
    assert event.action == "review_sell"
    assert broker.get_position(db, model_a.id, "000001") is None


@patch("app.agents.monitor.engine.recent_reflections", return_value="(无)")
@patch("app.agents.monitor.indicators_text", side_effect=Exception("skip"))
@patch("app.agents.monitor.market.get_daily_kline", side_effect=Exception("skip"))
@patch("app.agents.monitor.llm.chat", return_value="没有 JSON 的回复")
def test_review_bad_json_holds(_chat, _k, _t, _r, db, model_a):
    broker.get_account(db, model_a.id)
    pos = make_position(db, model_a.id)
    event = monitor.review_position(db, pos, price=9.0, pct_change=-2.0,
                                    pnl_pct=-0.10, trigger="stop_loss")
    assert event.action == "review_hold"
    assert broker.get_position(db, model_a.id, "000001") is not None
