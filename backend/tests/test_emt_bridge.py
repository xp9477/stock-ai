import json
import logging
import os
import threading
from pathlib import Path

import pytest

from emt_bridge.config import BridgeConfig, BridgeConfigError
from emt_bridge.main import build_api, ensure_supported_python, prepare_vendor_workdir
from emt_bridge.orders import market_for_code, process_inbox
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


class _Vendor:
    class TraderApi:
        pass


def _state(tmp_path) -> BridgeState:
    return BridgeState(
        account_ref="emt-sim-primary",
        snapshot_path=tmp_path / "snapshot.json",
        journal_path=tmp_path / "events.jsonl",
    )


def test_on_connected_is_implemented_and_marks_connected(tmp_path):
    state = _state(tmp_path)
    api = build_api(_Vendor, state)
    assert callable(getattr(api, "onConnected"))
    api.onConnected()
    snapshot = state.snapshot()
    assert snapshot["connected"] is True
    assert json.loads((tmp_path / "snapshot.json").read_text(encoding="utf-8"))["connected"] is True


def test_on_disconnected_accepts_session_and_reason(tmp_path):
    state = _state(tmp_path)
    disconnected = threading.Event()
    api = build_api(_Vendor, state, disconnected)
    state.set_connected(True)
    api.onDisconnected(99, 7)
    snapshot = state.snapshot()
    assert snapshot["connected"] is False
    assert snapshot["query_errors"] == ["broker disconnected (7)"]
    assert disconnected.is_set()


def test_on_disconnected_accepts_reason_only(tmp_path):
    state = _state(tmp_path)
    api = build_api(_Vendor, state)
    state.set_connected(True)
    api.onDisconnected(3)
    assert state.snapshot()["query_errors"] == ["broker disconnected (3)"]


def test_callback_exception_is_swallowed(tmp_path, monkeypatch, caplog):
    state = _state(tmp_path)
    api = build_api(_Vendor, state)
    caplog.set_level(logging.ERROR)

    def boom(*_args, **_kwargs):
        raise RuntimeError("callback boom")

    monkeypatch.setattr(state, "set_connected", boom)
    api.onConnected()
    assert "EMT callback onConnected failed" in caplog.text


def test_concurrent_snapshot_writes_do_not_raise(tmp_path):
    state = _state(tmp_path)
    errors: list[BaseException] = []

    def worker(seed: int) -> None:
        try:
            for i in range(40):
                state.set_connected(bool((seed + i) % 2))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    payload = json.loads((tmp_path / "snapshot.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    leftover = list(tmp_path.glob("snapshot.json.*.tmp"))
    assert leftover == []


def test_snapshot_write_failure_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    state = _state(tmp_path)
    caplog.set_level(logging.ERROR)

    def boom(*_args, **_kwargs):
        raise FileNotFoundError("emt_snapshot.json.tmp")

    monkeypatch.setattr("emt_bridge.state.os.replace", boom)
    state.write_snapshot()
    assert "failed to write broker snapshot" in caplog.text
    assert not (tmp_path / "snapshot.json").exists()
    leftover = list(tmp_path.glob("snapshot.json.*.tmp"))
    assert leftover == []


def test_prepare_vendor_workdir_is_writable(tmp_path):
    original = Path.cwd()
    target = tmp_path / "vendor-logs"
    try:
        prepare_vendor_workdir(target)
        assert Path.cwd() == target.resolve()
        probe = target / "lshw.txt"
        probe.write_text("ok", encoding="utf-8")
        assert probe.read_text(encoding="utf-8") == "ok"
    finally:
        os.chdir(original)


def test_market_for_code_matches_snapshot_convention():
    assert market_for_code("000001") == 1
    assert market_for_code("300001") == 1
    assert market_for_code("600000") == 2
    assert market_for_code("688001") == 2


def test_process_inbox_rejects_stale_order_during_safety_pause(tmp_path):
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    inbox.mkdir()
    request_id = "ord-testorder1"
    (inbox / f"{request_id}.json").write_text(json.dumps({
        "request_id": request_id,
        "side": "buy",
        "code": "600000",
        "quantity": 100,
        "price": 10.5,
    }), encoding="utf-8")
    class Api:
        def insertOrder(self, payload, session):
            raise AssertionError("safety-paused bridge must not call insertOrder")

    handled = process_inbox(api=Api(), session=9, inbox=inbox, outbox=outbox)
    assert handled == 1
    result = json.loads((outbox / f"{request_id}.json").read_text(encoding="utf-8"))
    assert result["accepted"] is False
    assert result["order_emt_id"] == 0
    assert result["error"] == "automatic_execution_safety_paused"
    assert not (inbox / f"{request_id}.json").exists()
