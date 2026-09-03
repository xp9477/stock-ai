"""Run-level, zero-capital mechanical shadow benchmark.

The only supported benchmark is ``eligible_universe_equal_weight_v1``.  This
module never fetches prices and never imports or mutates broker/account/order
state.  A caller must freeze the deterministic eligible universe and actual
point-in-time price evidence, then append later real marks.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy.orm import Session

from ..models import (
    Run,
    RunMarketArtifact,
    ShadowBenchmarkMark,
    ShadowBenchmarkSnapshot,
    TradePlan,
)
from .evidence import canonical_json, fingerprint


BENCHMARK_KEY = "eligible_universe_equal_weight_v1"
ARTIFACT_SOURCE_KEY = "engine_strategy_eligible_quote_v1"
TRUSTED_QUOTE_SOURCES = frozenset({"fuyao"})


def _aware_utc(
    value: datetime | str,
    field: str,
    *,
    assume_shanghai: bool = False,
) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{field} must be an ISO datetime") from error
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        if assume_shanghai:
            from zoneinfo import ZoneInfo
            value = value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        else:
            raise ValueError(f"{field} must be timezone-aware")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _codes(raw: list[str], field: str) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be a list")
    codes = sorted({str(code).strip() for code in raw if str(code).strip()})
    if len(codes) != len(raw):
        raise ValueError(f"{field} contains blanks or duplicate codes")
    if any(len(code) != 6 or not code.isdigit() for code in codes):
        raise ValueError(f"{field} contains an invalid stock code")
    return codes


def _quote_evidence(
    raw: Mapping[str, Mapping[str, object]],
    codes: list[str],
    *,
    latest_at: datetime | None = None,
    earliest_quote_at: datetime | None = None,
) -> tuple[dict[str, dict[str, object]], dict[str, float], datetime]:
    if not isinstance(raw, Mapping):
        raise ValueError("quote evidence must be a code-to-quote mapping")
    if sorted(str(code).strip() for code in raw) != codes:
        raise ValueError("quote evidence must exactly cover the frozen universe")
    normalized: dict[str, dict[str, object]] = {}
    prices: dict[str, float] = {}
    received_times: list[datetime] = []
    for code in codes:
        quote = raw.get(code)
        if not isinstance(quote, Mapping):
            raise ValueError(f"quote evidence for {code} is missing")
        if str(quote.get("code") or code).strip() != code:
            raise ValueError(f"quote evidence code mismatch for {code}")
        try:
            price = float(quote.get("price"))
        except (TypeError, ValueError) as error:
            raise ValueError(f"quote evidence price is invalid for {code}") from error
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f"quote evidence price is invalid for {code}")
        source = str(quote.get("source") or "").strip()
        if source not in TRUSTED_QUOTE_SOURCES:
            raise ValueError(f"quote evidence source is not trusted for {code}")
        if quote.get("tradable") is not True:
            raise ValueError(f"quote evidence is not explicitly tradable for {code}")
        quote_asof = _aware_utc(
            quote.get("quote_asof"), f"quote_asof[{code}]", assume_shanghai=True)
        received_at = _aware_utc(
            quote.get("received_at"), f"received_at[{code}]", assume_shanghai=True)
        if received_at < quote_asof:
            raise ValueError(f"quote evidence was received before its as-of time for {code}")
        if latest_at is not None and (quote_asof > latest_at or received_at > latest_at):
            raise ValueError(f"quote evidence exceeds the frozen cutoff for {code}")
        if earliest_quote_at is not None and quote_asof < earliest_quote_at:
            raise ValueError(f"quote evidence predates the scheduled mark for {code}")
        normalized[code] = {
            "code": code,
            "name": str(quote.get("name") or "")[:80],
            "price": price,
            "quote_asof": quote_asof.isoformat(),
            "received_at": received_at.isoformat(),
            "source": source,
            "tradable": True,
            "trade_status": str(quote.get("trade_status") or "")[:40],
        }
        prices[code] = price
        received_times.append(received_at)
    observed_at = max(received_times) if received_times else datetime.min.replace(
        tzinfo=timezone.utc)
    return normalized, prices, observed_at


def freeze_engine_run_artifact(
    db: Session,
    *,
    run_id: int,
    data_cutoff_at: datetime,
    analysis_codes: list[str],
    eligible_quotes: Mapping[str, Mapping[str, object]],
) -> RunMarketArtifact:
    """Persist the exact engine eligibility evidence; callers supply no digest."""
    run = db.get(Run, run_id)
    if run is None:
        raise ValueError("run does not exist")
    cutoff = _aware_utc(data_cutoff_at, "data_cutoff_at")
    analysis = _codes(analysis_codes, "analysis_codes")
    eligible = _codes(list(eligible_quotes), "eligible quote codes")
    if not set(eligible).issubset(set(analysis)):
        raise ValueError("eligible universe is outside the analyzed Run universe")
    evidence, prices, _received_at = _quote_evidence(
        eligible_quotes, eligible, latest_at=cutoff)
    payload = {
        "schema_version": "run_market_artifact_v1",
        "source_key": ARTIFACT_SOURCE_KEY,
        "run_id": run_id,
        "data_cutoff_at": cutoff.isoformat(),
        "analysis_universe": analysis,
        "eligible_universe": eligible,
        "reference_prices": prices,
        "quote_evidence": evidence,
    }
    digest = fingerprint(payload)
    existing = (
        db.query(RunMarketArtifact)
        .filter(RunMarketArtifact.run_id == run_id)
        .first()
    )
    if existing is not None:
        if existing.artifact_fingerprint != digest:
            raise ValueError("run already has a different immutable market artifact")
        verify_run_market_artifact(existing)
        return existing
    artifact = RunMarketArtifact(
        run_id=run_id,
        source_key=ARTIFACT_SOURCE_KEY,
        data_cutoff_at=cutoff,
        analysis_universe_json=canonical_json(analysis),
        eligible_universe_json=canonical_json(eligible),
        reference_prices_json=canonical_json(prices),
        quote_evidence_json=canonical_json(evidence),
        artifact_fingerprint=digest,
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def verify_run_market_artifact(artifact: RunMarketArtifact) -> dict[str, object]:
    """Recompute provenance so a hand-edited artifact fails closed."""
    cutoff = _stored_utc(artifact.data_cutoff_at)
    analysis = _codes(json.loads(artifact.analysis_universe_json), "analysis universe")
    eligible = _codes(json.loads(artifact.eligible_universe_json), "eligible universe")
    if not set(eligible).issubset(set(analysis)):
        raise ValueError("eligible universe is outside the analyzed Run universe")
    evidence_raw = json.loads(artifact.quote_evidence_json)
    evidence, prices, _received_at = _quote_evidence(
        evidence_raw, eligible, latest_at=cutoff)
    stored_prices = json.loads(artifact.reference_prices_json)
    if prices != stored_prices:
        raise ValueError("run market artifact reference prices do not match quote evidence")
    payload = {
        "schema_version": "run_market_artifact_v1",
        "source_key": artifact.source_key,
        "run_id": artifact.run_id,
        "data_cutoff_at": cutoff.isoformat(),
        "analysis_universe": analysis,
        "eligible_universe": eligible,
        "reference_prices": prices,
        "quote_evidence": evidence,
    }
    if artifact.source_key != ARTIFACT_SOURCE_KEY:
        raise ValueError("run market artifact source is not trusted")
    if fingerprint(payload) != artifact.artifact_fingerprint:
        raise ValueError("run market artifact fingerprint mismatch")
    return payload


def freeze_run_shadow_snapshot(
    db: Session,
    *,
    run_id: int,
    scheduled_execution_at: datetime,
    idempotency_key: str,
) -> ShadowBenchmarkSnapshot:
    """Freeze a benchmark only from the engine-produced immutable Run artifact."""
    key = str(idempotency_key or "").strip()
    if not key:
        raise ValueError("idempotency_key is required")
    existing = (
        db.query(ShadowBenchmarkSnapshot)
        .filter(ShadowBenchmarkSnapshot.idempotency_key == key)
        .first()
    )
    if existing is not None:
        if existing.run_id != run_id:
            raise ValueError("idempotency key belongs to another shadow snapshot")
        if existing.artifact_id is None:
            raise ValueError("legacy shadow snapshot has no trusted Run artifact")
        return existing
    run = db.get(Run, run_id)
    if run is None:
        raise ValueError("run does not exist")
    plans = (
        db.query(TradePlan)
        .filter(TradePlan.run_id == run_id)
        .order_by(TradePlan.id)
        .all()
    )
    if not plans:
        raise ValueError("run has no TradePlan lifecycle to attach the shadow snapshot")
    artifact = (
        db.query(RunMarketArtifact)
        .filter(RunMarketArtifact.run_id == run_id)
        .first()
    )
    if artifact is None:
        raise ValueError("run has no trusted market artifact")
    artifact_payload = verify_run_market_artifact(artifact)
    cutoff = _stored_utc(artifact.data_cutoff_at)
    execution = _aware_utc(scheduled_execution_at, "scheduled_execution_at")
    if execution < cutoff:
        raise ValueError("scheduled_execution_at cannot precede signal_cutoff_at")
    if any(_stored_utc(plan.data_cutoff_at) != cutoff for plan in plans):
        raise ValueError("all run TradePlans must share the frozen signal cutoff")

    codes = list(artifact_payload["eligible_universe"])
    if len(codes) < 2:
        raise ValueError("eligible universe must contain at least two codes")
    if not {plan.code for plan in plans if plan.side == "buy"}.issubset(set(codes)):
        raise ValueError("buy TradePlan code is outside the frozen eligible universe")
    prices = dict(artifact_payload["reference_prices"])
    weight = 1.0 / len(codes)
    weights = {code: weight for code in codes}

    run_existing = (
        db.query(ShadowBenchmarkSnapshot)
        .filter(ShadowBenchmarkSnapshot.run_id == run_id)
        .first()
    )
    if run_existing is not None:
        raise ValueError("run already has a different shadow benchmark snapshot")
    snapshot = ShadowBenchmarkSnapshot(
        run_id=run_id,
        artifact_id=artifact.id,
        benchmark_key=BENCHMARK_KEY,
        signal_cutoff_at=cutoff,
        scheduled_execution_at=execution,
        universe_json=canonical_json(codes),
        universe_fingerprint=fingerprint(codes),
        weights_json=canonical_json(weights),
        reference_prices_json=canonical_json(prices),
        reference_data_fingerprint=artifact.artifact_fingerprint,
        idempotency_key=key[:128],
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def append_shadow_mark(
    db: Session,
    *,
    snapshot_id: int,
    quote_evidence: Mapping[str, Mapping[str, object]],
    idempotency_key: str,
) -> ShadowBenchmarkMark:
    """Append one real observed mark; no quote is invented or fetched here."""
    key = str(idempotency_key or "").strip()
    if not key:
        raise ValueError("idempotency_key is required")
    existing = (
        db.query(ShadowBenchmarkMark)
        .filter(ShadowBenchmarkMark.idempotency_key == key)
        .first()
    )
    if existing is not None:
        if existing.snapshot_id != snapshot_id:
            raise ValueError("idempotency key belongs to another shadow mark")
        return existing
    snapshot = db.get(ShadowBenchmarkSnapshot, snapshot_id)
    if snapshot is None:
        raise ValueError("shadow benchmark snapshot does not exist")
    codes = json.loads(snapshot.universe_json)
    references = json.loads(snapshot.reference_prices_json)
    weights = json.loads(snapshot.weights_json)
    evidence, prices, observed = _quote_evidence(
        quote_evidence,
        codes,
        earliest_quote_at=_stored_utc(snapshot.scheduled_execution_at),
    )
    benchmark_return = sum(
        float(weights[code]) * (prices[code] / float(references[code]) - 1.0)
        for code in codes
    )
    mark = ShadowBenchmarkMark(
        snapshot_id=snapshot.id,
        observed_at=observed,
        observed_prices_json=canonical_json(prices),
        data_fingerprint=fingerprint({
            "schema_version": "shadow_mark_evidence_v1",
            "snapshot_id": snapshot.id,
            "quote_evidence": evidence,
        }),
        benchmark_return_pct=benchmark_return,
        idempotency_key=key[:128],
    )
    db.add(mark)
    db.commit()
    db.refresh(mark)
    return mark


def snapshot_dict(snapshot: ShadowBenchmarkSnapshot) -> dict:
    return {
        "id": snapshot.id,
        "run_id": snapshot.run_id,
        "artifact_id": snapshot.artifact_id,
        "benchmark_key": snapshot.benchmark_key,
        "signal_cutoff_at": snapshot.signal_cutoff_at.isoformat(),
        "scheduled_execution_at": snapshot.scheduled_execution_at.isoformat(),
        "eligible_codes": json.loads(snapshot.universe_json),
        "weights": json.loads(snapshot.weights_json),
        "reference_prices": json.loads(snapshot.reference_prices_json),
        "reference_data_fingerprint": snapshot.reference_data_fingerprint,
    }
