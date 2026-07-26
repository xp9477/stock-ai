"""引擎编排测试: mock LLM 与行情,验证多模型流水线与容错。"""
import json
from unittest.mock import patch

import pandas as pd

from app.agents import engine
from app.models import AgentOutput, Decision, Order, Reflection, Run
from app.trading import broker

DECISION_JSON = json.dumps({"action": "buy", "target_position_pct": 0.1,
                            "confidence": 0.8, "reason": "测试"})


def fake_chat(system, user, model, retries=2):
    if "交易员" in system or "风控经理" in system:
        return f"推理...\n{DECISION_JSON}"
    if "复盘教练" in system:
        return "- 教训: 不要追高"
    return "分析报告内容"


def fake_quote(code):
    return {"code": code, "name": "测试股", "price": 10.0, "pct_change": 1.0,
            "volume": 1, "turnover": 1, "pe": 20, "pb": 2, "market_cap": 1e10}


def fake_kline(code):
    return pd.DataFrame([{
        "日期": "2026-07-01", "开盘": 10, "收盘": 10, "最高": 10, "最低": 10,
        "成交量": 100, "涨跌幅": 0,
    }] * 30)


PATCHES = dict(
    chat=patch("app.agents.engine.llm.chat", side_effect=fake_chat),
    quote=patch("app.agents.engine.market.get_quote", side_effect=fake_quote),
    kline=patch("app.agents.engine.market.get_daily_kline", side_effect=fake_kline),
    info=patch("app.agents.engine.market.get_stock_info", return_value={"行业": "测试"}),
    news=patch("app.agents.engine.market.get_news", return_value=[
        {"title": "利好", "content": "内容", "time": "2026-07-25"}]),
    pquote=patch("app.trading.portfolio.market.get_quote", side_effect=fake_quote),
)


def run_with_patches(fn):
    started = [p.start() for p in PATCHES.values()]
    try:
        return fn()
    finally:
        for p in PATCHES.values():
            p.stop()
        _ = started


def test_analyze_stock_full_pipeline(db, model_a):
    def body():
        broker.get_account(db, model_a.id)
        run = Run(trigger="manual")
        db.add(run)
        db.commit()
        inputs = engine.prepare_stock_inputs("600519", "测试股")
        decision = engine.analyze_stock(db, run.id, model_a, "600519", "测试股",
                                        inputs, "大盘中性", "(无)")
        assert decision.action == "buy"
        agents = {o.agent for o in db.query(AgentOutput).all()}
        assert agents == {"technical", "fundamental", "news",
                          "bull_1", "bear_1", "bull_2", "bear_2", "trader", "risk"}
        orders = db.query(Order).filter(Order.status == "filled").all()
        assert len(orders) == 1 and orders[0].qty == 10000
        assert orders[0].model_pk == model_a.id
        # 买入理由已写入持仓,供复审
        pos = broker.get_position(db, model_a.id, "600519")
        assert pos.buy_reason == "测试"

    run_with_patches(body)


def test_analyze_stock_bad_json_falls_back_to_hold(db, model_a):
    with patch("app.agents.engine.llm.chat", return_value="没有 JSON 的回复"), \
         patch("app.agents.engine.market.get_quote", side_effect=fake_quote), \
         patch("app.trading.portfolio.market.get_quote", side_effect=fake_quote):
        broker.get_account(db, model_a.id)
        run = Run(trigger="manual")
        db.add(run)
        db.commit()
        inputs = {"tech": "t", "fund": "f", "news": "n"}
        decision = engine.analyze_stock(db, run.id, model_a, "600519", "测试股",
                                        inputs, "大盘中性", "(无)")
        assert decision.action == "hold"
        assert "解析失败" in decision.reason


def test_reflect_saves_lessons(db, model_a):
    def body():
        broker.get_account(db, model_a.id)
        run = Run(trigger="manual")
        db.add(run)
        db.commit()
        broker.buy(db, model_a.id, run.id, "600519", "测试股", price=10.0,
                   pct_change=0.0, target_amount=10000, reason="test")
        engine.reflect(db, run.id, model_a)
        rows = db.query(Reflection).filter(Reflection.model_pk == model_a.id).all()
        assert len(rows) == 1
        assert "追高" in rows[0].content

    run_with_patches(body)


def test_recent_reflections_injected(db, model_a):
    db.add(Reflection(model_pk=model_a.id, content="教训一"))
    db.commit()
    text = engine.recent_reflections(db, model_a.id)
    assert "教训一" in text
