import json
from datetime import datetime, timedelta, timezone

import pytest

from app.trading.broker_snapshot import (
    BrokerSnapshotError,
    broker_snapshot_status,
    load_broker_snapshot,
    reconcile_snapshot_projection,
    require_reference_snapshot,
)
from app.models import Account, Model, Position


def valid_snapshot(now: datetime) -> dict:
    return {
        "schema_version": 1,
        "provider": "emt",
        "mode": "simulation",
        "read_only": True,
        "connected": True,
        "reconciled": True,
        "account_ref": "emt-sim-primary",
        "observed_at": now.isoformat(),
        "asset": {"total_asset": 200_000.0, "buying_power": 180_000.0},
        "positions": [{"code": "600000", "total_qty": 100}],
        "orders": [],
        "trades": [],
        "query_errors": [],
    }


def write_snapshot(tmp_path, payload: dict):
    path = tmp_path / "emt_snapshot.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_loads_fresh_read_only_simulation_snapshot(tmp_path):
    now = datetime.now(timezone.utc)
    path = write_snapshot(tmp_path, valid_snapshot(now))

    snapshot = load_broker_snapshot(path, now=now)

    assert snapshot.account_ref == "emt-sim-primary"
    assert snapshot.asset["buying_power"] == 180_000.0
    assert snapshot.positions[0]["code"] == "600000"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mode", "live", "only an EMT simulation account"),
        ("read_only", False, "bridge must be read-only"),
        ("provider", "unknown", "unexpected broker provider"),
    ],
)
def test_rejects_unauthorized_snapshot_modes(tmp_path, field, value, message):
    now = datetime.now(timezone.utc)
    payload = valid_snapshot(now)
    payload[field] = value
    path = write_snapshot(tmp_path, payload)

    with pytest.raises(BrokerSnapshotError, match=message):
        load_broker_snapshot(path, now=now)


def test_rejects_stale_or_future_snapshot(tmp_path):
    now = datetime.now(timezone.utc)
    stale = valid_snapshot(now - timedelta(seconds=61))
    with pytest.raises(BrokerSnapshotError, match="stale"):
        load_broker_snapshot(write_snapshot(tmp_path, stale), now=now)

    future = valid_snapshot(now + timedelta(seconds=6))
    with pytest.raises(BrokerSnapshotError, match="future"):
        load_broker_snapshot(write_snapshot(tmp_path, future), now=now)


@pytest.mark.parametrize("secret_key", ["password", "token", "account_id", "phone"])
def test_rejects_credentials_or_raw_account_identity_anywhere(tmp_path, secret_key):
    now = datetime.now(timezone.utc)
    payload = valid_snapshot(now)
    payload["positions"][0][secret_key] = "must-not-cross-process-boundary"

    with pytest.raises(BrokerSnapshotError, match="forbidden"):
        load_broker_snapshot(write_snapshot(tmp_path, payload), now=now)


def test_status_fails_closed_for_missing_and_incomplete_snapshot(tmp_path):
    now = datetime.now(timezone.utc)
    missing = broker_snapshot_status(tmp_path / "missing.json", now=now)
    assert missing["reference_ready"] is False
    assert missing["configured"] is False

    payload = valid_snapshot(now)
    payload["reconciled"] = False
    incomplete = broker_snapshot_status(write_snapshot(tmp_path, payload), now=now)
    assert incomplete["configured"] is True
    assert incomplete["reference_ready"] is False


def test_status_rejects_complete_vendor_demo_portfolio_above_capital_boundary(tmp_path):
    now = datetime.now(timezone.utc)
    payload = valid_snapshot(now)
    payload["asset"]["total_asset"] = 456_000_000.0
    path = write_snapshot(tmp_path, payload)

    status = broker_snapshot_status(path, now=now, max_total_asset=400_000.0)

    assert status["configured"] is True
    assert status["connected"] is True
    assert status["reconciled"] is True
    assert status["capital_boundary_ok"] is False
    assert status["reference_ready"] is False
    assert status["reason"] == "broker total asset exceeds configured capital boundary"


def test_reference_loader_rejects_complete_snapshot_above_boundary(tmp_path):
    now = datetime.now(timezone.utc)
    payload = valid_snapshot(now)
    payload["asset"]["total_asset"] = 456_000_000.0

    with pytest.raises(BrokerSnapshotError, match="capital boundary"):
        require_reference_snapshot(
            write_snapshot(tmp_path, payload),
            now=now,
            max_age_seconds=60,
            max_total_asset=400_000.0,
        )


def test_reconciles_broker_as_mutable_official_portfolio_projection(db, tmp_path):
    now = datetime.now(timezone.utc)
    model = Model(
        name="official",
        type="ensemble",
        enabled=True,
        is_official_strategy=True,
    )
    db.add(model)
    db.flush()
    account = Account(model_pk=model.id, cash=100_000.0, initial_cash=100_000.0)
    stale = Position(
        model_pk=model.id,
        code="000001",
        name="stale",
        total_qty=100,
        available_qty=100,
        avg_cost=10.0,
    )
    db.add_all([account, stale])
    db.commit()
    payload = valid_snapshot(now)
    payload["asset"] = {
        "total_asset": 202_000.0,
        "buying_power": 150_000.0,
        "security_asset": 52_000.0,
    }
    payload["positions"] = [{
        "ticker": "600000",
        "ticker_name": "浦发银行",
        "total_qty": 1_000,
        "sellable_qty": 800,
        "avg_price": 50.0,
        "unrealized_pnl": 2_000.0,
    }]
    snapshot = require_reference_snapshot(
        write_snapshot(tmp_path, payload),
        now=now,
        max_age_seconds=60,
        max_total_asset=400_000.0,
    )

    summary = reconcile_snapshot_projection(
        db,
        model_pk=model.id,
        snapshot=snapshot,
        initial_equity=200_000.0,
    )
    db.commit()

    db.refresh(account)
    db.refresh(stale)
    projected = db.query(Position).filter_by(
        model_pk=model.id, code="600000",
    ).one()
    assert account.cash == 150_000.0
    assert account.initial_cash == 200_000.0
    assert stale.total_qty == 0
    assert stale.available_qty == 0
    assert projected.name == "浦发银行"
    assert projected.total_qty == 1_000
    assert projected.available_qty == 800
    assert projected.avg_cost == 50.0
    assert summary["total_equity"] == 202_000.0
    assert summary["position_count"] == 1
    assert len(summary["snapshot_fingerprint"]) == 64
