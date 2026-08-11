"""用户视角逻辑修复：账本写入、股池上限、规则删除保护、监控跳过规则臂。"""
from unittest.mock import patch

from app.agents import monitor
from app.ledger import closed_trade_count
from app.models import Account, AgentOutput, Model, Order, Position, Run, TradeLedger, Watchlist
from app.trading import broker, portfolio


def test_execute_decision_records_ledger_open_and_close(db, model_a):
    broker.get_account(db, model_a.id)
    quote = {"price": 10.0, "pct_change": 0.0}

    with patch("app.trading.portfolio.market.get_trade_quote", return_value=quote):
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
            assert closes[0].is_closed is True
            assert opens[0].is_closed is True
            assert closed_trade_count(db, model_pk=model_a.id) == 1


def test_execute_decision_rejects_unsafe_quote_without_writes(db, model_a):
    broker.get_account(db, model_a.id)
    with patch("app.trading.portfolio.market.get_trade_quote", return_value=None):
        result = portfolio.execute_decision(
            db, model_a.id, None, "600519", "贵州茅台", "buy", 0.1)
    assert result is not None and result.ok is False
    assert db.query(Order).count() == 0
    assert db.query(TradeLedger).count() == 0


def test_partial_sell_does_not_count_as_closed_trade(db, model_a):
    broker.get_account(db, model_a.id)
    quote = {"price": 10.0, "pct_change": 0.0}
    with patch("app.trading.portfolio.market.get_trade_quote", return_value=quote):
        portfolio.execute_decision(
            db, model_a.id, None, "600519", "贵州茅台", "buy", 0.2)
    pos = broker.get_position(db, model_a.id, "600519")
    pos.available_qty = pos.total_qty
    db.commit()
    with patch(
        "app.trading.portfolio.market.get_trade_quote",
        return_value={"price": 11.0, "pct_change": 0.0},
    ):
        result = portfolio.execute_decision(
            db, model_a.id, None, "600519", "贵州茅台", "sell", 0.1)
    assert result is not None and result.ok
    assert broker.get_position(db, model_a.id, "600519") is not None
    assert closed_trade_count(db, model_pk=model_a.id) == 0

    first_close = db.query(TradeLedger).filter(
        TradeLedger.side == "close", TradeLedger.model_pk == model_a.id,
    ).one()
    with patch(
        "app.trading.portfolio.market.get_trade_quote",
        return_value={"price": 12.0, "pct_change": 0.0},
    ):
        final_result = portfolio.execute_decision(
            db, model_a.id, None, "600519", "贵州茅台", "sell", 0.0)
    assert final_result is not None and final_result.ok
    final_close = db.query(TradeLedger).filter(
        TradeLedger.side == "close", TradeLedger.is_closed.is_(True),
        TradeLedger.model_pk == model_a.id,
    ).one()
    final_leg_only = (12.0 - 10.0) * final_result.order.qty
    assert final_close.pnl > final_leg_only
    assert final_close.pnl > first_close.pnl
    assert closed_trade_count(db, model_pk=model_a.id) == 1


def test_trade_and_ledger_rollback_together(db, model_a):
    broker.get_account(db, model_a.id)
    quote = {"price": 10.0, "pct_change": 0.0}
    with patch("app.trading.portfolio.market.get_trade_quote", return_value=quote), \
            patch("app.trading.portfolio.record_open", side_effect=RuntimeError("ledger failed")):
        try:
            portfolio.execute_decision(
                db, model_a.id, None, "600519", "贵州茅台", "buy", 0.1)
            assert False, "ledger failure must propagate"
        except RuntimeError:
            pass
    assert broker.get_position(db, model_a.id, "600519") is None
    assert db.query(Order).count() == 0
    assert db.query(TradeLedger).count() == 0


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
        assert err.status_code == 410
        assert err.detail["code"] == "capitalized_rule_racing_retired"
    assert raised, "rule delete should fail"


def test_llm_advisor_listing_does_not_create_a_fake_account(db, model_a):
    from app.api.routes import list_models

    rows = list_models(db)
    row = next(item for item in rows if item["id"] == model_a.id)
    assert row["role"] == "advisor"
    assert row["total_equity"] is None and row["pnl_pct"] is None
    assert db.query(Account).filter(Account.model_pk == model_a.id).count() == 0


def test_delete_with_forward_evidence_archives_instead_of_erasing(db, model_a):
    from app.api.routes import delete_model

    run = Run(trigger="manual", status="done")
    db.add(run)
    db.flush()
    output = AgentOutput(
        run_id=run.id,
        model_pk=model_a.id,
        code="600000",
        agent="independent_judgment",
        input_summary="完整冻结事实",
        output='{"action":"hold"}',
    )
    db.add(output)
    db.commit()

    result = delete_model(model_a.id, db)

    assert result["archived"] is True
    assert db.get(Model, model_a.id) is not None
    assert db.get(Model, model_a.id).enabled is False
    assert db.get(AgentOutput, output.id) is not None


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
