"""资本化规则赛马退役后的 fail-closed 回归测试。"""
from datetime import date

import pytest

from app.models import Model
from app.strategies import rule_runner


def test_rule_rebalance_public_entrypoints_are_retired(db):
    rule = Model(name="历史规则", model_id="pool_equal", type="rule", enabled=True)
    db.add(rule)
    db.commit()

    with pytest.raises(RuntimeError, match="retired"):
        rule_runner.rebalance_strategy(db, "pool_equal")
    with pytest.raises(RuntimeError, match="retired"):
        rule_runner.rebalance_all_rules(db)


def test_equal_weight_helper_remains_available_for_research_evidence():
    weights = rule_runner._equal_weights(["a", "b"])
    assert abs(sum(weights.values()) - 0.6) < 1e-9


def test_legacy_calendar_helper_is_read_only():
    assert rule_runner.is_rebalance_day(date(2026, 8, 3)) is True
    assert rule_runner.is_rebalance_day(date(2026, 8, 4)) is False
