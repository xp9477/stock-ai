from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ASSET_FIELDS = (
    "total_asset", "buying_power", "security_asset", "withholding_amount",
    "frozen_margin", "orig_banlance", "banlance", "captial_asset",
)
POSITION_FIELDS = (
    "ticker", "ticker_name", "market", "total_qty", "sellable_qty",
    "avg_price", "unrealized_pnl", "yesterday_position",
)
ORDER_FIELDS = (
    "order_emt_id", "order_client_id", "ticker", "market", "side",
    "price", "quantity", "qty_traded", "qty_left", "trade_amount",
    "order_status", "order_submit_status", "insert_time", "update_time",
    "cancel_time", "order_exch_id", "business_type",
)
TRADE_FIELDS = (
    "order_emt_id", "order_client_id", "ticker", "market", "exec_id",
    "price", "quantity", "trade_time", "trade_amount", "report_index",
    "order_exch_id", "trade_type", "side", "business_type",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def whitelist(data: object, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    return {field: _safe_scalar(data[field]) for field in fields if field in data}


def error_code(error: object) -> int:
    if not isinstance(error, dict):
        return 0
    try:
        return int(error.get("error_id", 0) or 0)
    except (TypeError, ValueError):
        return -1


@dataclass
class QueryBatch:
    generation: int
    kind: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class BridgeState:
    """Thread-safe accumulator for asynchronous EMT query callbacks."""

    REQUIRED_KINDS = frozenset({"asset", "positions", "orders", "trades"})
    FIELDS = {
        "asset": ASSET_FIELDS,
        "positions": POSITION_FIELDS,
        "orders": ORDER_FIELDS,
        "trades": TRADE_FIELDS,
    }

    def __init__(self, *, account_ref: str, snapshot_path: Path, journal_path: Path):
        self.account_ref = account_ref
        self.snapshot_path = snapshot_path
        self.journal_path = journal_path
        self._lock = threading.RLock()
        self._connected = False
        self._reconciled = False
        self._observed_at = utc_now()
        self._asset: dict[str, Any] = {"total_asset": 0.0, "buying_power": 0.0}
        self._positions: list[dict[str, Any]] = []
        self._orders: list[dict[str, Any]] = []
        self._trades: list[dict[str, Any]] = []
        self._query_errors: list[str] = ["initial reconciliation has not completed"]
        self._pending: dict[int, QueryBatch] = {}
        self._completed: dict[int, set[str]] = {}
        self._generation_errors: dict[int, list[str]] = {}

    def set_connected(self, connected: bool, *, reason_code: int = 0) -> None:
        with self._lock:
            self._connected = connected
            if not connected:
                self._reconciled = False
                self._query_errors = [f"broker disconnected ({reason_code})"]
            self._observed_at = utc_now()
        self.append_event("connection", {"connected": connected, "reason_code": reason_code})
        self.write_snapshot()

    def begin_query(self, *, reqid: int, generation: int, kind: str) -> None:
        if kind not in self.REQUIRED_KINDS:
            raise ValueError(f"unknown query kind: {kind}")
        new_generation = False
        with self._lock:
            if generation not in self._completed:
                new_generation = True
                self._reconciled = False
                self._query_errors = ["reconciliation in progress"]
            self._pending[reqid] = QueryBatch(generation=generation, kind=kind)
            self._completed.setdefault(generation, set())
            self._generation_errors.setdefault(generation, [])
        if new_generation:
            self.write_snapshot()

    def generation_in_flight(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def fail_in_flight(self, reason: str) -> None:
        with self._lock:
            self._pending.clear()
            self._reconciled = False
            self._query_errors = [reason]
            self._observed_at = utc_now()
        self.write_snapshot()

    def query_send_failed(self, *, reqid: int, return_code: int) -> None:
        with self._lock:
            batch = self._pending.pop(reqid, None)
            if batch is None:
                return
            message = f"{batch.kind} query send failed ({return_code})"
            self._generation_errors.setdefault(batch.generation, []).append(message)
            self._query_errors = [message]
            self._reconciled = False
            self._observed_at = utc_now()
        self.write_snapshot()

    def record_query(self, *, reqid: int, data: object, error: object, last: bool) -> None:
        should_write = False
        with self._lock:
            batch = self._pending.get(reqid)
            if batch is None:
                return
            row = whitelist(data, self.FIELDS[batch.kind])
            if row:
                batch.rows.append(row)
            code = error_code(error)
            if code:
                batch.errors.append(f"{batch.kind} query error ({code})")
            if not last:
                return

            self._pending.pop(reqid, None)
            if batch.kind == "asset":
                if batch.rows:
                    self._asset = batch.rows[-1]
            elif batch.kind == "positions":
                self._positions = batch.rows
            elif batch.kind == "orders":
                self._orders = batch.rows
            elif batch.kind == "trades":
                self._trades = batch.rows

            completed = self._completed.setdefault(batch.generation, set())
            completed.add(batch.kind)
            generation_errors = self._generation_errors.setdefault(batch.generation, [])
            generation_errors.extend(batch.errors)
            if batch.kind == "asset" and not batch.rows:
                generation_errors.append("asset query returned no data")
            self._query_errors = list(generation_errors)
            if completed == self.REQUIRED_KINDS:
                self._reconciled = self._connected and not generation_errors
                self._observed_at = utc_now()
                for generation in list(self._completed):
                    if generation < batch.generation:
                        self._completed.pop(generation, None)
                        self._generation_errors.pop(generation, None)
            should_write = True
        if should_write:
            self.write_snapshot()

    def record_order_event(self, data: object, error: object) -> None:
        row = whitelist(data, ORDER_FIELDS)
        code = error_code(error)
        self.append_event("order", {"data": row, "error_code": code})

    def record_trade_event(self, data: object) -> None:
        row = whitelist(data, TRADE_FIELDS)
        self.append_event("trade", {"data": row})

    def record_error(self, error: object) -> None:
        self.append_event("error", {"error_code": error_code(error)})

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": 1,
                "provider": "emt",
                "mode": "simulation",
                "read_only": True,
                "connected": self._connected,
                "reconciled": self._reconciled,
                "account_ref": self.account_ref,
                "observed_at": self._observed_at.isoformat(),
                "asset": dict(self._asset),
                "positions": list(self._positions),
                "orders": list(self._orders),
                "trades": list(self._trades),
                "query_errors": list(self._query_errors),
            }

    def write_snapshot(self) -> None:
        payload = self.snapshot()
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.snapshot_path.with_suffix(self.snapshot_path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, self.snapshot_path)

    def append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "schema_version": 1,
            "provider": "emt",
            "mode": "simulation",
            "account_ref": self.account_ref,
            "event_type": event_type,
            "received_at": utc_now().isoformat(),
            "payload": payload,
        }
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock, self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(self.journal_path, 0o600)
