# Ubuntu deployment boundary

The deployment has two isolated processes:

1. `stock-ai` runs FastAPI/Vue in Docker and binds to `127.0.0.1:8000`.
2. `stock-ai-emt-bridge` runs directly on the Ubuntu host because EMT uses a
   native Linux SDK and host identity/network information.  It has no order or
   cancel methods.  It publishes only a normalized simulation snapshot and an
   append-only event journal under `/var/lib/stock-ai/broker`.

Broker credentials belong only in `/etc/stock-ai/emt-bridge.env`, mode `0600`.
They must never be added to the project `.env`, SQLite database, logs, or Git.
The web container mounts the broker directory read-only.

Set `BROKER_REFERENCE_REQUIRED=true` for the Ubuntu deployment.  In that mode
the official strategy's mutable Account/Position rows are a projection of the
fresh EMT simulation snapshot before each decision run and final ticket
approval.  A missing, stale, incomplete, or over-boundary snapshot stops the
workflow before the LLM or capital authorization can use stale internal state.

The host bridge runs as the dedicated `stock-ai` user.  Keep
`/var/lib/stock-ai/broker` owned by that user with mode `0700`, and allow
execute-only traversal on its parent (`chmod 0711 /var/lib/stock-ai`).  The
database directory and database files remain independently protected.

The application remains bound to localhost because it has no login system.
Access it from another machine with an SSH tunnel:

```text
ssh -L 8000:127.0.0.1:8000 <ubuntu-user>@192.168.0.25
```

Set `HOST_PORT=18000` if port 8000 is already occupied, then tunnel with
`ssh -L 18000:127.0.0.1:18000 ...` and open `http://127.0.0.1:18000`.
Do not change `BIND_ADDRESS` to `0.0.0.0`
until an authenticated reverse proxy has been deployed.

The EMT v2.27.0 archives are not vendored.  The official Python archive must
be MD5-verified before its `emt_api_python/lib/linux` directory is installed at
`/opt/emt-api/python/lib/linux`.  Expected MD5 for the archive downloaded on
2026-08-12: `ec40d2189aede70c315b0ed38cd61cb3`.

Run the bridge with a CPython version that has passed an import smoke test on
the target host.  EMT v2.27.0 imported successfully with Ubuntu 24.04's
CPython 3.12 on the target deployment.  The wrapper is rejected outside
CPython 3.8-3.12; an actual import smoke test crashed on CPython 3.14.

`/api/status` reports `broker_sync.reference_ready=true` only after a fresh,
complete funds/positions/orders/trades reconciliation.  Missing, stale, live,
writable, disconnected, partially queried, or over-capital-boundary snapshots
fail closed.  The default broker total-asset boundary is CNY 400,000.
