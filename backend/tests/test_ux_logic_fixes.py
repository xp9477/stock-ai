"""用户视角逻辑修复：账本写入、股池上限、规则删除保护、监控跳过规则臂。"""
from unittest.mock import patch

from app.agents import monitor
from app.models import Model, Position, TradeLedger, Watchlist
from app.trading import broker, portfolio


def test_execute_decision_records_ledger_open_and_close(db, model_a):
    broker.get_account(db, model_a.id)
    quote = {"price": 10.0, "pct_change": 0.0}

    with patch("app.trading.portfolio.market.get_quote", return_value=quote):
        with patch(
            "app.trading.portfolio.position_value",
            side_effect=lambda p: p.total_qty * 10.0,
        ):
            buy = portfolio.execute_decision(
                db, model_a.id, None, "600519", "贵州茅台",
                "buy", 0.10, reason="测试买入",
            )
            assert buy is not None and buy.ok
            opens = db.query(TradeLedger).filter(
                TradeLedger.side == "open", TradeLedger.model_pk == model_a.id,
            ).all()
            assert len(opens) == 1
            assert opens[0].signal_source == "ai"

            # 模拟下一交易日可卖（同日 settle 仍冻结当日买入）
            pos = broker.get_position(db, model_a.id, "600519")
            assert pos and pos.total_qty > 0
            pos.available_qty = pos.total_qty
            db.commit()

            sell = portfolio.execute_decision(
                db, model_a.id, None, "600519", "贵州茅台",
                "sell", 0.0, reason="测试卖出",
            )
            assert sell is not None and sell.ok
            closes = db.query(TradeLedger).filter(
                TradeLedger.side == "close", TradeLedger.model_pk == model_a.id,
            ).all()
            assert len(closes) == 1
            assert closes[0].signal_source == "ai"


def test_watchlist_respects_pool_max(db):
    from fastapi import HTTPException
    from app.api.routes import WatchlistAdd, add_watchlist

    for code, name in (("600000", "浦发"), ("600001", "测试")):
        db.add(Watchlist(code=code, name=name, source="manual"))
    db.commit()

    def fake_get_setting(key, *args, **kwargs):
        if key == "selector.pool_max":
            return 2
        from app.runtime_settings import get_setting as real
        return real(key, *args, **kwargs)

    with patch("app.api.routes.market.validate_code", return_value={"name": "新股", "price": 10}):
        with patch("app.runtime_settings.get_setting", side_effect=fake_get_setting):
            try:
                add_watchlist(WatchlistAdd(code="600519"), db)
                raised = False
            except HTTPException as err:
                raised = True
                assert err.status_code == 400
                assert "已满" in err.detail
            assert raised, "should reject when full"


def test_cannot_delete_rule_model(db):
    from fastapi import HTTPException
    from app.api.routes import delete_model

    rule = Model(name="S2", model_id="s2_weekly", type="rule")
    db.add(rule)
    db.commit()
    try:
        delete_model(rule.id, db)
        raised = False
    except HTTPException as err:
        raised = True
        assert err.status_code == 400
        assert "规则" in err.detail
    assert raised, "rule delete should fail"


def test_run_monitor_skips_rule_positions(db):
    """规则臂持仓即使深亏也不触发监控。"""
    rule = Model(name="规则", model_id="s2_weekly", type="rule", enabled=True)
    db.add(rule)
    db.commit()
    broker.get_account(db, rule.id)
    db.add(Position(
        model_pk=rule.id, code="000001", name="测试",
        total_qty=1000, available_qty=1000, avg_cost=10.0,
    ))
    db.commit()

    # 防止 run_monitor 关闭测试 session
    real_close = db.close
    db.close = lambda: None  # type: ignore[method-assign]

    quote = {"price": 7.0, "pct_change": -5.0}  # 浮亏 -30%
    try:
        with patch("app.agents.monitor.engine.is_running", return_value=False):
            with patch("app.database.SessionLocal", return_value=db):
                with patch("app.agents.monitor.market.get_quote", return_value=quote):
                    n = monitor.run_monitor()
        assert n == 0
        assert broker.get_position(db, rule.id, "000001") is not None
    finally:
        db.close = real_close  # type: ignore[method-assign]
