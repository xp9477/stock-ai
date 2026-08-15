from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents import llm
from app.models import Position
from app.runtime_settings import get_setting
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
    assert target <= float(get_setting("risk.max_position_pct")) + 1e-9
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


def test_retired_grok_45_is_rewritten_to_46():
    assert llm.resolve_model_id("grok-4.5") == "grok-4.6"
    assert llm.resolve_model_id("Grok-4-5") == "grok-4.6"
    assert llm.resolve_model_id("grok-4.6") == "grok-4.6"
    assert llm.resolve_model_id("gpt-5.6-sol-high") == "gpt-5.6-sol"
    assert llm.resolve_model_id("gpt-5.6-sol") == "gpt-5.6-sol"


def test_reasoning_effort_is_high_only_for_grok():
    assert llm.reasoning_effort_for("grok-4.6") == "high"
    assert llm.reasoning_effort_for("Grok-4.6") == "high"
    assert llm.reasoning_effort_for("grok-4.5") == "high"
    assert llm.reasoning_effort_for("gpt-5.6-sol-high") is None
    assert llm.resolve_model_id("gemini-3.6-flash-high") == "gemini-3.7-flash-high"
    assert llm.reasoning_effort_for("gemini-3.7-flash-high") is None


def test_chat_sends_reasoning_effort_for_grok_only():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    with patch("app.agents.llm.get_client", return_value=client):
        assert llm.chat("system", "user", "grok-4.5", retries=0) == "ok"
    assert captured["model"] == "grok-4.6"
    assert captured["extra_body"] == {"reasoning_effort": "high"}

    captured.clear()
    with patch("app.agents.llm.get_client", return_value=client):
        assert llm.chat("system", "user", "gpt-5.6-sol", retries=0) == "ok"
    assert captured["model"] == "gpt-5.6-sol"
    assert "extra_body" not in captured


def test_chat_rejects_empty_model_response():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=""))]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: response)
        )
    )
    with patch("app.agents.llm.get_client", return_value=client):
        with pytest.raises(RuntimeError, match="空内容"):
            llm.chat("system", "user", "model", retries=0)


def test_total_equity_ignores_zero_quantity_placeholders(db, model_a):
    account = broker.get_account(db, model_a.id)
    db.add(Position(
        model_pk=model_a.id, code="018003", name="not_ready",
        total_qty=0, available_qty=0, avg_cost=0.0,
    ))
    db.commit()

    with patch("app.trading.portfolio.market.get_trade_quote") as quote:
        equity = portfolio.total_equity(db, model_a.id)

    quote.assert_not_called()
    assert equity["total_equity"] == account.cash
