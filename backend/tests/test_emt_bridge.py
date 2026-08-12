import json
from pathlib import Path

import pytest

from emt_bridge.config import BridgeConfig, BridgeConfigError
from emt_bridge.main import ensure_supported_python
from emt_bridge.state import BridgeState


def test_bridge_config_refuses_live_or_writable_mode(monkeypatch):
    monkeypatch.setenv("EMT_MODE", "live")
    with pytest.raises(BridgeConfigError, match="simulation"):
        BridgeConfig.from_env()

    monkeypatch.setenv("EMT_MODE", "simulation")
    monkeypatch.setenv("EMT_READ_ONLY", "false")
    with pytest.raises(BridgeConfigError, match="remain true"):
        BridgeConfig.from_env()


@pytest.mark.parametrize("version", [(3, 8), (3, 10), (3, 12)])
def test_vendor_python_version_guard_accepts_conservative_range(version):
    ensure_supported_python(version)


@pytest.mark.parametrize("version", [(3, 7), (3, 13), (3, 14)])
def test_vendor_python_version_guard_blocks_unverified_abi(version):
    with pytest.raises(RuntimeError, match="CPython 3.8-3.12"):
        ensure_supported_python(version)


def test_query_generation_becomes_reconciled_only_when_all_queries_finish(tmp_path):
    state = BridgeState(
        account_ref="emt-sim-primary",
        snapshot_path=tmp_path / "snapshot.json",
        journal_path=tmp_path / "events.jsonl",
    )
    state.set_connected(True)
    rows = {
        "asset": {"total_asset": 200_000.0, "buying_power": 180_000.0},
        "positions": {"ticker": "600000", "total_qty": 100, "sellable_qty": 100},
        "orders": {},
        "trades": {},
    }
    for reqid, kind in enumerate(rows, start=11):
        state.begin_query(reqid=reqid, generation=1, kind=kind)
        state.record_query(reqid=reqid, data=rows[kind], error={}, last=True)
        if kind != "trades":
            assert state.snapshot()["reconciled"] is False

    snapshot = state.snapshot()
    assert snapshot["reconciled"] is True
    assert snapshot["asset"]["buying_power"] == 180_000.0
    assert snapshot["positions"] == [{
        "ticker": "600000", "total_qty": 100, "sellable_qty": 100,
    }]


def test_new_generation_immediately_invalidates_previous_reconciliation(tmp_path):
    state = BridgeState(
        account_ref="emt-sim-primary",
        snapshot_path=tmp_path / "snapshot.json",
        journal_path=tmp_path / "events.jsonl",
    )
    state.set_connected(True)
    for reqid, kind in enumerate(("asset", "positions", "orders", "trades"), start=11):
        state.begin_query(reqid=reqid, generation=1, kind=kind)
        data = {"total_asset": 1.0, "buying_power": 1.0} if kind == "asset" else {}
        state.record_query(reqid=reqid, data=data, error={}, last=True)
    assert state.snapshot()["reconciled"] is True

    state.begin_query(reqid=21, generation=2, kind="asset")
    assert state.snapshot()["reconciled"] is False
    assert state.snapshot()["query_errors"] == ["reconciliation in progress"]


def test_empty_asset_response_never_reconciles(tmp_path):
    state = BridgeState(
        account_ref="emt-sim-primary",
        snapshot_path=tmp_path / "snapshot.json",
        journal_path=tmp_path / "events.jsonl",
    )
    state.set_connected(True)
    for reqid, kind in enumerate(("asset", "positions", "orders", "trades"), start=31):
        state.begin_query(reqid=reqid, generation=3, kind=kind)
        state.record_query(reqid=reqid, data={}, error={}, last=True)
    assert state.snapshot()["reconciled"] is False
    assert state.snapshot()["query_errors"] == ["asset query returned no data"]


def test_unfinished_generation_is_explicitly_failed_before_next_cycle(tmp_path):
    state = BridgeState(
        account_ref="emt-sim-primary",
        snapshot_path=tmp_path / "snapshot.json",
        journal_path=tmp_path / "events.jsonl",
    )
    state.set_connected(True)
    state.begin_query(reqid=41, generation=4, kind="asset")
    assert state.generation_in_flight() is True

    state.fail_in_flight("previous reconciliation did not complete")

    assert state.generation_in_flight() is False
    assert state.snapshot()["reconciled"] is False
    assert state.snapshot()["query_errors"] == [
        "previous reconciliation did not complete",
    ]


def test_query_error_fails_closed_and_journal_whitelists_fields(tmp_path):
    state = BridgeState(
        account_ref="emt-sim-primary",
        snapshot_path=tmp_path / "snapshot.json",
        journal_path=tmp_path / "events.jsonl",
    )
    state.set_connected(True)
    for reqid, kind in enumerate(("asset", "positions", "orders", "trades"), start=21):
        state.begin_query(reqid=reqid, generation=2, kind=kind)
        data = {"total_asset": 1.0, "buying_power": 1.0} if kind == "asset" else {}
        error = {"error_id": 9001, "error_msg": "do not persist this"} if kind == "orders" else {}
        state.record_query(reqid=reqid, data=data, error=error, last=True)

    assert state.snapshot()["reconciled"] is False
    assert state.snapshot()["query_errors"] == ["orders query error (9001)"]

    state.record_order_event(
        {"ticker": "000001", "order_emt_id": 7, "password": "forbidden"},
        {"error_id": 0, "error_msg": "ignored"},
    )
    event = json.loads(Path(tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert event["payload"]["data"] == {"ticker": "000001", "order_emt_id": 7}
    assert "password" not in json.dumps(event)
