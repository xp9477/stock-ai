import pytest
from fastapi import HTTPException

from app.api.routes import reset_account
from app.main import app
from app.models import Run


def test_split_domain_routers_are_registered():
    # FastAPI 0.116+ may keep nested routers as a lazy _IncludedRouter object,
    # so the generated contract is the stable way to assert public routes.
    paths = set(app.openapi()["paths"])
    assert "/api/research/hypotheses" in paths
    assert "/api/research/grid/run" in paths
    assert "/api/backtest/run" in paths
    assert "/api/factors/snapshot" in paths
    assert "/api/ledger/stats" in paths


def test_destructive_account_reset_is_disabled_and_evidence_survives(db):
    run = Run(trigger="manual", status="done")
    db.add(run)
    db.commit()

    with pytest.raises(HTTPException) as caught:
        reset_account(db)

    assert caught.value.status_code == 409
    assert caught.value.detail["status"] == "immutable_evidence"
    assert db.get(Run, run.id) is not None
