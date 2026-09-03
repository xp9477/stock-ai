import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pandas as pd
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app import database as database_module
from app.backtest import evidence, shadow
from app.database import Base, enable_sqlite_foreign_keys
from app.models import (
    Account,
    AgentOutput,
    BacktestExperiment,
    BacktestExperimentResult,
    Decision,
    GateCheck,
    HoldoutAccessEvent,
    HoldoutSelection,
    Order,
    Position,
    Run,
    RunMarketArtifact,
    ShadowBenchmarkMark,
    ShadowBenchmarkSnapshot,
)
from app.trading.trade_plans import create_plan_from_decision


SPEC = {
    "name": "evidence-test",
    "mode": "factor_cross_section",
    "factors": ["mom_short", "quality_roe"],
    "top_n": 1,
    "rebalance": "W-MON",
    "events": [],
}


def _panel(days: int = 30) -> pd.DataFrame:
    rows = []
    for i, day in enumerate(pd.bdate_range("2024-01-02", periods=days)):
        for j, code in enumerate(("000001", "600000")):
            rows.append({
                "date": day,
                "code": code,
                "close": 10.0 + j + i * (0.02 + j * 0.01),
                "mom_short": 0.01 * (i + j),
                "quality_roe": 0.10 + 0.01 * j,
            })
    return pd.DataFrame(rows)


def _run(db, panel=None) -> BacktestExperiment:
    return evidence.run_reproducible_experiment(
        db,
        panel=panel if panel is not None else _panel(),
        spec=SPEC,
        universe=["600000", "000001"],
        data_cutoff_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
    )


def test_factor_validation_allows_warmup_but_requires_usable_complete_dates():
    panel = _panel(days=6)
    panel.loc[panel["date"] < panel["date"].sort_values().unique()[2], "mom_short"] = float("nan")
    evidence.validate_spec_data(panel, SPEC)

    incomplete = panel.copy()
    incomplete.loc[incomplete["code"] == "600000", "quality_roe"] = float("nan")
    with pytest.raises(ValueError, match="two usable dates"):
        evidence.validate_spec_data(incomplete, SPEC)


def test_single_declared_factor_is_a_valid_experiment_contract(db):
    spec = {**SPEC, "factors": ["mom_short"], "top_n": 1}
    panel = _panel(days=10)
    evidence.validate_spec_data(panel, spec)
    result = evidence.run_reproducible_experiment(
        db,
        panel=panel,
        spec=spec,
        universe=["600000", "000001"],
        data_cutoff_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
        development_ratio=0.6,
    )
    assert result.status == "development_completed"


def test_experiment_freezes_all_fingerprints_and_is_idempotent(db):
    first = _run(db)
    shuffled = _panel().sample(frac=1.0, random_state=7)
    repeated = _run(db, shuffled)

    assert repeated.id == first.id
    assert first.status == "development_completed"
    assert first.holdout_opened_at is None
    assert first.data_cutoff_at is not None
    for digest in (
        first.experiment_key,
        first.spec_fingerprint,
        first.code_fingerprint,
        first.config_fingerprint,
        first.data_fingerprint,
        first.universe_fingerprint,
    ):
        assert len(digest) == 64
    assert db.query(BacktestExperiment).count() == 1
    assert db.query(BacktestExperimentResult).count() == 1

    public = evidence.experiment_dict(db, first)
    assert public["development"] is not None
    assert public["holdout"] is None
    assert public["holdout_sealed"] is True
    with pytest.raises(ValueError, match="sealed"):
        evidence.experiment_dict(db, first, include_holdout=True)


def test_opening_holdout_never_recomputes_and_appends_access(db):
    experiment = _run(db)
    with pytest.raises(ValueError, match="no holdout result"):
        evidence.get_result(db, experiment.id, "holdout")

    first = evidence.open_holdout(db, experiment.id, actor="tester")
    fingerprint_before = evidence.get_result(
        db, experiment.id, "holdout").result_fingerprint
    second = evidence.open_holdout(db, experiment.id, actor="tester")

    assert first == second
    assert evidence.get_result(
        db, experiment.id, "holdout").result_fingerprint == fingerprint_before
    assert db.query(BacktestExperimentResult).count() == 2
    assert db.query(HoldoutSelection).count() == 1
    assert db.query(HoldoutAccessEvent).count() == 2
    assert db.get(BacktestExperiment, experiment.id).holdout_opened_at is not None


def test_campaign_can_select_only_one_holdout_candidate_forever(db):
    first = _run(db)
    second = evidence.run_reproducible_experiment(
        db,
        panel=_panel(),
        spec=dict(SPEC, name="second-candidate", top_n=2),
        universe=["000001", "600000"],
        data_cutoff_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
        development_ratio=0.7,
    )
    assert first.id != second.id
    assert first.campaign_key == second.campaign_key

    evidence.open_holdout(db, first.id, actor="selector")
    with pytest.raises(ValueError, match="reserved for another experiment"):
        evidence.open_holdout(db, second.id, actor="selector")

    selection = db.query(HoldoutSelection).one()
    assert selection.experiment_id == first.id
    assert evidence.get_result(db, first.id, "holdout") is not None
    with pytest.raises(ValueError, match="no holdout result"):
        evidence.get_result(db, second.id, "holdout")
    # Re-opening the winner is a read of the same immutable result, not a rerun.
    evidence.open_holdout(db, first.id, actor="selector")
    assert db.query(HoldoutSelection).count() == 1


def test_split_ratio_changes_experiment_identity_but_not_campaign(db):
    first = _run(db)
    second = evidence.run_reproducible_experiment(
        db,
        panel=_panel(),
        spec=SPEC,
        universe=["000001", "600000"],
        data_cutoff_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
        development_ratio=0.7,
    )

    assert first.campaign_key == second.campaign_key
    assert first.experiment_key != second.experiment_key
    assert first.id != second.id
    assert first.development_ratio == pytest.approx(0.8)
    assert second.development_ratio == pytest.approx(0.7)


def test_campaign_reservation_is_atomic_across_concurrent_connections(tmp_path):
    database_path = (tmp_path / "holdout-race.db").as_posix()
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    event.listen(engine, "connect", enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    seed = Session()
    try:
        first = _run(seed)
        second = evidence.run_reproducible_experiment(
            seed,
            panel=_panel(),
            spec=dict(SPEC, name="concurrent-candidate", top_n=2),
            universe=["000001", "600000"],
            data_cutoff_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
            development_ratio=0.7,
        )
        assert first.campaign_key == second.campaign_key
        campaign_key = first.campaign_key
        candidates = (first.id, second.id)
    finally:
        seed.close()

    barrier = Barrier(2)

    def reserve(experiment_id: int) -> int | None:
        session = Session()
        try:
            development = evidence.get_result(session, experiment_id, "development")
            session.add(HoldoutSelection(
                campaign_key=campaign_key,
                experiment_id=experiment_id,
                development_result_id=development.id,
                development_result_fingerprint=development.result_fingerprint,
                selected_by="concurrent-test",
            ))
            barrier.wait(timeout=5)
            try:
                session.commit()
                return experiment_id
            except IntegrityError:
                session.rollback()
                return None
        finally:
            session.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(reserve, candidates))
        assert sum(outcome is not None for outcome in outcomes) == 1
        verify = Session()
        try:
            selection = verify.query(HoldoutSelection).one()
            assert selection.experiment_id in candidates
        finally:
            verify.close()
    finally:
        engine.dispose()


def test_experiment_and_results_reject_rewrite_or_delete(db):
    experiment = _run(db)
    result = evidence.get_result(db, experiment.id, "development")

    experiment.spec_json = evidence.canonical_json({"name": "tampered"})
    with pytest.raises(ValueError, match="provenance is immutable"):
        db.commit()
    db.rollback()

    result = evidence.get_result(db, experiment.id, "development")
    result.result_json = "{}"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()

    experiment = db.get(BacktestExperiment, experiment.id)
    db.delete(experiment)
    with pytest.raises(ValueError, match="permanent audit evidence"):
        db.commit()
    db.rollback()


@pytest.mark.parametrize("case", ["naive_cutoff", "same_day", "missing_code", "duplicate"])
def test_incomplete_or_ambiguous_data_fails_closed(db, case):
    panel = _panel()
    universe = ["000001", "600000"]
    cutoff = datetime(2024, 3, 1, tzinfo=timezone.utc)
    if case == "naive_cutoff":
        cutoff = datetime(2024, 3, 1)
    elif case == "same_day":
        cutoff = panel["date"].max().to_pydatetime().replace(tzinfo=timezone.utc)
    elif case == "missing_code":
        panel = panel[panel["code"] == "000001"]
    elif case == "duplicate":
        panel = pd.concat([panel, panel.iloc[[0]]], ignore_index=True)

    with pytest.raises(ValueError):
        evidence.run_reproducible_experiment(
            db,
            panel=panel,
            spec=SPEC,
            universe=universe,
            data_cutoff_at=cutoff,
        )
    assert db.query(BacktestExperiment).count() == 0


def _plan(db, model_a):
    now = datetime(2026, 8, 10, 1, 30, tzinfo=timezone.utc)
    run = Run(trigger="schedule")
    db.add(run)
    db.flush()
    decision = Decision(
        run_id=run.id,
        model_pk=model_a.id,
        code="600519",
        name="测试股",
        action="buy",
        target_position_pct=0.2,
        confidence=0.8,
        reason="候选",
    )
    db.add(decision)
    db.flush()
    plan = create_plan_from_decision(
        db,
        decision,
        reference_price=100.0,
        max_buy_price=103.0,
        data_cutoff_at=now - timedelta(hours=10),
        valid_from_at=now,
        expires_at=now + timedelta(hours=1),
        idempotency_key="shadow-plan",
        commit=True,
    )
    return plan, now - timedelta(hours=10), now


def _trusted_quote(code: str, price: float, quote_asof: datetime, received_at=None):
    return {
        "code": code,
        "name": f"stock-{code}",
        "price": price,
        "quote_asof": quote_asof.isoformat(),
        "received_at": (received_at or quote_asof + timedelta(seconds=1)).isoformat(),
        "source": "fuyao",
        "tradable": True,
        "trade_status": "tradable",
    }


def _freeze_artifact(db, run_id: int, cutoff: datetime):
    return shadow.freeze_engine_run_artifact(
        db,
        run_id=run_id,
        data_cutoff_at=cutoff,
        analysis_codes=["600519", "000001"],
        eligible_quotes={
            "600519": _trusted_quote("600519", 100.0, cutoff - timedelta(minutes=2)),
            "000001": _trusted_quote("000001", 50.0, cutoff - timedelta(minutes=2)),
        },
    )


def test_run_shadow_is_fixed_equal_weight_and_zero_capital(db, model_a):
    plan, cutoff, scheduled_at = _plan(db, model_a)
    artifact = _freeze_artifact(db, plan.run_id, cutoff)
    before = (
        db.query(Account).count(),
        db.query(Position).count(),
        db.query(Order).count(),
    )
    snapshot = shadow.freeze_run_shadow_snapshot(
        db,
        run_id=plan.run_id,
        scheduled_execution_at=scheduled_at,
        idempotency_key="shadow-snapshot",
    )
    mark = shadow.append_shadow_mark(
        db,
        snapshot_id=snapshot.id,
        quote_evidence={
            "600519": _trusted_quote(
                "600519", 110.0, scheduled_at + timedelta(minutes=4),
                scheduled_at + timedelta(minutes=5)),
            "000001": _trusted_quote(
                "000001", 55.0, scheduled_at + timedelta(minutes=4),
                scheduled_at + timedelta(minutes=5)),
        },
        idempotency_key="shadow-mark",
    )
    repeated = shadow.append_shadow_mark(
        db,
        snapshot_id=snapshot.id,
        quote_evidence={
            "600519": _trusted_quote(
                "600519", 110.0, scheduled_at + timedelta(minutes=4),
                scheduled_at + timedelta(minutes=5)),
            "000001": _trusted_quote(
                "000001", 55.0, scheduled_at + timedelta(minutes=4),
                scheduled_at + timedelta(minutes=5)),
        },
        idempotency_key="shadow-mark",
    )

    assert repeated.id == mark.id
    assert snapshot.artifact_id == artifact.id
    assert snapshot.reference_data_fingerprint == artifact.artifact_fingerprint
    assert snapshot.benchmark_key == "eligible_universe_equal_weight_v1"
    assert json.loads(snapshot.weights_json) == {"000001": 0.5, "600519": 0.5}
    assert mark.benchmark_return_pct == pytest.approx(0.10)
    assert db.query(ShadowBenchmarkSnapshot).count() == 1
    assert db.query(ShadowBenchmarkMark).count() == 1
    assert before == (
        db.query(Account).count(),
        db.query(Position).count(),
        db.query(Order).count(),
    )
    referenced_tables = {
        fk.column.table.name
        for model in (ShadowBenchmarkSnapshot, ShadowBenchmarkMark)
        for fk in model.__table__.foreign_keys
    }
    assert not {"account", "positions", "orders"}.intersection(referenced_tables)


def test_shadow_fails_closed_without_real_observation_evidence(db, model_a):
    plan, cutoff, scheduled_at = _plan(db, model_a)
    with pytest.raises(ValueError, match="no trusted market artifact"):
        shadow.freeze_run_shadow_snapshot(
            db,
            run_id=plan.run_id,
            scheduled_execution_at=scheduled_at,
            idempotency_key="missing-data",
        )

    with pytest.raises(ValueError, match="source is not trusted"):
        shadow.freeze_engine_run_artifact(
            db,
            run_id=plan.run_id,
            data_cutoff_at=cutoff,
            analysis_codes=["600519", "000001"],
            eligible_quotes={
                "600519": _trusted_quote(
                    "600519", 100.0, cutoff - timedelta(minutes=2))
                    | {"source": "caller-asserted"},
                "000001": _trusted_quote(
                    "000001", 50.0, cutoff - timedelta(minutes=2))
                    | {"source": "caller-asserted"},
            },
        )
    assert db.query(RunMarketArtifact).count() == 0

    _freeze_artifact(db, plan.run_id, cutoff)
    snapshot = shadow.freeze_run_shadow_snapshot(
        db,
        run_id=plan.run_id,
        scheduled_execution_at=scheduled_at,
        idempotency_key="valid-snapshot",
    )
    with pytest.raises(ValueError, match="predates the scheduled mark"):
        shadow.append_shadow_mark(
            db,
            snapshot_id=snapshot.id,
            quote_evidence={
                "600519": _trusted_quote(
                    "600519", 99.0, scheduled_at - timedelta(seconds=1),
                    scheduled_at + timedelta(seconds=1)),
                "000001": _trusted_quote(
                    "000001", 50.0, scheduled_at - timedelta(seconds=1),
                    scheduled_at + timedelta(seconds=1)),
            },
            idempotency_key="bad-time",
        )


def test_canonical_hashes_ignore_mapping_and_row_order():
    assert evidence.fingerprint({"a": 1, "b": 2}) == evidence.fingerprint({"b": 2, "a": 1})
    panel = evidence.normalize_completed_panel(
        _panel(),
        universe=["000001", "600000"],
        data_cutoff_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
    )
    shuffled = evidence.normalize_completed_panel(
        _panel().sample(frac=1.0, random_state=4),
        universe=["600000", "000001"],
        data_cutoff_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
    )
    assert evidence.panel_fingerprint(panel) == evidence.panel_fingerprint(shuffled)
    changed = shuffled.copy()
    changed.loc[0, "close"] += 0.01
    assert evidence.panel_fingerprint(panel) != evidence.panel_fingerprint(changed)


def test_declared_factor_cannot_silently_fall_back_to_available_columns(db):
    bad_spec = dict(SPEC, factors=["mom_short", "ep"])
    with pytest.raises(ValueError, match="missing declared factors: ep"):
        evidence.run_reproducible_experiment(
            db,
            panel=_panel(),
            spec=bad_spec,
            universe=["000001", "600000"],
            data_cutoff_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
        )
    assert db.query(BacktestExperiment).count() == 0


def test_invalid_mode_cannot_be_rewritten_to_a_default_strategy(db):
    with pytest.raises(ValueError, match="invalid experiment spec"):
        evidence.run_reproducible_experiment(
            db,
            panel=_panel(),
            spec=dict(SPEC, mode="grid_intraday"),
            universe=["000001", "600000"],
            data_cutoff_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
        )
    assert db.query(BacktestExperiment).count() == 0


def test_sqlite_foreign_keys_are_enabled_and_enforced(db):
    assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    db.add(HoldoutSelection(
        campaign_key="orphan-campaign",
        experiment_id=999,
        development_result_id=999,
        development_result_fingerprint="f" * 64,
        selected_by="test",
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_migrate_installs_audit_triggers_on_an_existing_database(tmp_path, monkeypatch):
    database_path = (tmp_path / "existing.db").as_posix()
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        names = connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_%'"
        )).scalars().all()
        for name in names:
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    monkeypatch.setattr(database_module, "engine", engine)
    try:
        database_module._migrate()
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='trigger' AND name LIKE 'trg_%'"
            )).scalar_one() >= 20
    finally:
        engine.dispose()


def test_lightweight_migration_adds_shadow_and_execution_intent_columns(
    tmp_path, monkeypatch,
):
    database_path = (tmp_path / "legacy-columns.db").as_posix()
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", enable_sqlite_foreign_keys)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE run_market_artifacts (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE shadow_benchmark_snapshots (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE execution_intents (id INTEGER PRIMARY KEY)")
    monkeypatch.setattr(database_module, "engine", engine)
    try:
        database_module._migrate()
        with engine.connect() as connection:
            shadow_columns = {
                row[1] for row in connection.execute(
                    text("PRAGMA table_info(shadow_benchmark_snapshots)"))
            }
            intent_columns = {
                row[1] for row in connection.execute(
                    text("PRAGMA table_info(execution_intents)"))
            }
        assert "artifact_id" in shadow_columns
        assert {
            "authorized_target_position_pct",
            "authorized_notional",
            "authorized_qty",
            "estimated_fee",
            "risk_snapshot_json",
        }.issubset(intent_columns)
    finally:
        engine.dispose()


def test_sqlite_triggers_block_raw_audit_rewrite_and_delete(db, model_a):
    experiment = _run(db)
    development = evidence.get_result(db, experiment.id, "development")
    plan, cutoff, scheduled_at = _plan(db, model_a)
    agent_output = AgentOutput(
        run_id=plan.run_id,
        model_pk=model_a.id,
        code=plan.code,
        agent="decision",
        output="frozen output",
    )
    gate = GateCheck(
        plan_id=plan.id,
        plan_version=plan.version,
        gate_type="price",
        outcome="pass",
    )
    db.add_all([agent_output, gate])
    db.commit()
    artifact = _freeze_artifact(db, plan.run_id, cutoff)
    snapshot = shadow.freeze_run_shadow_snapshot(
        db,
        run_id=plan.run_id,
        scheduled_execution_at=scheduled_at,
        idempotency_key="trigger-shadow-snapshot",
    )
    mark = shadow.append_shadow_mark(
        db,
        snapshot_id=snapshot.id,
        quote_evidence={
            "600519": _trusted_quote(
                "600519", 101.0, scheduled_at + timedelta(minutes=4),
                scheduled_at + timedelta(minutes=5)),
            "000001": _trusted_quote(
                "000001", 50.5, scheduled_at + timedelta(minutes=4),
                scheduled_at + timedelta(minutes=5)),
        },
        idempotency_key="trigger-shadow-mark",
    )
    evidence.open_holdout(db, experiment.id, actor="trigger-test")
    holdout = evidence.get_result(db, experiment.id, "holdout")
    selection = db.query(HoldoutSelection).one()
    access = db.query(HoldoutAccessEvent).one()

    trigger_count = db.execute(text(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type='trigger' AND name LIKE 'trg_%'"
    )).scalar_one()
    assert trigger_count >= 20

    immutable_rows = (
        ("agent_outputs", "output", agent_output.id),
        ("decisions", "reason", plan.decision_id),
        ("gate_checks", "reason", gate.id),
        ("backtest_experiments", "spec_json", experiment.id),
        ("backtest_experiment_results", "result_json", development.id),
        ("backtest_experiment_results", "result_json", holdout.id),
        ("holdout_selections", "selected_by", selection.id),
        ("holdout_access_events", "actor", access.id),
        ("run_market_artifacts", "artifact_fingerprint", artifact.id),
        ("shadow_benchmark_snapshots", "reference_data_fingerprint", snapshot.id),
        ("shadow_benchmark_marks", "data_fingerprint", mark.id),
    )
    for table, column, row_id in immutable_rows:
        with pytest.raises(DBAPIError):
            db.execute(
                text(f"UPDATE {table} SET {column}=:value WHERE id=:id"),
                {"id": row_id, "value": "tampered"},
            )
        db.rollback()

    permanent_rows = (
        ("agent_outputs", agent_output.id),
        ("decisions", plan.decision_id),
        ("gate_checks", gate.id),
        ("backtest_experiments", experiment.id),
        ("backtest_experiment_results", development.id),
        ("backtest_experiment_results", holdout.id),
        ("holdout_selections", selection.id),
        ("holdout_access_events", access.id),
        ("run_market_artifacts", artifact.id),
        ("shadow_benchmark_snapshots", snapshot.id),
        ("shadow_benchmark_marks", mark.id),
    )
    for table, row_id in permanent_rows:
        with pytest.raises(DBAPIError, match="permanent audit evidence"):
            db.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": row_id})
        db.rollback()
