"""合议合成规则测试(零 LLM)。"""
import json
from unittest.mock import patch

from app.agents import engine
from app.models import Decision, Model, Run
from app.trading import broker


def fake_quote(code):
    return {"code": code, "name": "测试股", "price": 10.0, "pct_change": 1.0,
            "volume": 1, "turnover": 1, "pe": 20, "pb": 2, "market_cap": 1e10}


def make_ensemble(db, members):
    ens = Model(name="组合", type="ensemble",
                members=json.dumps([m.id for m in members]))
    db.add(ens)
    db.commit()
    return ens


def add_decision(db, run_id, model_pk, action, pct, conf):
    db.add(Decision(run_id=run_id, model_pk=model_pk, code="000001",
                    name="测试股", action=action, target_position_pct=pct,
                    confidence=conf, reason="test"))
    db.commit()


@patch("app.trading.portfolio.market.get_quote", side_effect=fake_quote)
def test_majority_vote_two_to_one(_quote, db, model_a, model_b, model_c):
    run = Run(trigger="manual")
    db.add(run)
    db.commit()
    ens = make_ensemble(db, [model_a, model_b, model_c])
    broker.get_account(db, ens.id)
    add_decision(db, run.id, model_a.id, "buy", 0.2, 0.8)
    add_decision(db, run.id, model_b.id, "buy", 0.1, 0.6)
    add_decision(db, run.id, model_c.id, "hold", 0.0, 0.5)

    engine.synthesize_ensemble(db, run.id, ens, {"000001": "测试股"})

    dec = (db.query(Decision).filter(Decision.model_pk == ens.id).first())
    assert dec.action == "buy"
    # 仓位取获胜方均值 (0.2+0.1)/2=0.15
    assert abs(dec.target_position_pct - 0.15) < 1e-9
    assert abs(dec.confidence - 0.7) < 1e-9
    # 已执行: 15% * 100万 = 15万 -> 15000 股 @10
    from app.models import Order
    orders = db.query(Order).filter(Order.model_pk == ens.id,
                                    Order.status == "filled").all()
    assert len(orders) == 1 and orders[0].qty == 15000


@patch("app.trading.portfolio.market.get_quote", side_effect=fake_quote)
def test_two_member_split_holds(_quote, db, model_a, model_b):
    run = Run(trigger="manual")
    db.add(run)
    db.commit()
    ens = make_ensemble(db, [model_a, model_b])
    broker.get_account(db, ens.id)
    add_decision(db, run.id, model_a.id, "buy", 0.2, 0.8)
    add_decision(db, run.id, model_b.id, "sell", 0.0, 0.7)

    engine.synthesize_ensemble(db, run.id, ens, {"000001": "测试股"})

    dec = db.query(Decision).filter(Decision.model_pk == ens.id).first()
    assert dec.action == "hold"
    assert "无多数共识" in dec.reason


@patch("app.trading.portfolio.market.get_quote", side_effect=fake_quote)
def test_three_way_tie_holds(_quote, db, model_a, model_b, model_c):
    run = Run(trigger="manual")
    db.add(run)
    db.commit()
    ens = make_ensemble(db, [model_a, model_b, model_c])
    broker.get_account(db, ens.id)
    add_decision(db, run.id, model_a.id, "buy", 0.2, 0.8)
    add_decision(db, run.id, model_b.id, "sell", 0.0, 0.7)
    add_decision(db, run.id, model_c.id, "hold", 0.0, 0.5)

    engine.synthesize_ensemble(db, run.id, ens, {"000001": "测试股"})

    dec = db.query(Decision).filter(Decision.model_pk == ens.id).first()
    assert dec.action == "hold"


@patch("app.trading.portfolio.market.get_quote", side_effect=fake_quote)
def test_error_decisions_excluded(_quote, db, model_a, model_b):
    run = Run(trigger="manual")
    db.add(run)
    db.commit()
    ens = make_ensemble(db, [model_a, model_b])
    broker.get_account(db, ens.id)
    add_decision(db, run.id, model_a.id, "buy", 0.2, 0.8)
    db.add(Decision(run_id=run.id, model_pk=model_b.id, code="000001",
                    name="测试股", action="hold", error="LLM 失败"))
    db.commit()

    engine.synthesize_ensemble(db, run.id, ens, {"000001": "测试股"})

    dec = db.query(Decision).filter(Decision.model_pk == ens.id).first()
    # 只剩 1 票有效,1 票过半成立 -> buy
    assert dec.action == "buy"
