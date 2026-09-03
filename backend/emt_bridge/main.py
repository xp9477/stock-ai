from __future__ import annotations

import ctypes
import importlib
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from .config import BridgeConfig, BridgeConfigError
from .orders import process_inbox
from .state import BridgeState


LOG = logging.getLogger("emt_bridge")


def ensure_supported_python(version: tuple[int, int] | None = None) -> None:
    """Refuse runtimes outside the native wrapper range before importing it.

    EMT v2.27.0's wrapper crashed during an import smoke test on CPython 3.14.
    The bridge is therefore pinned conservatively; Python 3.10 is the target
    runtime for deployment and 3.8-3.12 remain eligible for an import probe.
    """

    current = version or (sys.version_info.major, sys.version_info.minor)
    if current < (3, 8) or current > (3, 12):
        raise RuntimeError(
            "EMT v2.27.0 native bridge requires CPython 3.8-3.12; use 3.10"
        )


def load_vendor_module(sdk_dir: Path):
    ensure_supported_python()
    sdk_dir = sdk_dir.resolve()
    core = sdk_dir / "libemt_api.so"
    wrapper = sdk_dir / "vnemttrader.so"
    if not core.is_file() or not wrapper.is_file():
        raise RuntimeError("EMT Linux SDK files are missing")
    ctypes.CDLL(str(core), mode=ctypes.RTLD_GLOBAL)
    sys.path.insert(0, str(sdk_dir))
    return importlib.import_module("vnemttrader")


def prepare_vendor_workdir(log_dir: Path) -> Path:
    """Give the native SDK a writable cwd (it writes ``lshw.txt`` here).

    systemd runs this unit with ProtectSystem=strict and a read-only bind of
    the source tree, so leaving cwd at the code mount makes login fail with
    ``cannot create lshw.txt: Read-only file system``.
    """

    log_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(log_dir)
    return log_dir


def _callback(name: str):
    """Keep Boost.Python from seeing a Python exception as a dead interpreter."""

    def decorator(fn):
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:  # noqa: BLE001
                LOG.exception("EMT callback %s failed", name)
        return wrapped
    return decorator


def build_api(vendor, state: BridgeState, disconnected: threading.Event | None = None):
    class ReadOnlyTraderApi(vendor.TraderApi):
        @_callback("onConnected")
        def onConnected(self):
            # EMT v2.27 wrapper: onConnected(). Missing this is
            # ``TypeError: 'NoneType' object is not callable`` during login.
            state.set_connected(True)

        @_callback("onDisconnected")
        def onDisconnected(self, *args):
            # Wrapper signature is onDisconnected(session, reason); older
            # bindings may pass only reason. Use the last integer as the code.
            reason = args[-1] if args else 0
            state.set_connected(False, reason_code=int(reason or 0))
            if disconnected is not None:
                disconnected.set()

        @_callback("onError")
        def onError(self, error):
            state.record_error(error)

        @_callback("onOrderEvent")
        def onOrderEvent(self, data, error, _session):
            state.record_order_event(data, error)

        @_callback("onTradeEvent")
        def onTradeEvent(self, data, _session):
            state.record_trade_event(data)

        @_callback("onHoldingChangeEvent")
        def onHoldingChangeEvent(self, data, _session):
            row = data if isinstance(data, dict) else {}
            state.append_event("holding_change", {
                "ticker": row.get("ticker"),
                "market": row.get("market"),
                "total_qty": row.get("total_qty"),
            })

        @_callback("onQueryAsset")
        def onQueryAsset(self, data, error, reqid, last, _session):
            state.record_query(reqid=reqid, data=data, error=error, last=bool(last))

        @_callback("onQueryPosition")
        def onQueryPosition(self, data, error, reqid, last, _session):
            state.record_query(reqid=reqid, data=data, error=error, last=bool(last))

        @_callback("onQueryOrder")
        def onQueryOrder(self, data, error, reqid, last, _session):
            state.record_query(reqid=reqid, data=data, error=error, last=bool(last))

        @_callback("onQueryTrade")
        def onQueryTrade(self, data, error, reqid, last, _session):
            state.record_query(reqid=reqid, data=data, error=error, last=bool(last))

    return ReadOnlyTraderApi()


def issue_reconciliation(api, state: BridgeState, session: int, generation: int) -> None:
    calls = (
        ("asset", lambda reqid: api.queryAsset(session, reqid)),
        ("positions", lambda reqid: api.queryPosition("", session, reqid)),
        ("orders", lambda reqid: api.queryOrders(
            {"ticker": "", "begin_time": 0, "end_time": 0}, session, reqid)),
        ("trades", lambda reqid: api.queryTrades(
            {"ticker": "", "begin_time": 0, "end_time": 0}, session, reqid)),
    )
    for offset, (kind, call) in enumerate(calls, start=1):
        reqid = generation * 10 + offset
        state.begin_query(reqid=reqid, generation=generation, kind=kind)
        return_code = int(call(reqid))
        if return_code != 0:
            state.query_send_failed(reqid=reqid, return_code=return_code)


def run(config: BridgeConfig) -> None:
    vendor = load_vendor_module(config.sdk_dir)
    state = BridgeState(
        account_ref=config.account_ref,
        snapshot_path=config.snapshot_path,
        journal_path=config.journal_path,
    )
    prepare_vendor_workdir(config.log_dir)
    disconnected = threading.Event()
    api = build_api(vendor, state, disconnected)
    api.createTraderApi(config.client_id, str(config.log_dir), 4)
    api.subscribePublicTopic(0)
    api.setSoftwareVersion("stock-ai-ro-1")
    session = int(api.login(
        config.server_host,
        config.server_port,
        config.user,
        config.password,
        1,
        config.local_ip,
    ))
    if session == 0:
        state.set_connected(False, reason_code=-1)
        raise RuntimeError("EMT login failed; inspect the vendor log locally")

    state.set_connected(True)
    stop = threading.Event()

    def request_stop(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    generation = 1
    try:
        while not stop.is_set() and not disconnected.is_set():
            try:
                if state.generation_in_flight():
                    state.fail_in_flight("previous reconciliation did not complete")
                if config.sim_orders:
                    try:
                        process_inbox(
                            api=api, session=session,
                            inbox=config.order_inbox, outbox=config.order_outbox,
                        )
                    except Exception:  # noqa: BLE001
                        LOG.exception("EMT sim inbox processing failed")
                issue_reconciliation(api, state, session, generation)
                generation += 1
            except Exception:  # noqa: BLE001
                LOG.exception("EMT reconciliation cycle failed")
            for _ in range(config.refresh_seconds):
                if config.sim_orders:
                    try:
                        process_inbox(
                            api=api, session=session,
                            inbox=config.order_inbox, outbox=config.order_outbox,
                        )
                    except Exception:  # noqa: BLE001
                        LOG.exception("EMT sim inbox processing failed")
                if stop.wait(1) or disconnected.is_set():
                    break
        if disconnected.is_set() and not stop.is_set():
            raise RuntimeError("EMT connection was lost; service restart required")
    finally:
        try:
            state.set_connected(False, reason_code=0)
        except Exception:  # noqa: BLE001
            LOG.exception("failed to publish disconnected snapshot")
        try:
            api.logout(session)
        finally:
            api.release()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = BridgeConfig.from_env()
        run(config)
    except (BridgeConfigError, RuntimeError) as exc:
        # Never interpolate vendor config or credential-bearing objects here.
        LOG.error("EMT read-only bridge stopped: %s", str(exc))
        return 1
    except Exception:  # noqa: BLE001
        LOG.exception("EMT read-only bridge crashed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
