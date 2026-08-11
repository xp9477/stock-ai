import json

import pandas as pd
import pytest

from app.agents import engine as decision_engine
from app.models import (
    Account,
    BacktestExperiment,
    BacktestExperimentResult,
    HoldoutAccessEvent,
    Model,
    ResearchHypothesis,
    Watchlist,
)
from app.api.analytics_routes import BacktestBody, run_backtest as run_analytics_backtest
from app.research import service as research
from app.research.grid import run_grid


SPEC = {
    "name": "sealed-spec",
    "mode": "factor_cross_section",
    "factors": ["mom_short", "quality_roe"],
    "top_n": 1,
    "rebalance": "W-MON",
    "events": [],
}


def _panel() -> pd.DataFrame:
    rows = []
    for i, day in enumerate(pd.bdate_range("2024-01-02", periods=35)):
        for j, code in enumerate(("000001", "600000")):
            rows.append({
                "date": day,
                "code": code,
                "close": 10.0 + j + i * 0.02,
                "mom_short": 0.01 * (i + j),
                "mom_mid": 0.02 * (i + j),
                "low_vol": -0.01 - 0.001 * j,
                "ep": 0.05 + 0.01 * j,
                "bp": 0.4 + 0.1 * j,
                "quality_roe": 0.1 + 0.01 * j,
            })
    return pd.DataFrame(rows)


def _hypothesis(db):
    db.add_all([
        Watchlist(code="000001", name="平安", source="manual"),
        Watchlist(code="600000", name="浦发", source="manual"),
    ])
    db.commit()
    created = research.create_hypothesis(db, "可复现实验", title="封存实验")
    return research.update_spec(db, created["id"], SPEC, confirm=True)


def test_research_run_seals_then_opens_same_holdout_once(monkeypatch, db):
    hypothesis = _hypothesis(db)
    monkeypatch.setattr("app.factors.panel.build_factor_panel", lambda *a, **k: _panel())

    sealed = research.run_backtest(
        db, hypothesis["id"], reveal_holdout=False, actor="tester")
    experiment_id = sealed["backtest"]["experiment_id"]
    assert sealed["backtest"]["holdout"] is None
    assert sealed["backtest"]["holdout_sealed"] is True
    assert db.query(BacktestExperiment).count() == 1
    assert db.query(BacktestExperimentResult).count() == 1
    assert db.query(HoldoutAccessEvent).count() == 0

    opened = research.reveal_holdout(db, hypothesis["id"], experiment_id, actor="tester")
    assert opened["backtest"]["holdout"] is not None
    assert opened["backtest"]["holdout_sealed"] is False
    assert db.query(HoldoutAccessEvent).count() == 1
    assert db.query(BacktestExperimentResult).count() == 2

    repeated = research.run_backtest(
        db, hypothesis["id"], reveal_holdout=True, actor="tester")
    assert repeated["backtest"]["experiment_id"] == experiment_id
    assert db.query(BacktestExperiment).count() == 1
    assert db.query(BacktestExperimentResult).count() == 2
    assert db.query(HoldoutAccessEvent).count() == 2

    changed = dict(SPEC, name="post-holdout-change", top_n=2)
    with pytest.raises(ValueError, match="已经查看"):
        research.update_spec(db, hypothesis["id"], changed, confirm=True)
    with pytest.raises(ValueError, match="已经查看"):
        research.translate(db, hypothesis["id"])


def test_translate_clears_old_result_and_promotion_recommendation(monkeypatch, db):
    created = research.create_hypothesis(db, "旧理论", title="旧规格")
    row = db.get(ResearchHypothesis, created["id"])
    row.spec_json = json.dumps(SPEC)
    row.backtest_json = json.dumps({"result": {"metrics": {"sample_ok": True}}})
    row.suggestion = "promote"
    row.status = "suggested"
    db.commit()

    translated_spec = dict(SPEC, name="NEW_UNTESTED", top_n=2)
    monkeypatch.setattr(
        research,
        "translate_theory",
        lambda *a, **k: {"spec": translated_spec, "source": "test", "errors": []},
    )
    translated = research.translate(db, row.id)

    assert translated["spec"]["name"] == "NEW_UNTESTED"
    assert translated["backtest"] is None
    assert translated["suggestion"] == ""
    with pytest.raises(ValueError, match="先完成回测"):
        research.promote(db, row.id)


def test_result_spec_fingerprint_mismatch_blocks_promotion(monkeypatch, db):
    hypothesis = _hypothesis(db)
    monkeypatch.setattr("app.factors.panel.build_factor_panel", lambda *a, **k: _panel())
    completed = research.run_backtest(
        db, hypothesis["id"], reveal_holdout=True, actor="tester")
    row = db.get(ResearchHypothesis, hypothesis["id"])
    row.spec_json = json.dumps(dict(SPEC, name="tampered-after-result", top_n=2))
    row.suggestion = "promote"
    row.status = "suggested"
    db.commit()

    with pytest.raises(ValueError, match="指纹不匹配"):
        research.promote(db, row.id)
    assert completed["backtest"]["experiment_id"] is not None


def test_mutable_suggestion_cannot_override_immutable_results(monkeypatch, db):
    hypothesis = _hypothesis(db)
    monkeypatch.setattr("app.factors.panel.build_factor_panel", lambda *a, **k: _panel())
    research.run_backtest(db, hypothesis["id"], reveal_holdout=True, actor="tester")
    row = db.get(ResearchHypothesis, hypothesis["id"])
    row.suggestion = "promote"
    row.status = "suggested"
    db.commit()

    with pytest.raises(ValueError, match="不可变.*不支持晋升"):
        research.promote(db, row.id)


def test_evidence_promotion_never_creates_capital_account(monkeypatch, db):
    hypothesis = _hypothesis(db)
    monkeypatch.setattr("app.factors.panel.build_factor_panel", lambda *a, **k: _panel())
    completed = research.run_backtest(
        db, hypothesis["id"], reveal_holdout=True, actor="tester")
    experiment_id = completed["backtest"]["experiment_id"]
    captured_thresholds = {}

    def promote_with_frozen_thresholds(*_args, **kwargs):
        captured_thresholds.update(kwargs)
        return "promote"

    monkeypatch.setattr(research, "_suggest", promote_with_frozen_thresholds)
    before = {
        "models": db.query(Model).count(),
        "accounts": db.query(Account).count(),
    }

    promoted = research.promote(db, hypothesis["id"])

    assert promoted["status"] == "promoted"
    assert promoted["promoted_experiment_id"] == experiment_id
    assert promoted["promoted_model_id"] == ""
    assert promoted["capital_account_created"] is False
    assert db.query(Model).count() == before["models"]
    assert db.query(Account).count() == before["accounts"]
    frozen_config = json.loads(db.get(
        BacktestExperiment, experiment_id).config_json)["settings"]
    assert captured_thresholds == {
        "min_trade_days": int(frozen_config["race.min_trade_days"]),
        "min_closed_trades": int(frozen_config["race.min_closed_trades"]),
    }
    context = decision_engine._promoted_research_context(db)
    assert f"experiment={experiment_id}" in context
    assert "不可变回测证据" in context


def test_analytics_backtest_records_development_without_consuming_holdout(
    monkeypatch, db,
):
    db.add_all([
        Watchlist(code="000001", name="平安", source="manual"),
        Watchlist(code="600000", name="浦发", source="manual"),
    ])
    db.commit()
    monkeypatch.setattr("app.factors.panel.build_factor_panel", lambda *a, **k: _panel())

    first = run_analytics_backtest(BacktestBody(years=1, top_n=1), db)
    repeated = run_analytics_backtest(BacktestBody(years=1, top_n=1), db)

    assert first["experiment_id"] == repeated["experiment_id"]
    assert first["holdout_reserved"] is True
    experiment = db.get(BacktestExperiment, first["experiment_id"])
    assert experiment.validation_mode == "development_only"
    results = db.query(BacktestExperimentResult).filter_by(
        experiment_id=experiment.id).all()
    assert [row.phase for row in results] == ["development"]
    assert db.query(HoldoutAccessEvent).count() == 0


def test_grid_rows_reference_permanent_development_results(monkeypatch, db):
    db.add_all([
        Watchlist(code="000001", name="平安", source="manual"),
        Watchlist(code="600000", name="浦发", source="manual"),
    ])
    db.commit()
    monkeypatch.setattr("app.research.grid.build_factor_panel", lambda *a, **k: _panel())

    result = run_grid(db, years=1, include_equal_weight=True, max_combos=1)

    assert result["holdout_reserved"] is True
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    experiment = db.get(BacktestExperiment, row["experiment_id"])
    persisted = db.query(BacktestExperimentResult).filter_by(
        experiment_id=experiment.id).one()
    assert experiment.validation_mode == "development_only"
    assert persisted.phase == "development"
    assert persisted.result_fingerprint == row["result_fingerprint"]
