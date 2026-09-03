"""EMT order-channel compatibility boundary.

Automatic submission is deliberately safety-paused.  The previous file inbox
could not prove exactly-once delivery: a web timeout left a request that the
bridge could submit later, and a bridge crash could submit it twice.
"""
from __future__ import annotations

import logging
from typing import Any


LOG = logging.getLogger(__name__)
ORDER_SUBMISSION_ENABLED = False


def inbox_ready() -> bool:
    return False


def submit_simulation_order(
    *,
    side: str,
    code: str,
    quantity: int,
    price: float,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Refuse submission until a durable broker-order ledger is implemented."""
    del side, code, quantity, price, timeout_seconds
    LOG.warning("EMT 自动报单安全冻结：未创建 inbox 请求")
    return {
        "request_id": "",
        "accepted": False,
        "order_emt_id": 0,
        "error": "automatic_execution_safety_paused",
    }
