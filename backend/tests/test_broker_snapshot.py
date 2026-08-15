import json
from datetime import datetime, timedelta, timezone

import pytest

from app.trading.broker_snapshot import (
    BrokerSnapshotError,
    broker_snapshot_status,
    load_broker_snapshot,
    normalized_positions,
)


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
        "positions": [{
            "code": "600000", "total_qty": 100, "sellable_qty": 100,
            "avg_price": 10.0, "unrealized_pnl": 0.0,
        }],
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


def test_zero_quantity_vendor_placeholders_are_not_positions(tmp_path):
    now = datetime.now(timezone.utc)
    payload = valid_snapshot(now)
    payload["positions"] = [{
        "ticker": "018003", "ticker_name": "not_ready", "total_qty": 0,
        "sellable_qty": 0, "avg_price": 0.0, "unrealized_pnl": 0.0,
    }]
    snapshot = load_broker_snapshot(write_snapshot(tmp_path, payload), now=now)

    assert normalized_positions(snapshot) == ()
    status = broker_snapshot_status(
        write_snapshot(tmp_path, payload), now=now)
    assert status["position_count"] == 0
