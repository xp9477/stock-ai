"""Read-only, fail-closed view of an external broker reconciliation snapshot.

The web process deliberately does not load a broker's native SDK.  A separate
bridge owns that SDK and atomically publishes a normalized JSON snapshot.  This
module validates that file before any broker facts are shown to the rest of the
application.
"""

from __future__ import annotations

import json
import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
_ACCOUNT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$")
_FORBIDDEN_KEYS = {
    "account_id",
    "account_number",
    "email",
    "password",
    "phone",
    "secret",
    "token",
    "user",
    "username",
}


class BrokerSnapshotError(ValueError):
    """The external snapshot is missing, unsafe, stale, or malformed."""


@dataclass(frozen=True)
class BrokerSnapshot:
    provider: str
    mode: str
    read_only: bool
    connected: bool
    reconciled: bool
    account_ref: str
    observed_at: datetime
    asset: dict[str, Any]
    positions: tuple[dict[str, Any], ...]
    orders: tuple[dict[str, Any], ...]
    trades: tuple[dict[str, Any], ...]
    query_errors: tuple[str, ...]


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise BrokerSnapshotError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BrokerSnapshotError(f"{field} is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BrokerSnapshotError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _reject_secrets(value: object, path: str = "snapshot") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS:
                raise BrokerSnapshotError(f"forbidden credential/account field at {path}.{key}")
            _reject_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secrets(child, f"{path}[{index}]")


def _require_bool(data: dict[str, Any], field: str) -> bool:
    value = data.get(field)
    if not isinstance(value, bool):
        raise BrokerSnapshotError(f"{field} must be boolean")
    return value


def _require_records(data: dict[str, Any], field: str) -> tuple[dict[str, Any], ...]:
    value = data.get(field)
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise BrokerSnapshotError(f"{field} must be a list of objects")
    return tuple(value)


def _validate_asset(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BrokerSnapshotError("asset must be an object")
    for field in ("total_asset", "buying_power"):
        number = value.get(field)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise BrokerSnapshotError(f"asset.{field} must be numeric")
        if not math.isfinite(float(number)) or float(number) < 0:
            raise BrokerSnapshotError(f"asset.{field} must be finite and non-negative")
    return value


def load_broker_snapshot(
    path: str | Path,
    *,
    max_age_seconds: int = 60,
    now: datetime | None = None,
) -> BrokerSnapshot:
    """Load a complete simulation snapshot or fail closed.

    A snapshot from a live account is intentionally rejected by this adapter.
    Enabling real funds is a separate future authorization, not a config flip.
    """

    snapshot_path = Path(path)
    try:
        raw_text = snapshot_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BrokerSnapshotError(f"snapshot unavailable: {snapshot_path}") from exc
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise BrokerSnapshotError("snapshot is not valid JSON") from exc
    if not isinstance(data, dict):
        raise BrokerSnapshotError("snapshot root must be an object")

    _reject_secrets(data)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise BrokerSnapshotError("unsupported broker snapshot schema")
    if data.get("provider") != "emt":
        raise BrokerSnapshotError("unexpected broker provider")
    if data.get("mode") != "simulation":
        raise BrokerSnapshotError("only an EMT simulation account is authorized")
    if _require_bool(data, "read_only") is not True:
        raise BrokerSnapshotError("bridge must be read-only")

    account_ref = data.get("account_ref")
    if not isinstance(account_ref, str) or not _ACCOUNT_REF_RE.fullmatch(account_ref):
        raise BrokerSnapshotError("account_ref must be an opaque local label")

    observed_at = _parse_timestamp(data.get("observed_at"), "observed_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - observed_at).total_seconds()
    if age < -5:
        raise BrokerSnapshotError("snapshot timestamp is in the future")
    if max_age_seconds <= 0 or age > max_age_seconds:
        raise BrokerSnapshotError("snapshot is stale")

    errors = data.get("query_errors", [])
    if not isinstance(errors, list) or not all(isinstance(item, str) for item in errors):
        raise BrokerSnapshotError("query_errors must be a list of strings")

    return BrokerSnapshot(
        provider="emt",
        mode="simulation",
        read_only=True,
        connected=_require_bool(data, "connected"),
        reconciled=_require_bool(data, "reconciled"),
        account_ref=account_ref,
        observed_at=observed_at,
        asset=_validate_asset(data.get("asset")),
        positions=_require_records(data, "positions"),
        orders=_require_records(data, "orders"),
        trades=_require_records(data, "trades"),
        query_errors=tuple(errors),
    )


def broker_snapshot_status(
    path: str | Path,
    *,
    max_age_seconds: int = 60,
    max_total_asset: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a non-sensitive readiness summary for ``/api/status``."""

    try:
        snapshot = load_broker_snapshot(path, max_age_seconds=max_age_seconds, now=now)
        open_positions = normalized_positions(snapshot)
    except BrokerSnapshotError as exc:
        return {
            "provider": "emt",
            "mode": "simulation",
            "configured": False,
            "connected": False,
            "reconciled": False,
            "reference_ready": False,
            "reason": str(exc),
        }
    ready = snapshot.connected and snapshot.reconciled and not snapshot.query_errors
    reason = "" if ready else "broker snapshot is incomplete or has query errors"
    if max_total_asset is not None:
        if not math.isfinite(float(max_total_asset)) or float(max_total_asset) <= 0:
            raise ValueError("max_total_asset must be finite and positive")
        if float(snapshot.asset["total_asset"]) > float(max_total_asset):
            ready = False
            reason = "broker total asset exceeds configured capital boundary"
    return {
        "provider": snapshot.provider,
        "mode": snapshot.mode,
        "configured": True,
        "connected": snapshot.connected,
        "reconciled": snapshot.reconciled,
        "reference_ready": ready,
        "account_ref": snapshot.account_ref,
        "observed_at": snapshot.observed_at.isoformat(),
        "position_count": len(open_positions),
        "order_count": len(snapshot.orders),
        "trade_count": len(snapshot.trades),
        "capital_boundary_ok": (
            max_total_asset is None
            or float(snapshot.asset["total_asset"]) <= float(max_total_asset)
        ),
        "reason": reason,
    }


def require_reference_snapshot(
    path: str | Path,
    *,
    max_age_seconds: int,
    max_total_asset: float,
    now: datetime | None = None,
) -> BrokerSnapshot:
    """Return a fresh, complete broker snapshot inside the capital boundary."""

    snapshot = load_broker_snapshot(
        path, max_age_seconds=max_age_seconds, now=now,
    )
    if not snapshot.connected or not snapshot.reconciled or snapshot.query_errors:
        raise BrokerSnapshotError("broker snapshot is incomplete or has query errors")
    if not math.isfinite(float(max_total_asset)) or float(max_total_asset) <= 0:
        raise ValueError("max_total_asset must be finite and positive")
    if float(snapshot.asset["total_asset"]) > float(max_total_asset):
        raise BrokerSnapshotError("broker total asset exceeds configured capital boundary")
    return snapshot


def _finite_number(value: object, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrokerSnapshotError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise BrokerSnapshotError(f"{field} is outside its allowed range")
    return number


def _whole_quantity(value: object, field: str) -> int:
    number = _finite_number(value, field)
    quantity = int(number)
    if abs(number - quantity) > 1e-9:
        raise BrokerSnapshotError(f"{field} must be a whole quantity")
    return quantity


def normalized_positions(snapshot: BrokerSnapshot) -> tuple[dict[str, Any], ...]:
    """Normalize EMT position rows without accepting ambiguous identities."""

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(snapshot.positions):
        code = str(raw.get("ticker") or raw.get("code") or "").strip()
        if not re.fullmatch(r"\d{5,6}", code):
            raise BrokerSnapshotError(f"positions[{index}].ticker is invalid")
        if code in seen:
            raise BrokerSnapshotError(f"duplicate broker position: {code}")
        seen.add(code)
        total_qty = _whole_quantity(
            raw.get("total_qty", 0), f"positions[{index}].total_qty",
        )
        available_qty = _whole_quantity(
            raw.get("sellable_qty", raw.get("available_qty", 0)),
            f"positions[{index}].sellable_qty",
        )
        if available_qty > total_qty:
            raise BrokerSnapshotError(
                f"positions[{index}].sellable_qty exceeds total_qty"
            )
        avg_cost = _finite_number(
            raw.get("avg_price", raw.get("avg_cost", 0.0)),
            f"positions[{index}].avg_price",
        )
        unrealized_pnl = _finite_number(
            raw.get("unrealized_pnl", 0.0),
            f"positions[{index}].unrealized_pnl",
            minimum=-math.inf,
        )
        if total_qty > 0 and avg_cost <= 0:
            raise BrokerSnapshotError(
                f"positions[{index}].avg_price must be positive for an open position"
            )
        # EMT 会返回 total_qty=0、ticker_name=not_ready 的占位行。
        # 它们不是持仓；投影后反而会制造行情请求、限流与超时。
        if total_qty == 0:
            continue
        rows.append({
            "code": code,
            "name": str(raw.get("ticker_name") or raw.get("name") or code).strip(),
            "total_qty": total_qty,
            "available_qty": available_qty,
            "avg_cost": avg_cost,
            "unrealized_pnl": unrealized_pnl,
        })
    return tuple(rows)


def snapshot_fingerprint(snapshot: BrokerSnapshot) -> str:
    payload = {
        "provider": snapshot.provider,
        "mode": snapshot.mode,
        "account_ref": snapshot.account_ref,
        "observed_at": snapshot.observed_at.isoformat(),
        "asset": snapshot.asset,
        "positions": snapshot.positions,
        "orders": snapshot.orders,
        "trades": snapshot.trades,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def reconcile_snapshot_projection(
    db,
    *,
    model_pk: int,
    snapshot: BrokerSnapshot,
    initial_equity: float,
) -> dict[str, Any]:
    """Project the dedicated broker account into mutable current-state rows.

    Orders, decisions, ledgers, and prior evidence remain untouched. Positions
    absent at the broker are zeroed rather than deleted so foreign-keyed audit
    history remains valid.
    """

    from ..models import Account, Model, Position

    model = db.get(Model, model_pk)
    if (
        model is None or model.type != "ensemble"
        or not model.enabled or not model.is_official_strategy
    ):
        raise BrokerSnapshotError("broker projection target is not the official strategy")
    account = db.query(Account).filter(Account.model_pk == model_pk).first()
    if account is None:
        raise BrokerSnapshotError("official strategy account is missing")
    baseline = _finite_number(initial_equity, "initial_equity")
    if baseline <= 0:
        raise BrokerSnapshotError("initial_equity must be positive")

    broker_rows = {row["code"]: row for row in normalized_positions(snapshot)}
    current = {
        row.code: row
        for row in db.query(Position).filter(Position.model_pk == model_pk).all()
    }
    observed_naive = snapshot.observed_at.replace(tzinfo=None)
    for code, position in current.items():
        if code not in broker_rows:
            position.total_qty = 0
            position.available_qty = 0
            position.updated_at = observed_naive
    for code, row in broker_rows.items():
        position = current.get(code)
        if position is None:
            position = Position(
                model_pk=model_pk,
                code=code,
                name=row["name"],
                total_qty=row["total_qty"],
                available_qty=row["available_qty"],
                avg_cost=row["avg_cost"],
                buy_reason="EMT simulation broker reconciliation",
                updated_at=observed_naive,
            )
            db.add(position)
        else:
            position.name = row["name"]
            position.total_qty = row["total_qty"]
            position.available_qty = row["available_qty"]
            position.avg_cost = row["avg_cost"]
            position.updated_at = observed_naive

    account.cash = float(snapshot.asset["buying_power"])
    account.initial_cash = baseline
    db.flush()
    return {
        "provider": snapshot.provider,
        "mode": snapshot.mode,
        "observed_at": snapshot.observed_at.isoformat(),
        "snapshot_fingerprint": snapshot_fingerprint(snapshot),
        "total_equity": float(snapshot.asset["total_asset"]),
        "buying_power": float(snapshot.asset["buying_power"]),
        "security_asset": float(snapshot.asset.get("security_asset", 0.0) or 0.0),
        "position_count": sum(1 for row in broker_rows.values() if row["total_qty"] > 0),
    }


def reconcile_configured_broker_portfolio(db, model_pks: list[int]) -> dict[str, Any] | None:
    """Use the broker as source of truth when deployment requires it."""

    from ..config import settings

    if not settings.broker_reference_required:
        return None
    if len(model_pks) != 1:
        raise BrokerSnapshotError("broker reference requires one official strategy")
    snapshot = require_reference_snapshot(
        settings.broker_snapshot_path,
        max_age_seconds=settings.broker_snapshot_max_age_seconds,
        max_total_asset=settings.broker_snapshot_max_total_asset,
    )
    return reconcile_snapshot_projection(
        db,
        model_pk=model_pks[0],
        snapshot=snapshot,
        initial_equity=settings.broker_snapshot_initial_equity,
    )
