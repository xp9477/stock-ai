"""P3 研究闭环：规格 / 启发式翻译 / 回测引擎 / 晋升名额。"""
import json

import numpy as np
import pandas as pd

from app.models import Account, Model, ResearchHypothesis, Watchlist
from app.research import service as research
from app.research.spec import heuristic_from_theory, validate_spec
from app.backtest.spec_runner import run_spec_backtest
from app.backtest.validation import split_development_holdout


def test_heuristic_parses_theory():
    spec = heuristic_from_theory("短中期动量加 ROE，每周前 8 只，止损 8%")
    assert spec["mode"] == "factor_cross_section"
    assert "mom_short" in spec["factors"] or "mom_mid" in spec["factors"]
    assert "quality_roe" in spec["factors"]
    assert spec["top_n"] == 8
    assert any(e["type"] == "stop_loss_pct" for e in spec["events"])


def test_validate_rejects_bad_mode_softly():
    spec, errs = validate_spec({"mode": "grid_intraday", "factors": ["mom_short"], "top_n": 5})
    # mode 非法会记录错误并回退
    assert spec["mode"] in ("factor_cross_section", "equal_weight")


def _synthetic_panel(n_days=40, codes=("000001", "000002", "600000", "600519")):
    rows = []
    rng = np.random.default_rng(42)
    for i in range(n_days):
        dt = pd.Timestamp("2024-01-02") + pd.Timedelta(days=i)
        if dt.weekday() >= 5:
            continue
        for j, c in enumerate(codes):
            close = 10 + j + i * 0.02 + float(rng.normal(0, 0.1))
            rows.append({
                "date": dt,
                "code": c,
                "close": close,
                "mom_short": float(rng.normal(j * 0.1, 0.05)),
                "mom_mid": float(rng.normal(0, 0.05)),
                "low_vol": float(rng.uniform(0.5, 1.5)),
                "ep": float(rng.uniform(0.02, 0.08)),
                "bp": float(rng.uniform(0.2, 1.0)),
                "quality_roe": float(rng.uniform(0.05, 0.2)),
            })
    return pd.DataFrame(rows)


def test_spec_runner_produces_equity():
    panel = _synthetic_panel()
    spec = {
        "name": "t",
        "mode": "factor_cross_section",
        "factors": ["mom_short", "quality_roe"],
        "top_n": 2,
        "rebalance": "W-MON",
        "events": [{"type": "stop_loss_pct", "value": -0.2}],
    }
    res = run_spec_backtest(panel, spec, initial_cash=1_000_000)
    assert res.equity is not None and len(res.equity) > 5
    assert "sharpe" in res.metrics


def test_holdout_split_is_strictly_later():
    development, holdout = split_development_holdout(_synthetic_panel(n_days=80))
    assert not development.empty and not holdout.empty
    assert pd.to_datetime(development["date"]).max() < pd.to_datetime(holdout["date"]).min()


def test_legacy_mutable_projection_cannot_promote_or_create_capital(db):
    db.add(Watchlist(code="600519", name="茅台", source="manual"))
    db.add(Watchlist(code="000001", name="平安", source="manual"))
    db.commit()

    h = research.create_hypothesis(db, "动量前 5 只周频", title="动量5")
    hid = h["id"]
    # 跳过真实回测：写入假 backtest
    row = db.get(ResearchHypothesis, hid)
    row.spec_json = json.dumps({
        "name": "动量5", "mode": "factor_cross_section",
        "factors": ["mom_short"], "top_n": 5, "rebalance": "W-MON", "events": [],
    })
    row.backtest_json = json.dumps({"result": {"metrics": {"sample_ok": True, "sharpe": 1.0}}})
    row.status = "suggested"
    row.suggestion = "promote"
    db.commit()

    before = (
        db.query(Model).count(),
        db.query(Account).count(),
    )
    try:
        research.promote(db, hid)
        assert False, "legacy mutable projection must not promote"
    except ValueError as err:
        assert "不可变 experiment" in str(err)
    assert (db.query(Model).count(), db.query(Account).count()) == before


def test_empty_projection_cannot_promote(db):
    h = research.create_hypothesis(db, "x", title="无证据")
    row = db.get(ResearchHypothesis, h["id"])
    row.backtest_json = "{}"
    row.status = "backtested"
    db.commit()
    try:
        research.promote(db, h["id"])
        assert False, "should fail"
    except ValueError as err:
        assert "不可变 experiment" in str(err)
