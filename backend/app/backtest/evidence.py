"""Reproducible, append-only evidence for deterministic backtests.

This module never fetches market data.  Callers must supply a completed panel,
an explicit universe, and a timezone-aware cutoff.  Missing or ambiguous
provenance fails closed instead of producing a result that merely looks
reproducible.
"""
from __future__ import annotations

import hashlib
import json
import platform
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    BacktestExperiment,
    BacktestExperimentResult,
    HoldoutAccessEvent,
    HoldoutSelection,
    utc_now,
)
from ..runtime_settings import get_setting
from ..research.spec import validate_spec
from .engine import BacktestResult, run_equal_weight_buyhold
from .spec_runner import run_spec_backtest
from .validation import split_development_holdout


REPRODUCIBILITY_SETTING_KEYS = (
    "account.initial_cash",
    "trading.commission_rate",
    "trading.commission_min",
    "trading.transfer_fee_rate",
    "trading.stamp_tax_rate",
    "trading.slippage_bps",
    "factor.lookback_short",
    "factor.lookback_mid",
    "factor.lookback_rev",
    "factor.vol_window",
    "factor.turnover_window",
    "factor.neutralize_size",
    "factor.neutralize_board",
    "race.min_trade_days",
    "race.min_closed_trades",
)


def _json_default(value: Any):
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"unsupported evidence value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Canonical JSON used for every persisted fingerprint."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def spec_fingerprint(spec: dict[str, Any]) -> str:
    if not isinstance(spec, dict) or not spec:
        raise ValueError("experiment spec must be a non-empty object")
    return fingerprint(spec)


def normalize_experiment_spec(spec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, dict) or not spec:
        raise ValueError("experiment spec must be a non-empty object")
    normalized, errors = validate_spec(spec)
    if errors:
        raise ValueError("invalid experiment spec: " + "; ".join(errors))
    unsupported = list(normalized.get("unsupported") or [])
    if unsupported:
        raise ValueError(
            "experiment spec contains unsupported behavior: "
            + "; ".join(str(item) for item in unsupported))
    return normalized


def source_code_fingerprint() -> str:
    """Hash the exact deterministic implementation used by the experiment."""
    app_root = Path(__file__).resolve().parents[1]
    relative_paths = (
        "backtest/evidence.py",
        "backtest/engine.py",
        "backtest/execution.py",
        "backtest/metrics.py",
        "backtest/spec_runner.py",
        "backtest/validation.py",
        "factors/definitions.py",
        "factors/score.py",
        "research/spec.py",
    )
    digest = hashlib.sha256()
    for relative in relative_paths:
        path = app_root / relative
        if not path.is_file():
            raise RuntimeError(f"cannot fingerprint missing source: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def runtime_config_snapshot() -> dict[str, Any]:
    """Capture settings and numerical runtime versions that affect results."""
    settings_snapshot = {
        key: get_setting(key) for key in REPRODUCIBILITY_SETTING_KEYS
    }
    return {
        "settings": settings_snapshot,
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    }


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("data_cutoff_at must be timezone-aware")
    if value.utcoffset() is None:
        raise ValueError("data_cutoff_at must have a concrete UTC offset")
    return value.astimezone(timezone.utc)


def normalize_completed_panel(
    panel: pd.DataFrame,
    *,
    universe: list[str],
    data_cutoff_at: datetime,
) -> pd.DataFrame:
    """Validate a complete point-in-time panel and return canonical row order."""
    cutoff = _aware_utc(data_cutoff_at)
    if panel is None or not isinstance(panel, pd.DataFrame) or panel.empty:
        raise ValueError("backtest panel is empty")
    normalized = panel.copy()
    if "close" not in normalized.columns and "收盘" in normalized.columns:
        normalized = normalized.rename(columns={"收盘": "close"})
    missing = {"date", "code", "close"} - set(normalized.columns)
    if missing:
        raise ValueError("backtest panel missing columns: " + ", ".join(sorted(missing)))

    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    if normalized["date"].isna().any():
        raise ValueError("backtest panel contains invalid dates")
    normalized["code"] = normalized["code"].astype(str).str.strip()
    if (normalized["code"] == "").any():
        raise ValueError("backtest panel contains empty stock codes")
    normalized["close"] = pd.to_numeric(normalized["close"], errors="coerce")
    close_values = normalized["close"].to_numpy(dtype=float)
    if not np.isfinite(close_values).all() or (close_values <= 0).any():
        raise ValueError("backtest panel contains invalid close prices")
    numeric = normalized.select_dtypes(include=[np.number])
    if numeric.size and np.isinf(numeric.to_numpy(dtype=float, na_value=np.nan)).any():
        raise ValueError("backtest panel contains infinite numeric values")
    if normalized.duplicated(subset=["date", "code"]).any():
        raise ValueError("backtest panel contains duplicate date/code rows")

    expected_universe = sorted({str(code).strip() for code in universe if str(code).strip()})
    if len(expected_universe) < 2:
        raise ValueError("backtest universe must contain at least two codes")
    panel_universe = sorted(normalized["code"].unique().tolist())
    if panel_universe != expected_universe:
        raise ValueError("panel universe does not match the frozen universe")

    # Daily rows stamped on the cutoff calendar date may still be incomplete.
    # Requiring all rows to be strictly earlier makes that ambiguity explicit.
    max_date = normalized["date"].max()
    if max_date.date() >= cutoff.date():
        raise ValueError("panel includes data on or after the frozen cutoff date")
    if normalized["date"].nunique() < 10:
        raise ValueError("backtest panel needs at least ten distinct dates")
    return normalized.sort_values(["date", "code"], kind="mergesort").reset_index(drop=True)


def panel_artifact_json(panel: pd.DataFrame) -> str:
    """Serialize every used data value in deterministic, replayable order."""
    ordered = panel.reindex(sorted(panel.columns), axis=1).copy()
    for column in ordered.columns:
        if pd.api.types.is_datetime64_any_dtype(ordered[column]):
            ordered[column] = ordered[column].dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    return ordered.to_json(
        orient="records",
        date_format="iso",
        date_unit="us",
        double_precision=15,
    )


def panel_fingerprint(panel: pd.DataFrame) -> str:
    """Hash every column/value after canonical row and column ordering."""
    payload = panel_artifact_json(panel)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def panel_from_artifact(experiment: BacktestExperiment) -> pd.DataFrame:
    """Rehydrate exactly the frozen panel and verify its content address."""
    raw = experiment.data_artifact_json
    actual = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if actual != experiment.data_fingerprint:
        raise ValueError("frozen data artifact fingerprint mismatch")
    try:
        records = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("frozen data artifact is invalid JSON") from error
    if not isinstance(records, list) or not records:
        raise ValueError("frozen data artifact is empty")
    panel = pd.DataFrame(records)
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce")
    if panel["date"].isna().any():
        raise ValueError("frozen data artifact contains invalid dates")
    return panel


def validate_spec_data(panel: pd.DataFrame, spec: dict[str, Any]) -> None:
    """Validate that a factor experiment has usable complete cross-sections.

    Rolling factors naturally contain warm-up NaNs, so whole-column finite
    coverage is neither realistic nor desirable.  A date is usable only when
    enough stocks have finite values for *all* declared factors; this matches
    the eligibility rule used by the backtest runner.
    """
    if spec.get("mode") != "factor_cross_section":
        return
    factors = list(dict.fromkeys(spec.get("factors") or []))
    if not factors:
        raise ValueError("factor experiment has no declared factors")
    missing = [factor for factor in factors if factor not in panel.columns]
    if missing:
        raise ValueError("panel missing declared factors: " + ", ".join(missing))

    numeric = pd.DataFrame(index=panel.index)
    for factor in factors:
        numeric[factor] = pd.to_numeric(panel[factor], errors="coerce")
    finite_all = np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1)

    universe_size = int(panel["code"].astype(str).nunique())
    try:
        requested_top_n = max(1, int(spec.get("top_n") or 1))
    except (TypeError, ValueError):
        requested_top_n = 1
    # At least two securities are needed for a meaningful cross-sectional
    # comparison.  Larger baskets must have enough eligible names to fill the
    # requested basket, capped by the frozen universe itself.
    required_stocks = min(universe_size, max(2, requested_top_n))
    eligible = panel.loc[finite_all, ["date", "code"]]
    eligible_counts = eligible.groupby("date")["code"].nunique()
    usable_dates = eligible_counts[eligible_counts >= required_stocks]
    if len(usable_dates) < 2:
        raise ValueError(
            "factor panel needs at least two usable dates with "
            f"{required_stocks} stocks finite for all declared factors"
        )


def _result_payload(strategy: BacktestResult, anchor: BacktestResult) -> dict[str, Any]:
    return {
        "strategy": strategy.to_dict(),
        "anchor": anchor.to_dict(),
    }


def _record_result(
    db: Session,
    experiment_id: int,
    phase: str,
    payload: dict[str, Any],
) -> BacktestExperimentResult:
    if phase not in {"development", "holdout", "full"}:
        raise ValueError(f"unsupported experiment phase: {phase}")
    existing = (
        db.query(BacktestExperimentResult)
        .filter(
            BacktestExperimentResult.experiment_id == experiment_id,
            BacktestExperimentResult.phase == phase,
        )
        .first()
    )
    if existing is not None:
        if existing.result_fingerprint != fingerprint(payload):
            raise ValueError("an immutable experiment result already exists")
        return existing
    raw = canonical_json(payload)
    row = BacktestExperimentResult(
        experiment_id=experiment_id,
        phase=phase,
        result_json=raw,
        result_fingerprint=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
    db.add(row)
    db.flush()
    return row


def _create_experiment(
    db: Session,
    *,
    normalized: pd.DataFrame,
    spec: dict[str, Any],
    universe: list[str],
    cutoff: datetime,
    validation_mode: str,
    development_ratio: float,
    hypothesis_id: int | None,
    parent_experiment_id: int | None,
) -> tuple[BacktestExperiment, bool]:
    spec_hash = spec_fingerprint(spec)
    config = runtime_config_snapshot()
    config_hash = fingerprint(config)
    code_hash = source_code_fingerprint()
    codes = sorted({str(code).strip() for code in universe})
    universe_hash = fingerprint(codes)
    data_artifact = panel_artifact_json(normalized)
    data_hash = hashlib.sha256(data_artifact.encode("utf-8")).hexdigest()
    campaign_key = fingerprint({
        "data": data_hash,
        "universe": universe_hash,
        "cutoff": cutoff.isoformat(),
    })
    key_payload = {
        "hypothesis_id": hypothesis_id,
        "parent_experiment_id": parent_experiment_id,
        "campaign": campaign_key,
        "validation_mode": validation_mode,
        "development_ratio": float(development_ratio),
        "spec": spec_hash,
        "code": code_hash,
        "config": config_hash,
    }
    experiment_key = fingerprint(key_payload)
    existing = (
        db.query(BacktestExperiment)
        .filter(BacktestExperiment.experiment_key == experiment_key)
        .first()
    )
    if existing is not None:
        if existing.status not in {
            "development_completed", "completed", "holdout_failed",
        }:
            raise RuntimeError(
                f"experiment {existing.id} already exists with status {existing.status}")
        return existing, False

    experiment = BacktestExperiment(
        hypothesis_id=hypothesis_id,
        parent_experiment_id=parent_experiment_id,
        campaign_key=campaign_key,
        experiment_key=experiment_key,
        spec_json=canonical_json(spec),
        spec_fingerprint=spec_hash,
        code_fingerprint=code_hash,
        config_json=canonical_json(config),
        config_fingerprint=config_hash,
        data_fingerprint=data_hash,
        data_artifact_json=data_artifact,
        universe_json=canonical_json(codes),
        universe_fingerprint=universe_hash,
        data_cutoff_at=cutoff,
        data_start=str(normalized["date"].min())[:10],
        data_end=str(normalized["date"].max())[:10],
        row_count=len(normalized),
        date_count=int(normalized["date"].nunique()),
        validation_mode=validation_mode,
        development_ratio=float(development_ratio),
        status="running",
    )
    db.add(experiment)
    db.commit()  # the attempted run itself is permanent before execution begins
    db.refresh(experiment)
    return experiment, True


def _mark_failed(db: Session, experiment_id: int, error: Exception) -> None:
    db.rollback()
    failed = db.get(BacktestExperiment, experiment_id)
    if failed is not None:
        failed.status = "failed"
        failed.failure_reason = str(error)[:2000]
        failed.completed_at = utc_now()
        db.commit()


def run_reproducible_experiment(
    db: Session,
    *,
    panel: pd.DataFrame,
    spec: dict[str, Any],
    universe: list[str],
    data_cutoff_at: datetime,
    hypothesis_id: int | None = None,
    parent_experiment_id: int | None = None,
    development_ratio: float = 0.8,
) -> BacktestExperiment:
    """Persist one campaign candidate and run only its development phase."""
    if not 0.5 <= float(development_ratio) <= 0.9:
        raise ValueError("development_ratio must be between 0.5 and 0.9")
    spec = normalize_experiment_spec(spec)
    cutoff = _aware_utc(data_cutoff_at)
    normalized = normalize_completed_panel(
        panel, universe=universe, data_cutoff_at=cutoff)
    validate_spec_data(normalized, spec)
    development, holdout = split_development_holdout(
        normalized, development_ratio=float(development_ratio))
    if development.empty or holdout.empty:
        raise ValueError("development/holdout split is not viable")
    if development["date"].nunique() < 2 or holdout["date"].nunique() < 2:
        raise ValueError("development and holdout each need at least two dates")
    validate_spec_data(development, spec)
    validate_spec_data(holdout, spec)

    experiment, created = _create_experiment(
        db,
        normalized=normalized,
        spec=spec,
        universe=universe,
        cutoff=cutoff,
        validation_mode="development_holdout",
        development_ratio=float(development_ratio),
        hypothesis_id=hypothesis_id,
        parent_experiment_id=parent_experiment_id,
    )
    if not created:
        return experiment

    try:
        development_strategy = run_spec_backtest(development, spec)
        development_anchor = run_equal_weight_buyhold(development)
        _record_result(
            db,
            experiment.id,
            "development",
            _result_payload(development_strategy, development_anchor),
        )
        experiment.status = "development_completed"
        experiment.completed_at = utc_now()
        db.commit()
        db.refresh(experiment)
        return experiment
    except Exception as error:
        _mark_failed(db, experiment.id, error)
        raise


def run_reproducible_development(
    db: Session,
    *,
    panel: pd.DataFrame,
    spec: dict[str, Any],
    universe: list[str],
    data_cutoff_at: datetime,
    hypothesis_id: int | None = None,
    parent_experiment_id: int | None = None,
) -> BacktestExperiment:
    """Run a development-only experiment that has no holdout result to leak."""
    spec = normalize_experiment_spec(spec)
    cutoff = _aware_utc(data_cutoff_at)
    normalized = normalize_completed_panel(
        panel, universe=universe, data_cutoff_at=cutoff)
    validate_spec_data(normalized, spec)
    experiment, created = _create_experiment(
        db,
        normalized=normalized,
        spec=spec,
        universe=universe,
        cutoff=cutoff,
        validation_mode="development_only",
        development_ratio=1.0,
        hypothesis_id=hypothesis_id,
        parent_experiment_id=parent_experiment_id,
    )
    if not created:
        return experiment
    try:
        strategy = run_spec_backtest(normalized, spec)
        anchor = run_equal_weight_buyhold(normalized)
        _record_result(
            db,
            experiment.id,
            "development",
            _result_payload(strategy, anchor),
        )
        experiment.status = "completed"
        experiment.completed_at = utc_now()
        db.commit()
        db.refresh(experiment)
        return experiment
    except Exception as error:
        _mark_failed(db, experiment.id, error)
        raise


def get_result(
    db: Session,
    experiment_id: int,
    phase: str,
) -> BacktestExperimentResult:
    result = (
        db.query(BacktestExperimentResult)
        .filter(
            BacktestExperimentResult.experiment_id == experiment_id,
            BacktestExperimentResult.phase == phase,
        )
        .first()
    )
    if result is None:
        raise ValueError(f"experiment {experiment_id} has no {phase} result")
    return result


def result_dict(result: BacktestExperimentResult) -> dict[str, Any]:
    return json.loads(result.result_json)


def _reserve_holdout(
    db: Session,
    experiment: BacktestExperiment,
    *,
    actor: str,
) -> HoldoutSelection:
    development = get_result(db, experiment.id, "development")
    existing = (
        db.query(HoldoutSelection)
        .filter(HoldoutSelection.campaign_key == experiment.campaign_key)
        .first()
    )
    if existing is not None:
        if existing.experiment_id != experiment.id:
            raise ValueError(
                "campaign holdout is permanently reserved for another experiment")
        if (
            existing.development_result_id != development.id
            or existing.development_result_fingerprint != development.result_fingerprint
        ):
            raise ValueError("holdout reservation development evidence mismatch")
        return existing

    selection = HoldoutSelection(
        campaign_key=experiment.campaign_key,
        experiment_id=experiment.id,
        development_result_id=development.id,
        development_result_fingerprint=development.result_fingerprint,
        selected_by=(actor or "local_user")[:80],
    )
    db.add(selection)
    try:
        # Commit the unique reservation before loading/running holdout.  A
        # crash may retry this experiment, but can never switch candidates.
        db.commit()
        db.refresh(selection)
        return selection
    except IntegrityError as error:
        db.rollback()
        winner = (
            db.query(HoldoutSelection)
            .filter(HoldoutSelection.campaign_key == experiment.campaign_key)
            .first()
        )
        if winner is not None and winner.experiment_id == experiment.id:
            return winner
        raise ValueError(
            "campaign holdout is permanently reserved for another experiment") from error


def _run_selected_holdout(
    db: Session,
    experiment: BacktestExperiment,
) -> BacktestExperimentResult:
    existing = (
        db.query(BacktestExperimentResult)
        .filter(
            BacktestExperimentResult.experiment_id == experiment.id,
            BacktestExperimentResult.phase == "holdout",
        )
        .first()
    )
    if existing is not None:
        return existing
    try:
        panel = panel_from_artifact(experiment)
        cutoff = experiment.data_cutoff_at
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        universe = json.loads(experiment.universe_json)
        normalized = normalize_completed_panel(
            panel,
            universe=universe,
            data_cutoff_at=cutoff,
        )
        spec = normalize_experiment_spec(json.loads(experiment.spec_json))
        validate_spec_data(normalized, spec)
        _development, holdout = split_development_holdout(
            normalized, development_ratio=experiment.development_ratio)
        if holdout.empty or holdout["date"].nunique() < 2:
            raise ValueError("selected campaign holdout is not viable")
        validate_spec_data(holdout, spec)
        strategy = run_spec_backtest(holdout, spec)
        anchor = run_equal_weight_buyhold(holdout)
        result = _record_result(
            db,
            experiment.id,
            "holdout",
            _result_payload(strategy, anchor),
        )
        experiment.status = "completed"
        experiment.failure_reason = ""
        experiment.completed_at = utc_now()
        db.commit()
        db.refresh(result)
        return result
    except Exception as error:
        db.rollback()
        failed = db.get(BacktestExperiment, experiment.id)
        if failed is not None:
            failed.status = "holdout_failed"
            failed.failure_reason = str(error)[:2000]
            failed.completed_at = utc_now()
            db.commit()
        raise


def open_holdout(
    db: Session,
    experiment_id: int,
    *,
    actor: str = "local_user",
    purpose: str = "final_validation",
) -> dict[str, Any]:
    """Atomically select one campaign candidate, then run/reveal its holdout."""
    experiment = db.get(BacktestExperiment, experiment_id)
    if experiment is None:
        raise ValueError("experiment does not exist")
    if experiment.validation_mode != "development_holdout":
        raise ValueError("experiment has no selectable holdout")
    if experiment.status not in {
        "development_completed", "completed", "holdout_failed",
    }:
        raise ValueError("experiment development phase is not completed")
    selection = _reserve_holdout(db, experiment, actor=actor)
    experiment = db.get(BacktestExperiment, experiment.id)
    result = _run_selected_holdout(db, experiment)
    experiment = db.get(BacktestExperiment, experiment.id)
    if experiment.holdout_opened_at is None:
        experiment.holdout_opened_at = utc_now()
    access = HoldoutAccessEvent(
        selection_id=selection.id,
        experiment_id=experiment.id,
        result_id=result.id,
        result_fingerprint=result.result_fingerprint,
        actor=(actor or "local_user")[:80],
        purpose=(purpose or "final_validation")[:200],
    )
    db.add(access)
    db.commit()
    return result_dict(result)


def experiment_dict(
    db: Session,
    experiment: BacktestExperiment,
    *,
    include_holdout: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": experiment.id,
        "hypothesis_id": experiment.hypothesis_id,
        "campaign_key": experiment.campaign_key,
        "experiment_key": experiment.experiment_key,
        "status": experiment.status,
        "validation_mode": experiment.validation_mode,
        "spec": json.loads(experiment.spec_json),
        "fingerprints": {
            "spec": experiment.spec_fingerprint,
            "code": experiment.code_fingerprint,
            "config": experiment.config_fingerprint,
            "data": experiment.data_fingerprint,
            "universe": experiment.universe_fingerprint,
        },
        "data_cutoff_at": experiment.data_cutoff_at.isoformat(),
        "data_range": {
            "start": experiment.data_start,
            "end": experiment.data_end,
            "rows": experiment.row_count,
            "dates": experiment.date_count,
        },
        "holdout_opened": experiment.holdout_opened_at is not None,
    }
    try:
        payload["development"] = result_dict(
            get_result(db, experiment.id, "development"))
    except ValueError:
        payload["development"] = None
    try:
        payload["full"] = result_dict(get_result(db, experiment.id, "full"))
    except ValueError:
        payload["full"] = None
    if include_holdout:
        if experiment.validation_mode != "development_holdout":
            raise ValueError("this experiment has no holdout phase")
        if experiment.holdout_opened_at is None:
            raise ValueError("holdout is sealed; call open_holdout first")
        payload["holdout"] = result_dict(get_result(db, experiment.id, "holdout"))
    else:
        payload["holdout"] = None
        payload["holdout_sealed"] = (
            experiment.validation_mode == "development_holdout"
            and experiment.holdout_opened_at is None
        )
    return payload
