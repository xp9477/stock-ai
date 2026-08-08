"""P4：规则库网格、提议、hold_max_days。"""
from app.research.grid import expand_grid, import_specs_as_hypotheses
from app.research.library import get_library
from app.research.propose import propose_candidates
from app.research.spec import validate_spec
from app.backtest.spec_runner import run_spec_backtest
from app.models import ResearchHypothesis
import numpy as np
import pandas as pd


def test_library_shape():
    lib = get_library()
    assert lib["factors"] and lib["presets"] and lib["events"]
    assert any(e["type"] == "hold_max_days" for e in lib["events"])


def test_expand_grid_respects_max():
    specs = expand_grid(max_combos=6, include_equal_weight=True)
    assert 1 <= len(specs) <= 6
    assert all("name" in s for s in specs)


def test_hold_max_days_in_validate():
    spec, err = validate_spec({
        "name": "h",
        "mode": "equal_weight",
        "events": [{"type": "hold_max_days", "days": 10}],
    })
    assert not err
    assert spec["events"][0]["type"] == "hold_max_days"
    assert spec["events"][0]["days"] == 10


def test_import_specs(db):
    specs = expand_grid(max_combos=2, include_equal_weight=False)
    items = import_specs_as_hypotheses(db, specs)
    assert len(items) >= 1
    assert db.query(ResearchHypothesis).count() >= 1
    assert items[0]["status"] == "confirmed"


def test_propose_library(db):
    items = propose_candidates(db, count=3, mode="library")
    assert len(items) == 3
    assert all(i["status"] == "draft" for i in items)


def test_spec_runner_hold_max():
    rows = []
    for i in range(30):
        dt = pd.Timestamp("2024-01-02") + pd.Timedelta(days=i)
        if dt.weekday() >= 5:
            continue
        for j, c in enumerate(["000001", "000002", "600000"]):
            rows.append({
                "date": dt, "code": c,
                "close": 10 + j + i * 0.01,
                "mom_short": 0.1 * j,
                "quality_roe": 0.1,
            })
    panel = pd.DataFrame(rows)
    spec = {
        "name": "t",
        "mode": "factor_cross_section",
        "factors": ["mom_short", "quality_roe"],
        "top_n": 2,
        "rebalance": "W-MON",
        "events": [{"type": "hold_max_days", "days": 3}],
    }
    res = run_spec_backtest(panel, spec, initial_cash=1_000_000)
    assert len(res.equity) > 5
