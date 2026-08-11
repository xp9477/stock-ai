"""第一性原则决策编排：冻结事实、职责隔离与候选计划。"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.agents import engine
from app.backtest import shadow
from app.models import AgentOutput, Model, Order, Position, Run, TradePlan
from app.trading import broker


def _ensemble(db, members):
    model = Model(
        name="条件计划账户",
        type="ensemble",
        members=json.dumps([member.id for member in members]),
        is_official_strategy=True,
    )
    db.add(model)
    db.commit()
    return model


def _judgment_row(model):
    return {
        "model_pk": model.id,
        "model_name": model.name,
        "model_id": model.model_id,
        "judgment": {
            "action": "buy",
            "confidence": 0.7,
            "thesis": "冻结事实支持候选买入",
            "evidence": ["factsheet.score=0.8"],
            "risks": ["盘前公告尚待复核"],
            "invalidation_conditions": ["出现重大利空公告"],
        },
        "error": "",
    }


def _trade_output(target=0.15, price=102.0):
    return json.dumps({
        "action": "buy",
        "target_position_pct": target,
        "confidence": 0.68,
        "max_buy_price": price,
        "valid_until": "2099-01-01T10:30:00+08:00",
        "thesis": "只在门禁全部通过时建立目标仓位",
        "invalidation_conditions": ["出现重大利空公告", "价格超过买入上限"],
    }, ensure_ascii=False)


def _inputs():
    now = datetime.now(timezone.utc)
    return {
        "tech": "技术事实",
        "fund": "基本面事实",
        "stock_news_items": [{"title": "公司公告摘要", "content_hash": "n1"}],
        "factsheet": {"score": 0.8},
        "factsheet_hash": "f" * 64,
        "news_fingerprint": "n" * 64,
        "reference_price": 100.0,
        "reference_price_at": (now - timedelta(minutes=5)).isoformat(),
        "data_cutoff_at": (now - timedelta(minutes=5)).isoformat(),
    }


def _attach_run_artifact(db, run, inputs, codes=("600519",)):
    cutoff = datetime.fromisoformat(inputs["data_cutoff_at"])
    quotes = {
        code: {
            "code": code,
            "name": f"stock-{code}",
            "price": 100.0,
            "quote_asof": (cutoff - timedelta(minutes=2)).isoformat(),
            "received_at": (cutoff - timedelta(minutes=1)).isoformat(),
            "source": "tencent",
            "tradable": True,
            "trade_status": "tradable",
        }
        for code in codes
    }
    return shadow.freeze_engine_run_artifact(
        db,
        run_id=run.id,
        data_cutoff_at=cutoff,
        analysis_codes=list(codes),
        eligible_quotes=quotes,
    )


def test_frozen_snapshot_is_deterministic_and_account_blind():
    inputs = _inputs() | {
        "cash": 999999,
        "position": {"avg_cost": 12.3},
        "pnl": -12345,
        "reflections": "过去亏过，所以这次梭哈",
    }
    first, first_hash = engine._frozen_snapshot("600519", "测试股", inputs, "市场中性")
    second, second_hash = engine._frozen_snapshot("600519", "测试股", inputs, "市场中性")

    assert first == second
    assert first_hash == second_hash
    assert "999999" not in first
    assert "avg_cost" not in first
    assert "-12345" not in first
    assert "过去亏过" not in first
    assert "technical_data" in first and "direct_stock_news" in first


def test_next_plan_window_fails_closed_without_proven_trade_date():
    with patch("app.agents.engine.market.is_trade_date", return_value=False), \
            pytest.raises(RuntimeError, match="无法证明"):
        engine._next_plan_window(datetime.now(timezone.utc))


def test_final_and_risk_models_create_plan_without_execution(
    db, model_a, model_b, model_c,
):
    run = Run(trigger="schedule")
    db.add(run)
    db.commit()
    ensemble = _ensemble(db, [model_a, model_b, model_c])
    account = broker.get_account(db, ensemble.id)
    account.cash = 99000.0
    position = Position(
        model_pk=ensemble.id,
        code="600519",
        name="测试股",
        total_qty=100,
        available_qty=100,
        avg_cost=10.0,
    )
    db.add(position)
    db.commit()

    inputs = _inputs()
    _attach_run_artifact(db, run, inputs)
    frozen, snapshot_hash = engine._frozen_snapshot(
        "600519", "测试股", inputs, "市场中性")
    judgments = {
        model.id: _judgment_row(model)
        for model in (model_a, model_b, model_c)
    }
    calls = []

    def fake_chat(system, user, model, retries=2):
        calls.append((system, user, model))
        return _trade_output(0.20, 103.0) if model == model_a.model_id else _trade_output()

    quote = {
        "price": 11.0,
        "pct_change": 0.0,
        "tradable": True,
        "source": "test",
    }
    with patch("app.agents.engine.llm.chat", side_effect=fake_chat), \
         patch("app.agents.engine.market.is_trade_date", return_value=True), \
         patch("app.agents.engine.market.get_quote", return_value=quote), \
         patch("app.trading.portfolio.market.get_trade_quote", return_value=quote):
        decision, plan = engine._create_ensemble_candidate(
            db,
            run_id=run.id,
            ensemble=ensemble,
            code="600519",
            name="测试股",
            inputs=inputs,
            frozen_snapshot=frozen,
            snapshot_hash=snapshot_hash,
            judgments_by_model=judgments,
        )

    assert decision.action == "buy"
    assert plan is not None and plan.status == "candidate"
    assert plan.target_position_pct == decision.target_position_pct
    assert engine._aware_datetime(plan.data_cutoff_at) == engine._aware_datetime(
        inputs["data_cutoff_at"])
    valid_from = engine._aware_datetime(plan.valid_from_at)
    assert valid_from.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%H:%M") == "09:30"
    assert db.query(TradePlan).count() == 1
    assert db.query(Order).count() == 0
    unchanged = db.query(Position).filter_by(model_pk=ensemble.id, code="600519").one()
    assert unchanged.total_qty == 100 and unchanged.avg_cost == 10.0

    assert [call[2] for call in calls] == [model_a.model_id, model_b.model_id]
    final_input, risk_input = calls[0][1], calls[1][1]
    assert "持仓成本" in final_input and "浮动盈亏" in final_input
    assert "已实现盈亏" not in final_input
    assert "已实现盈亏" in risk_input and "未实现盈亏" in risk_input
    assert "高水位" in risk_input and "剩余" in risk_input

    outputs = db.query(AgentOutput).order_by(AgentOutput.id).all()
    assert [row.agent for row in outputs] == ["final_trader", "risk_review"]
    assert all(len(row.input_summary) > 200 for row in outputs)
    assert [row.model_id_snapshot for row in outputs] == [
        model_a.model_id, model_b.model_id,
    ]
    assert all(len(row.prompt_hash) == 64 for row in outputs)
    assert all(len(row.input_hash) == 64 for row in outputs)
    assert all(len(row.output_hash) == 64 for row in outputs)
    assert all("api_key" not in row.config_snapshot_json for row in outputs)
    policy = json.loads(plan.policy_snapshot_json)
    assert policy["snapshot_hash"] == snapshot_hash
    assert policy["final_model_id"] != policy["risk_model_id"]


def test_missing_two_thirds_quorum_fails_closed_without_llm_or_plan(
    db, model_a, model_b, model_c,
):
    run = Run(trigger="schedule")
    db.add(run)
    db.commit()
    ensemble = _ensemble(db, [model_a, model_b, model_c])
    inputs = _inputs()
    _attach_run_artifact(db, run, inputs)
    frozen, snapshot_hash = engine._frozen_snapshot(
        "600519", "测试股", inputs, "市场中性")

    with patch("app.agents.engine.llm.chat") as chat:
        decision, plan = engine._create_ensemble_candidate(
            db,
            run_id=run.id,
            ensemble=ensemble,
            code="600519",
            name="测试股",
            inputs=inputs,
            frozen_snapshot=frozen,
            snapshot_hash=snapshot_hash,
            judgments_by_model={model_a.id: _judgment_row(model_a)},
        )

    assert decision.action == "hold"
    assert decision.error == "independent_judgment_quorum_failed"
    assert plan is None
    assert db.query(TradePlan).count() == 0
    assert db.query(Order).count() == 0
    chat.assert_not_called()


def test_candidate_fails_closed_without_shared_run_artifact(
    db, model_a, model_b, model_c,
):
    run = Run(trigger="schedule")
    db.add(run)
    db.commit()
    ensemble = _ensemble(db, [model_a, model_b, model_c])
    inputs = _inputs()
    frozen, snapshot_hash = engine._frozen_snapshot(
        "600519", "test", inputs, "neutral")
    judgments = {
        model.id: _judgment_row(model)
        for model in (model_a, model_b, model_c)
    }

    with patch("app.agents.engine.llm.chat") as chat:
        decision, plan = engine._create_ensemble_candidate(
            db,
            run_id=run.id,
            ensemble=ensemble,
            code="600519",
            name="test",
            inputs=inputs,
            frozen_snapshot=frozen,
            snapshot_hash=snapshot_hash,
            judgments_by_model=judgments,
        )

    assert decision.action == "hold"
    assert decision.error == "missing_run_market_artifact"
    assert plan is None
    chat.assert_not_called()


def test_candidate_cannot_fork_the_run_cutoff(
    db, model_a, model_b, model_c,
):
    run = Run(trigger="schedule")
    db.add(run)
    db.commit()
    ensemble = _ensemble(db, [model_a, model_b, model_c])
    inputs = _inputs()
    _attach_run_artifact(db, run, inputs)
    forked_inputs = dict(inputs)
    forked_inputs["data_cutoff_at"] = (
        datetime.fromisoformat(inputs["data_cutoff_at"]) + timedelta(seconds=1)
    ).isoformat()
    frozen, snapshot_hash = engine._frozen_snapshot(
        "600519", "test", forked_inputs, "neutral")
    judgments = {
        model.id: _judgment_row(model)
        for model in (model_a, model_b, model_c)
    }

    with patch("app.agents.engine.llm.chat") as chat:
        decision, plan = engine._create_ensemble_candidate(
            db,
            run_id=run.id,
            ensemble=ensemble,
            code="600519",
            name="test",
            inputs=forked_inputs,
            frozen_snapshot=frozen,
            snapshot_hash=snapshot_hash,
            judgments_by_model=judgments,
        )

    assert decision.action == "hold"
    assert decision.error == "run_market_artifact_binding_mismatch"
    assert plan is None
    chat.assert_not_called()


def test_risk_review_may_reduce_but_never_expand_a_buy():
    trader = json.loads(_trade_output(0.20, 103.0))
    reduced = json.loads(_trade_output(0.15, 102.0))
    expanded_size = json.loads(_trade_output(0.25, 102.0))
    expanded_price = json.loads(_trade_output(0.15, 104.0))

    assert engine._risk_did_not_escalate(trader, reduced)
    assert not engine._risk_did_not_escalate(trader, expanded_size)
    assert not engine._risk_did_not_escalate(trader, expanded_price)
