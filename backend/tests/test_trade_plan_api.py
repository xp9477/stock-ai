"""HTTP contracts for the conditional trade-plan workflow."""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.trade_plan_routes import (
    execution_intent_router,
    trade_plan_router,
)
from app.database import Base, get_db
from app.models import (Account, Decision, ExecutionIntent, GateCheck, Model, Order,
                        Position, Run)
from app.trading.trade_plans import create_plan_from_decision


UTC = timezone.utc


@pytest.fixture()
def api_env():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    setup_db = TestSession()
    model = Model(
        name="API策略", model_id="", type="ensemble",
        is_official_strategy=True,
    )
    setup_db.add(model)
    setup_db.flush()
    setup_db.add(Account(
        model_pk=model.id, cash=100_000, initial_cash=100_000))
    setup_db.commit()

    app = FastAPI()
    app.include_router(trade_plan_router, prefix="/api")
    app.include_router(execution_intent_router, prefix="/api")

    def override_db():
        request_db = TestSession()
        try:
            yield request_db
        finally:
            request_db.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, setup_db, model
    setup_db.close()
    engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


def _bars():
    today = date.today()
    return [{
        "date": today - timedelta(days=80 - i),
        "open": 100.5,
        "prev_close": 100.0,
        "close": 100.0,
        "completed": True,
    } for i in range(60)]


def _quote(price: float = 101.0):
    timestamp = _now() - timedelta(seconds=1)
    return {
        "code": "600519",
        "price": price,
        "open": 100.5,
        "prev_close": 100.0,
        "quote_asof": timestamp.isoformat(),
        "received_at": timestamp.isoformat(),
        "tradable": True,
        "source": "test-double-source",
    }


def _news(fingerprint: str = "baseline", *, rss_ok: bool = True):
    return {
        "fingerprint": fingerprint,
        "rss_coverage_ok": rss_ok,
        "official_coverage": False,
        "source_results": [{"id": "rss", "ok": rss_ok}],
        "items": [],
    }


def _plan(db, model_a):
    current = _now()
    run = Run(trigger="schedule")
    db.add(run)
    db.flush()
    decision = Decision(
        run_id=run.id,
        model_pk=model_a.id,
        code="600519",
        name="测试股",
        action="buy",
        target_position_pct=0.2,
        confidence=0.8,
        reason="冻结事实形成的候选计划",
    )
    db.add(decision)
    db.flush()
    plan = create_plan_from_decision(
        db,
        decision,
        reference_price=100.0,
        max_buy_price=103.0,
        data_cutoff_at=current - timedelta(hours=12),
        valid_from_at=current - timedelta(minutes=5),
        expires_at=current + timedelta(hours=1),
        invalidation_conditions={"material_news": "review_required"},
        policy_snapshot={
            "news_fingerprint": "baseline",
            "gap_lookback_days": 60,
            "gap_percentile": 0.95,
            "gap_min_samples": 40,
            "hard_price_deviation_pct": 0.05,
        },
    )
    db.commit()
    return plan


def test_plan_list_detail_and_reject(api_env):
    api_client, db, model_a = api_env
    plan = _plan(db, model_a)

    listed = api_client.get("/api/trade-plans", params={"status": "candidate"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [plan.id]

    detail = api_client.get(f"/api/trade-plans/{plan.id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "candidate"
    assert detail.json()["gates"] == []

    rejected = api_client.post(
        f"/api/trade-plans/{plan.id}/reject",
        json={"expected_lock_version": plan.lock_version, "reason": "人工放弃"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["status_reason"] == "人工放弃"


def test_information_refresh_is_fail_closed(api_env):
    api_client, db, model_a = api_env
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ):
        response = api_client.post(
            f"/api/trade-plans/{plan.id}/refresh-information",
            json={
                "expected_lock_version": plan.lock_version,
                "human_official_confirmed": False,
                "idempotency_key": "refresh-information-1",
            },
        )
    assert response.status_code == 200
    assert response.json()["gate"]["outcome"] == "blocked_information"
    assert response.json()["plan"]["status"] == "blocked_information"


def test_price_validation_uses_server_quote_and_records_gate(api_env):
    api_client, db, model_a = api_env
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ):
        information = api_client.post(
            f"/api/trade-plans/{plan.id}/refresh-information",
            json={
                "expected_lock_version": plan.lock_version,
                "human_official_confirmed": True,
                "idempotency_key": "price-info-gate-1",
            },
        )
    assert information.status_code == 200
    assert information.json()["gate"]["outcome"] == "pass"
    current_lock = information.json()["plan"]["lock_version"]
    with patch(
        "app.trading.plan_service.market.get_execution_quote",
        return_value=_quote(),
    ) as quote_fetch, patch(
        "app.trading.plan_service.market.get_daily_kline",
        return_value=_bars(),
    ):
        response = api_client.post(
            f"/api/trade-plans/{plan.id}/validate-price",
            json={
                "expected_lock_version": current_lock,
                "idempotency_key": "validate-price-1",
            },
        )
    assert response.status_code == 200
    assert response.json()["evaluation"]["outcome"] == "pass"
    assert response.json()["plan"]["status"] == "awaiting_approval"
    assert db.query(GateCheck).count() == 2
    quote_fetch.assert_called_once_with(
        "600519", force_refresh=True, require_session=True)


def test_price_validation_cannot_bypass_information_gate(api_env):
    api_client, db, model_a = api_env
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.market.get_execution_quote",
    ) as quote_fetch:
        response = api_client.post(
            f"/api/trade-plans/{plan.id}/validate-price",
            json={
                "expected_lock_version": plan.lock_version,
                "idempotency_key": "price-before-information",
            },
        )
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "blocked_information"
    quote_fetch.assert_not_called()


def test_gate_idempotency_key_cannot_cross_plans(api_env):
    api_client, db, model_a = api_env
    first = _plan(db, model_a)
    second = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ):
        accepted = api_client.post(
            f"/api/trade-plans/{first.id}/refresh-information",
            json={
                "expected_lock_version": first.lock_version,
                "human_official_confirmed": True,
                "idempotency_key": "shared-gate-request-key",
            },
        )
        conflict = api_client.post(
            f"/api/trade-plans/{second.id}/refresh-information",
            json={
                "expected_lock_version": second.lock_version,
                "human_official_confirmed": True,
                "idempotency_key": "shared-gate-request-key",
            },
        )
    assert accepted.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["status"] == "idempotency_conflict"


def test_information_gate_rejects_stale_lock_version(api_env):
    api_client, db, model_a = api_env
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
    ) as news_fetch:
        response = api_client.post(
            f"/api/trade-plans/{plan.id}/refresh-information",
            json={
                "expected_lock_version": plan.lock_version + 1,
                "human_official_confirmed": True,
                "idempotency_key": "stale-information-lock",
            },
        )
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "version_conflict"
    news_fetch.assert_not_called()


def test_approval_requires_lock_version_and_idempotency_key(api_env):
    api_client, db, model_a = api_env
    plan = _plan(db, model_a)
    response = api_client.post(
        f"/api/trade-plans/{plan.id}/approve",
        json={"confirmed": True, "human_official_confirmed": True},
    )
    assert response.status_code == 422


def test_version_conflict_maps_plan_blocked_to_409(api_env):
    api_client, db, model_a = api_env
    plan = _plan(db, model_a)
    response = api_client.post(
        f"/api/trade-plans/{plan.id}/approve",
        json={
            "expected_lock_version": plan.lock_version + 1,
            "idempotency_key": "approve-version-conflict",
            "confirmed": True,
            "human_official_confirmed": True,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "version_conflict"


def test_approval_reruns_both_gates_and_only_creates_intent(api_env):
    api_client, db, model_a = api_env
    plan = _plan(db, model_a)
    payload = {
        "expected_lock_version": plan.lock_version,
        "idempotency_key": "approve-api-idempotent",
        "confirmed": True,
        "human_official_confirmed": True,
    }
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ) as news_fetch, patch(
        "app.trading.plan_service.market.get_execution_quote",
        return_value=_quote(),
    ) as quote_fetch, patch(
        "app.trading.plan_service.market.get_daily_kline",
        return_value=_bars(),
    ):
        first = api_client.post(f"/api/trade-plans/{plan.id}/approve", json=payload)
        second = api_client.post(f"/api/trade-plans/{plan.id}/approve", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["intent"]["id"] == second.json()["intent"]["id"]
    assert first.json()["plan"]["status"] == "ticket_ready"
    assert db.query(ExecutionIntent).count() == 1
    assert db.query(GateCheck).count() == 3
    assert db.query(Order).count() == 0
    assert db.query(Position).count() == 0
    assert first.json()["intent"]["authorized_qty"] == 100
    assert first.json()["intent"]["authorized_notional"] == 10_100
    assert first.json()["intent"]["risk_snapshot"]["canary_status"] == "active"
    news_fetch.assert_called_once()
    quote_fetch.assert_called_once_with(
        "600519", force_refresh=True, require_session=True)

    intents = api_client.get(
        "/api/execution-intents", params={"code": "600519"})
    assert intents.status_code == 200
    assert len(intents.json()["items"]) == 1
    assert intents.json()["items"][0]["status"] == "ticket_ready"


def test_hard_price_change_blocks_approval_with_409(api_env):
    api_client, db, model_a = api_env
    plan = _plan(db, model_a)
    with patch(
        "app.trading.plan_service.news_rss.stock_news_gate_snapshot",
        return_value=_news(),
    ), patch(
        "app.trading.plan_service.market.get_execution_quote",
        return_value=_quote(105.0),
    ), patch(
        "app.trading.plan_service.market.get_daily_kline",
        return_value=_bars(),
    ):
        response = api_client.post(
            f"/api/trade-plans/{plan.id}/approve",
            json={
                "expected_lock_version": plan.lock_version,
                "idempotency_key": "approve-hard-price-block",
                "confirmed": True,
                "human_official_confirmed": True,
            },
        )
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "invalidated_price"
    assert db.query(ExecutionIntent).count() == 0
    assert db.query(Order).count() == 0


def test_trade_plan_routes_are_in_main_openapi():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/api/trade-plans" in paths
    assert "/api/trade-plans/{plan_id}" in paths
    assert "/api/trade-plans/{plan_id}/approve" in paths
    assert "/api/execution-intents" in paths
