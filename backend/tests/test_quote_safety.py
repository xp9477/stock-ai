"""脏报价防护 + 非交易时段监控。"""
from datetime import datetime, time, timezone
from unittest.mock import patch

from app.agents import monitor
from app.data import market
from app.models import Position
from app.trading import broker


def test_sanitize_quote_rejects_fake_price_vs_cost():
    bad = {"price": 7.0, "pct_change": 0, "code": "002475"}
    with patch("app.data.market.last_close_price", return_value=55.0):
        assert market.sanitize_quote(bad, code="002475", avg_cost=55.9) is None


def test_sanitize_quote_accepts_normal_price():
    q = {"price": 56.0, "pct_change": 1.0, "code": "002475"}
    with patch("app.data.market.last_close_price", return_value=55.0):
        out = market.sanitize_quote(q, code="002475", avg_cost=55.9)
        assert out is not None
        assert out["price"] == 56.0


def test_trade_calendar_failure_is_fail_closed():
    with patch("app.data.market._trade_dates", side_effect=RuntimeError("offline")):
        assert market.is_trade_date() is False


def test_continuous_and_closing_session_boundaries():
    with patch("app.data.market.is_trade_date", return_value=True):
        assert market.is_trading_session(datetime(2026, 8, 10, 9, 29)) is False
        assert market.is_trading_session(datetime(2026, 8, 10, 9, 30)) is True
        assert market.is_trading_session(datetime(2026, 8, 10, 12, 0)) is False
        assert market.is_trading_session(datetime(2026, 8, 10, 14, 59)) is True
        assert market.is_trading_session(datetime(2026, 8, 10, 15, 1)) is False


def test_aware_utc_session_time_is_converted_to_shanghai():
    with patch("app.data.market.is_trade_date", return_value=True):
        assert market.is_trading_session(
            datetime(2026, 8, 10, 1, 35, tzinfo=timezone.utc)) is True
        assert market.is_trading_session(
            datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)) is False


def test_run_monitor_skips_outside_session(db, model_a):
    broker.get_account(db, model_a.id)
    db.add(Position(
        model_pk=model_a.id, code="002475", name="立讯",
        total_qty=1000, available_qty=1000, avg_cost=55.0,
    ))
    db.commit()

    with patch("app.agents.monitor.engine.is_running", return_value=False):
        with patch("app.agents.monitor.market.is_trading_session", return_value=False):
            n = monitor.run_monitor()
    assert n == 0
    assert broker.get_position(db, model_a.id, "002475") is not None


def test_run_monitor_skips_bad_quote_no_force_sell(db, model_a):
    broker.get_account(db, model_a.id)
    db.add(Position(
        model_pk=model_a.id, code="002475", name="立讯",
        total_qty=1000, available_qty=1000, avg_cost=55.0,
    ))
    db.commit()

    fake = {"price": 7.0, "pct_change": -80.0, "code": "002475"}
    with patch("app.agents.monitor.engine.is_running", return_value=False):
        with patch("app.agents.monitor.market.is_trading_session", return_value=True):
            with patch("app.agents.monitor.market.get_quote", return_value=fake):
                with patch("app.agents.monitor.market.sanitize_quote", return_value=None):
                    with patch("app.database.SessionLocal", return_value=db):
                        db.close = lambda: None
                        n = monitor.run_monitor()
    assert n == 0
    assert broker.get_position(db, model_a.id, "002475") is not None
