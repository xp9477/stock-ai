"""引擎编排测试: mock LLM 与行情,验证流水线与容错。"""
import json
from unittest.mock import patch

import pandas as pd

from app.agents import engine
from app.models import AgentOutput, Decision, Run
from app.trading import broker

DECISION_JSON = json.dumps({"action": "buy", "target_position_pct": 0.1,
                            "confidence": 0.8, "reason": "测试"})


def fake_chat(system, user, retries=2):
    if "交易员" in system or "风控经理" in system:
        return f"推理...\n{DECISION_JSON}"
    return "分析报告内容"


def fake_quote(code):
    return {"code": code, "name": "测试股", "price": 10.0, "pct_change": 1.0,
            "volume": 1, "turnover": 1, "pe": 20, "pb": 2, "market_cap": 1e10}


def fake_kline(code):
    return pd.DataFrame([{
        "日期": "2026-07-01", "开盘": 10, "收盘": 10, "最高": 10, "最低": 10,
        "成交量": 100, "涨跌幅": 0,
    }] * 30)


@patch("app.agents.engine.llm.chat", side_effect=fake_chat)
@patch("app.agents.engine.market.get_quote", side_effect=fake_quote)
@patch("app.agents.engine.market.get_daily_kline", side_effect=fake_kline)
@patch("app.agents.engine.market.get_stock_info", return_value={"行业": "测试"})
@patch("app.agents.engine.market.get_news", return_value=[
    {"title": "利好", "content": "内容", "time": "2026-07-25"}])
@patch("app.trading.portfolio.market.get_quote", side_effect=fake_quote)
def test_analyze_stock_full_pipeline(_pq, _news, _info, _kline, _quote, _chat, db):
    broker.get_account(db)
    run = Run(trigger="manual")
    db.add(run)
    db.commit()

    decision = engine.analyze_stock(db, run.id, "600519", "测试股")

    assert decision.action == "buy"
    agents = {out.agent for out in db.query(AgentOutput).all()}
    assert agents == {"technical", "fundamental", "news",
                      "bull_1", "bear_1", "bull_2", "bear_2", "trader", "risk"}
    # 决策已执行: 10% * 100万 = 10万 -> 10000 股 @10 元
    from app.models import Order
    orders = db.query(Order).filter(Order.status == "filled").all()
    assert len(orders) == 1
    assert orders[0].qty == 10000


@patch("app.agents.engine.llm.chat", return_value="没有任何 JSON 的回复")
@patch("app.agents.engine.market.get_quote", side_effect=fake_quote)
@patch("app.agents.engine.market.get_daily_kline", side_effect=fake_kline)
@patch("app.agents.engine.market.get_stock_info", return_value={})
@patch("app.agents.engine.market.get_news", return_value=[])
@patch("app.trading.portfolio.market.get_quote", side_effect=fake_quote)
def test_analyze_stock_bad_json_falls_back_to_hold(_pq, _news, _info, _kline, _quote, _chat, db):
    broker.get_account(db)
    run = Run(trigger="manual")
    db.add(run)
    db.commit()

    decision = engine.analyze_stock(db, run.id, "600519", "测试股")
    assert decision.action == "hold"
    assert "解析失败" in decision.reason
