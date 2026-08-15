from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class BridgeConfigError(ValueError):
    pass


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise BridgeConfigError(f"missing required environment variable: {name}")
    return value


def _true_only(name: str) -> bool:
    value = os.environ.get(name, "true").strip().lower()
    if value not in {"1", "true", "yes", "on"}:
        raise BridgeConfigError(f"{name} must remain true")
    return True


@dataclass(frozen=True)
class BridgeConfig:
    sdk_dir: Path
    server_host: str
    server_port: int
    user: str
    password: str
    local_ip: str
    client_id: int
    account_ref: str
    snapshot_path: Path
    journal_path: Path
    log_dir: Path
    refresh_seconds: int
    mode: str = "simulation"
    read_only: bool = True
    sim_orders: bool = False
    order_inbox: Path = Path("/var/lib/stock-ai/data/emt-orders/inbox")
    order_outbox: Path = Path("/var/lib/stock-ai/data/emt-orders/outbox")

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        mode = os.environ.get("EMT_MODE", "simulation").strip().lower()
        if mode != "simulation":
            raise BridgeConfigError("EMT_MODE must remain simulation")
        _true_only("EMT_READ_ONLY")
        sim_orders_raw = os.environ.get("EMT_SIM_ORDERS", "false").strip().lower()
        sim_orders = sim_orders_raw in {"1", "true", "yes", "on"}
        if sim_orders and mode != "simulation":
            raise BridgeConfigError("EMT_SIM_ORDERS is only allowed in simulation")

        try:
            port = int(_required("EMT_SERVER_PORT"))
            client_id = int(os.environ.get("EMT_CLIENT_ID", "31"))
            refresh_seconds = int(os.environ.get("EMT_REFRESH_SECONDS", "30"))
        except ValueError as exc:
            raise BridgeConfigError("EMT numeric settings are invalid") from exc
        if not 1 <= port <= 65535:
            raise BridgeConfigError("EMT_SERVER_PORT is out of range")
        if not 1 <= client_id <= 127:
            raise BridgeConfigError("EMT_CLIENT_ID must be between 1 and 127")
        if refresh_seconds < 10:
            raise BridgeConfigError("EMT_REFRESH_SECONDS must be at least 10")

        account_ref = os.environ.get("EMT_ACCOUNT_REF", "emt-sim-primary").strip()
        if not account_ref or len(account_ref) > 64:
            raise BridgeConfigError("EMT_ACCOUNT_REF must be a short opaque label")

        return cls(
            sdk_dir=Path(_required("EMT_SDK_DIR")),
            server_host=_required("EMT_SERVER_HOST"),
            server_port=port,
            user=_required("EMT_USER"),
            password=_required("EMT_PASSWORD"),
            local_ip=_required("EMT_LOCAL_IP"),
            client_id=client_id,
            account_ref=account_ref,
            snapshot_path=Path(os.environ.get(
                "EMT_SNAPSHOT_PATH", "/var/lib/stock-ai/broker/emt_snapshot.json")),
            journal_path=Path(os.environ.get(
                "EMT_JOURNAL_PATH", "/var/lib/stock-ai/broker/emt_events.jsonl")),
            log_dir=Path(os.environ.get(
                "EMT_LOG_DIR", "/var/lib/stock-ai/broker/vendor-logs")),
            refresh_seconds=refresh_seconds,
            mode=mode,
            read_only=True,
            sim_orders=sim_orders,
            order_inbox=Path(os.environ.get(
                "EMT_ORDER_INBOX", "/var/lib/stock-ai/data/emt-orders/inbox")),
            order_outbox=Path(os.environ.get(
                "EMT_ORDER_OUTBOX", "/var/lib/stock-ai/data/emt-orders/outbox")),
        )

