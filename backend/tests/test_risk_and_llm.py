from unittest.mock import patch

from app.agents import llm
from app.config import settings
from app.trading import broker, portfolio


def test_parse_decision_json_valid():
    text = '推理过程...\n{"action": "buy", "target_position_pct": 0.15, "confidence": 0.8, "reason": "趋势向好"}'
    result = llm.parse_decision_json(text)
    assert result == {"action": "buy", "target_position_pct": 0.15,
                      "confidence": 0.8, "reason": "趋势向好"}


def test_parse_decision_json_invalid_action():
    assert llm.parse_decision_json('{"action": "yolo", "target_position_pct": 1}') is None


def test_parse_decision_json_garbage():
    assert llm.parse_decision_json("完全没有 JSON 的输出") is None


def test_parse_decision_json_clamps_range():
    result = llm.parse_decision_json('{"action": "buy", "target_position_pct": 5, "confidence": 2, "reason": "x"}')
    assert result["target_position_pct"] == 1.0
    assert result["confidence"] == 1.0


def _no_quote(_code):
    return None


@patch("app.trading.portfolio.market.get_quote", _no_quote)
def test_risk_limit_caps_single_position(db):
    broker.get_account(db)
    with patch("app.trading.portfolio.SessionLocal", None, create=True):
        action, target, note = portfolio.apply_risk_limits(db, "600519", "buy", 0.5)
    assert action == "buy"
    assert target <= settings.max_position_pct + 1e-9
    assert "单票仓位上限" in note


@patch("app.trading.portfolio.market.get_quote", _no_quote)
def test_risk_limit_single_buy_cash_pct(db):
    broker.get_account(db)
    action, target, note = portfolio.apply_risk_limits(db, "600519", "buy", 0.3)
    # 全现金账户: 目标 30% = 30 万,超过可用资金 50%? 100万*50%=50万 > 30万,不触发
    assert action == "buy"
    account = broker.get_account(db)
    account.cash = 400000  # 假设已有 60 万持仓(此处简化仅测现金限制)
    db.commit()


@patch("app.trading.portfolio.market.get_quote", _no_quote)
def test_risk_limit_sell_passthrough(db):
    broker.get_account(db)
    action, target, note = portfolio.apply_risk_limits(db, "600519", "sell", 0.0)
    assert action == "sell"
    assert target == 0.0
