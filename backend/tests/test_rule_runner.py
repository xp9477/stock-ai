"""规则组调仓单测（mock 扶摇与因子）。"""
from unittest.mock import patch

from app.models import Model, Position, Watchlist
from app.seed import ensure_account, ensure_rule_strategies
from app.strategies import rule_runner


def _seed_pool(db):
    for code, name in [("600000", "浦发"), ("600001", "邯郸"), ("600002", "齐鲁"),
                       ("600003", "东北"), ("600004", "白云")]:
        db.add(Watchlist(code=code, name=name, source="manual"))
    db.commit()


def _seed_rules(db):
    ensure_rule_strategies(db)
    db.commit()
    for m in db.query(Model).filter(Model.type == "rule").all():
        ensure_account(db, m.id)
    db.commit()


def test_equal_weights_respects_cap():
    w = rule_runner._equal_weights(["a", "b"])
    assert abs(sum(w.values()) - 0.6) < 1e-9 or all(v <= 0.30 + 1e-9 for v in w.values())
    w10 = rule_runner._equal_weights([str(i) for i in range(10)])
    assert abs(w10["0"] - 0.1) < 1e-9


def test_pool_equal_rebalance(db):
    _seed_pool(db)
    _seed_rules(db)

    prices = {
        c: {"price": 10.0 + i, "pct_change": 0.0, "name": n}
        for i, (c, n) in enumerate([
            ("600000", "浦发"), ("600001", "邯郸"), ("600002", "齐鲁"),
            ("600003", "东北"), ("600004", "白云"),
        ])
    }

    with patch.object(rule_runner, "_prices", return_value=prices):
        result = rule_runner.rebalance_strategy(db, "pool_equal")

    assert result["ok"] is True
    assert len(result["targets"]) == 5
    model = db.query(Model).filter(Model.model_id == "pool_equal").first()
    pos_n = db.query(Position).filter(Position.model_pk == model.id).count()
    assert pos_n >= 1
    assert result["buys"]


def test_s2_weekly_uses_top_n(db):
    _seed_pool(db)
    _seed_rules(db)

    prices = {
        c: {"price": 10.0, "pct_change": 0.0, "name": c}
        for c in ["600000", "600001", "600002", "600003", "600004"]
    }

    with patch.object(rule_runner, "_prices", return_value=prices), \
            patch.object(rule_runner, "_target_codes_s2",
                         return_value=["600000", "600001", "600002"]):
        result = rule_runner.rebalance_strategy(db, "s2_weekly")

    assert result["ok"] is True
    assert set(result["targets"].keys()) == {"600000", "600001", "600002"}


def test_is_rebalance_day_monday():
    from datetime import date
    # 2026-08-03 is Monday
    assert rule_runner.is_rebalance_day(date(2026, 8, 3)) is True
    assert rule_runner.is_rebalance_day(date(2026, 8, 4)) is False
