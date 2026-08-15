"""Submit simulation orders to the isolated EMT bridge via a local inbox."""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings


LOG = logging.getLogger(__name__)


def inbox_ready() -> bool:
    return bool(settings.broker_reference_required) and bool(
        str(settings.broker_order_inbox or "").strip())


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def submit_simulation_order(
    *,
    side: str,
    code: str,
    quantity: int,
    price: float,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Drop a request for the host EMT bridge and wait for its result file."""
    inbox = Path(settings.broker_order_inbox)
    outbox = Path(settings.broker_order_outbox)
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    request_id = f"ord-{uuid.uuid4().hex}"
    payload = {
        "schema_version": 1,
        "request_id": request_id,
        "side": side,
        "code": code,
        "quantity": int(quantity),
        "price": float(price),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write(inbox / f"{request_id}.json", payload)
    deadline = time.monotonic() + float(
        timeout_seconds if timeout_seconds is not None
        else settings.broker_order_timeout_seconds
    )
    result_path = outbox / f"{request_id}.json"
    while time.monotonic() < deadline:
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.2)
                continue
            if isinstance(result, dict) and result.get("request_id") == request_id:
                return result
        time.sleep(0.25)
    return {
        "request_id": request_id,
        "accepted": False,
        "order_emt_id": 0,
        "error": "bridge_timeout",
    }
