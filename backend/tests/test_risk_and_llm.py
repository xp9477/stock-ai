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
def test_risk_limit_caps_single_position(db, model_a):
    broker.get_account(db, model_a.id)
    action, target, note = portfolio.apply_risk_limits(db, model_a.id, "600519", "buy", 0.5)
    assert action == "buy"
    assert target <= settings.max_position_pct + 1e-9
    assert "单票仓位上限" in note


@patch("app.trading.portfolio.market.get_quote", _no_quote)
def test_risk_limit_sell_passthrough(db, model_a):
    broker.get_account(db, model_a.id)
    action, target, note = portfolio.apply_risk_limits(db, model_a.id, "600519", "sell", 0.0)
    assert action == "sell"
    assert target == 0.0


def test_parse_decision_json_picks_last_valid_object():
    text = ('表格数据 {"col": 1} 中间还有 {"foo": "bar"}\n'
            '最终 {"action": "buy", "target_position_pct": 0.2, "confidence": 0.6, "reason": "ok"}')
    result = llm.parse_decision_json(text)
    assert result["action"] == "buy"
    assert result["target_position_pct"] == 0.2


def test_decide_with_fallback_uses_extractor():
    with patch("app.agents.llm.chat",
               return_value='{"action": "hold", "target_position_pct": 0, "confidence": 0.5, "reason": "观望"}'):
        result = llm.decide_with_fallback("长篇分析,没有 JSON 结论", "test-model")
    assert result["action"] == "hold"


def test_decide_with_fallback_gives_up_gracefully():
    with patch("app.agents.llm.chat", side_effect=RuntimeError("LLM down")):
        assert llm.decide_with_fallback("没有 JSON", "test-model") is None
