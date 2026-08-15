"""Simulation-only EMT order tickets exchanged over a local inbox.

The web process never loads the vendor SDK.  It drops a JSON request; this
bridge, which already holds the simulation login, calls insertOrder.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOG = logging.getLogger("emt_bridge.orders")

# EMT v2.x / XTP-compatible integers. Snapshot rows use market=1 for 000001 (SZ).
EMT_MKT_SZ_A = 1
EMT_MKT_SH_A = 2
EMT_PRICE_LIMIT = 1
EMT_SIDE_BUY = 1
EMT_SIDE_SELL = 2
EMT_POSITION_EFFECT_INIT = 0
EMT_BUSINESS_TYPE_CASH = 0

_CODE_RE = re.compile(r"^\d{6}$")
_REQ_RE = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")


def market_for_code(code: str) -> int:
    if code.startswith(("5", "6", "9")):
        return EMT_MKT_SH_A
    if code.startswith(("0", "1", "2", "3")):
        return EMT_MKT_SZ_A
    raise ValueError(f"unsupported exchange for {code}")


def parse_order_request(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("order request must be an object")
    request_id = str(raw.get("request_id") or "").strip()
    if not _REQ_RE.fullmatch(request_id):
        raise ValueError("request_id is invalid")
    side = str(raw.get("side") or "").strip().lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    code = str(raw.get("code") or "").strip()
    if not _CODE_RE.fullmatch(code):
        raise ValueError("code must be a 6-digit A-share ticker")
    try:
        quantity = int(raw.get("quantity"))
        price = float(raw.get("price"))
    except (TypeError, ValueError) as exc:
        raise ValueError("quantity and price must be numeric") from exc
    if quantity <= 0 or quantity % 100 != 0:
        raise ValueError("quantity must be a positive round lot")
    if not (0 < price < 100_000):
        raise ValueError("price is out of range")
    return {
        "request_id": request_id,
        "side": side,
        "code": code,
        "quantity": quantity,
        "price": round(price, 3),
    }


def emt_order_payload(request: dict[str, Any], *, client_id: int) -> dict[str, Any]:
    return {
        "order_client_id": int(client_id),
        "ticker": request["code"],
        "market": market_for_code(request["code"]),
        "price": request["price"],
        "quantity": request["quantity"],
        "price_type": EMT_PRICE_LIMIT,
        "side": EMT_SIDE_BUY if request["side"] == "buy" else EMT_SIDE_SELL,
        "position_effect": EMT_POSITION_EFFECT_INIT,
        "business_type": EMT_BUSINESS_TYPE_CASH,
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def write_result(outbox: Path, payload: dict[str, Any]) -> None:
    request_id = payload["request_id"]
    _atomic_write(outbox / f"{request_id}.json", payload)


def process_inbox(*, api, session: int, inbox: Path, outbox: Path) -> int:
    """Submit every pending inbox request. Returns how many files were handled."""
    try:
        if not inbox.is_dir():
            return 0
    except OSError:
        return 0
    handled = 0
    for path in sorted(inbox.glob("*.json")):
        if path.name.endswith(".tmp.json"):
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            request = parse_order_request(raw)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            LOG.warning("rejecting invalid EMT order file %s: %s", path.name, exc)
            write_result(outbox, {
                "request_id": path.stem,
                "accepted": False,
                "order_emt_id": 0,
                "error": "invalid_request",
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            })
            path.unlink(missing_ok=True)
            handled += 1
            continue
        client_id = abs(hash(request["request_id"])) % 1_000_000_000 or 1
        try:
            order_id = int(api.insertOrder(emt_order_payload(request, client_id=client_id), session))
        except Exception as exc:  # noqa: BLE001
            LOG.exception("insertOrder raised for %s", request["request_id"])
            write_result(outbox, {
                "request_id": request["request_id"],
                "accepted": False,
                "order_emt_id": 0,
                "error": f"insert_exception:{type(exc).__name__}",
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            })
            path.unlink(missing_ok=True)
            handled += 1
            continue
        accepted = order_id != 0
        write_result(outbox, {
            "request_id": request["request_id"],
            "accepted": accepted,
            "order_emt_id": order_id,
            "error": "" if accepted else "insertOrder returned 0",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        })
        LOG.info(
            "EMT sim order %s %s %s x%s @ %s -> %s",
            request["request_id"], request["side"], request["code"],
            request["quantity"], request["price"],
            order_id if accepted else "rejected",
        )
        path.unlink(missing_ok=True)
        handled += 1
    return handled
