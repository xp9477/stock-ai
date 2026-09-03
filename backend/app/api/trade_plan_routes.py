"""REST API for conditional trade plans and human-approved order tickets."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ExecutionIntent, GateCheck, TradePlan
from ..trading import plan_service
from ..trading.trade_plans import IdempotencyConflict, PASS


trade_plan_router = APIRouter(prefix="/trade-plans", tags=["trade-plans"])
execution_intent_router = APIRouter(
    prefix="/execution-intents", tags=["trade-plans"])


class InformationRefreshRequest(BaseModel):
    expected_lock_version: int = Field(ge=1)
    human_official_confirmed: bool = False
    idempotency_key: str = Field(min_length=8, max_length=128)


class PriceValidationRequest(BaseModel):
    expected_lock_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128)


class PlanApprovalRequest(BaseModel):
    expected_lock_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128)
    confirmed: bool
    human_official_confirmed: bool


class PlanRejectionRequest(BaseModel):
    expected_lock_version: int = Field(ge=1)
    reason: str = Field(default="用户拒绝", max_length=500)


def _get_plan(db: Session, plan_id: int) -> TradePlan:
    plan = db.get(TradePlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="交易计划不存在")
    return plan


def _blocked(error: plan_service.PlanBlocked) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"status": error.status, "reason": error.reason},
    )


def _ensure_mutable(plan: TradePlan) -> None:
    if plan.status in plan_service.TERMINAL_STATUSES or plan.status == "ticket_ready":
        raise plan_service.PlanBlocked(
            plan.status, plan.status_reason or "计划已经结束，不能再次运行门禁")


def _ensure_lock_version(plan: TradePlan, expected_lock_version: int) -> None:
    if plan.lock_version != expected_lock_version:
        raise plan_service.PlanBlocked(
            "version_conflict", "计划已被更新，请刷新后重新操作")


def _ensure_information_gate_passed(db: Session, plan: TradePlan) -> None:
    latest = (
        db.query(GateCheck)
        .filter(
            GateCheck.plan_id == plan.id,
            GateCheck.plan_version == plan.version,
            GateCheck.gate_type == "preopen_information",
        )
        .order_by(GateCheck.id.desc())
        .first()
    )
    if latest is None or latest.outcome != PASS:
        raise plan_service.PlanBlocked(
            "blocked_information",
            "必须先完成并通过最新的盘前信息门禁，才能校验价格",
        )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _serialize_intent(intent: ExecutionIntent, plan: TradePlan | None) -> dict[str, Any]:
    try:
        risk_snapshot = json.loads(intent.risk_snapshot_json or "{}")
    except (TypeError, json.JSONDecodeError):
        risk_snapshot = {}
    if not isinstance(risk_snapshot, dict):
        risk_snapshot = {}
    return {
        "id": intent.id,
        "plan_id": intent.plan_id,
        "status": intent.status,
        "version": intent.version,
        "lock_version": intent.lock_version,
        "approved_by": intent.approved_by,
        "approved_at": _iso(intent.approved_at),
        "approval_quote_price": intent.approval_quote_price,
        "approval_quote_asof": _iso(intent.approval_quote_asof),
        "authorized_target_position_pct": intent.authorized_target_position_pct,
        "authorized_notional": intent.authorized_notional,
        "authorized_qty": intent.authorized_qty,
        "estimated_fee": intent.estimated_fee,
        "risk_snapshot": risk_snapshot,
        "expires_at": _iso(intent.expires_at),
        "order_id": intent.order_id,
        "created_at": _iso(intent.created_at),
        "updated_at": _iso(intent.updated_at),
        "plan": ({
            "code": plan.code,
            "name": plan.name,
            "side": plan.side,
            "target_position_pct": plan.target_position_pct,
            "max_buy_price": plan.max_buy_price,
        } if plan is not None else None),
    }


@trade_plan_router.get("")
def list_trade_plans(
    status: str | None = None,
    model_pk: int | None = None,
    code: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(TradePlan)
    if status:
        query = query.filter(TradePlan.status == status)
    if model_pk is not None:
        query = query.filter(TradePlan.model_pk == model_pk)
    if code:
        query = query.filter(TradePlan.code == code.strip())
    plans = query.order_by(TradePlan.id.desc()).limit(limit).all()
    return {"items": [plan_service.serialize_plan(plan) for plan in plans]}


@trade_plan_router.get("/{plan_id}")
def get_trade_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = _get_plan(db, plan_id)
    gates = (db.query(GateCheck).filter(GateCheck.plan_id == plan.id)
             .order_by(GateCheck.id).all())
    return plan_service.serialize_plan(plan, gates=gates)


@trade_plan_router.post("/{plan_id}/refresh-information")
def refresh_trade_plan_information(
    plan_id: int,
    body: InformationRefreshRequest,
    db: Session = Depends(get_db),
):
    plan = _get_plan(db, plan_id)
    try:
        _ensure_mutable(plan)
        _ensure_lock_version(plan, body.expected_lock_version)
        check = plan_service.review_preopen_information(
            db,
            plan,
            human_official_confirmed=body.human_official_confirmed,
            force_refresh=True,
            idempotency_key=body.idempotency_key,
            commit=True,
        )
    except plan_service.PlanBlocked as error:
        raise _blocked(error) from error
    except IdempotencyConflict as error:
        raise _blocked(plan_service.PlanBlocked(
            "idempotency_conflict", str(error))) from error
    return {
        "plan": plan_service.serialize_plan(plan),
        "gate": {
            "id": check.id,
            "gate_type": check.gate_type,
            "outcome": check.outcome,
            "reason_code": check.reason_code,
            "reason": check.reason,
        },
    }


@trade_plan_router.post("/{plan_id}/validate-price")
def validate_trade_plan_price(
    plan_id: int,
    body: PriceValidationRequest,
    db: Session = Depends(get_db),
):
    plan = _get_plan(db, plan_id)
    try:
        _ensure_mutable(plan)
        _ensure_lock_version(plan, body.expected_lock_version)
        _ensure_information_gate_passed(db, plan)
        check, evaluation, quote = plan_service.validate_plan_price(
            db,
            plan,
            require_session=True,
            idempotency_key=body.idempotency_key,
            commit=True,
        )
    except plan_service.PlanBlocked as error:
        raise _blocked(error) from error
    except IdempotencyConflict as error:
        raise _blocked(plan_service.PlanBlocked(
            "idempotency_conflict", str(error))) from error
    return {
        "plan": plan_service.serialize_plan(plan),
        "gate": {
            "id": check.id,
            "gate_type": check.gate_type,
            "outcome": check.outcome,
            "reason_code": check.reason_code,
            "reason": check.reason,
        },
        "evaluation": evaluation.to_dict(),
        "quote": ({
            "price": quote.get("price"),
            "open": quote.get("open"),
            "prev_close": quote.get("prev_close"),
            "quote_asof": quote.get("quote_asof"),
            "source": quote.get("source"),
            "tradable": quote.get("tradable"),
        } if quote else None),
    }


@trade_plan_router.post("/{plan_id}/approve", status_code=201)
def approve_trade_plan(
    plan_id: int,
    body: PlanApprovalRequest,
    db: Session = Depends(get_db),
):
    plan = _get_plan(db, plan_id)

    # Resolve idempotency before checking the now-stale lock version.  The same
    # request may be retried safely, but a key can never authorize another plan.
    by_key = (db.query(ExecutionIntent)
              .filter(ExecutionIntent.idempotency_key == body.idempotency_key)
              .first())
    if by_key is not None:
        if by_key.plan_id != plan.id:
            raise _blocked(plan_service.PlanBlocked(
                "idempotency_conflict", "该幂等键已用于另一交易计划"))
        return {
            "plan": plan_service.serialize_plan(plan),
            "intent": _serialize_intent(by_key, plan),
        }
    by_plan = (db.query(ExecutionIntent)
               .filter(ExecutionIntent.plan_id == plan.id).first())
    if by_plan is not None:
        raise _blocked(plan_service.PlanBlocked(
            "already_approved", "该计划已经生成下单票据"))

    try:
        intent = plan_service.approve_plan(
            db,
            plan,
            expected_lock_version=body.expected_lock_version,
            idempotency_key=body.idempotency_key,
            confirmed=body.confirmed,
            human_official_confirmed=body.human_official_confirmed,
            require_session=True,
            commit=True,
        )
    except plan_service.PlanBlocked as error:
        raise _blocked(error) from error
    except IdempotencyConflict as error:
        raise _blocked(plan_service.PlanBlocked(
            "idempotency_conflict", str(error))) from error
    except IntegrityError as error:
        # A concurrent approval may win the unique constraint.  Never create a
        # second ticket and never turn the race into an implicit broker order.
        db.rollback()
        existing = (db.query(ExecutionIntent)
                    .filter(ExecutionIntent.plan_id == plan.id).first())
        if existing is not None and existing.idempotency_key == body.idempotency_key:
            db.refresh(plan)
            return {
                "plan": plan_service.serialize_plan(plan),
                "intent": _serialize_intent(existing, plan),
            }
        raise _blocked(plan_service.PlanBlocked(
            "approval_conflict", "计划已被另一个审批请求更新")) from error

    return {
        "plan": plan_service.serialize_plan(plan),
        "intent": _serialize_intent(intent, plan),
    }


@trade_plan_router.post("/{plan_id}/reject")
def reject_trade_plan(
    plan_id: int,
    body: PlanRejectionRequest,
    db: Session = Depends(get_db),
):
    plan = _get_plan(db, plan_id)
    if db.query(ExecutionIntent).filter(ExecutionIntent.plan_id == plan.id).first():
        raise _blocked(plan_service.PlanBlocked(
            "ticket_ready", "计划已经生成下单票据，不能再拒绝原计划"))
    try:
        rejected = plan_service.reject_plan(
            db,
            plan,
            reason=body.reason,
            expected_lock_version=body.expected_lock_version,
            commit=True,
        )
    except plan_service.PlanBlocked as error:
        raise _blocked(error) from error
    return plan_service.serialize_plan(rejected)


@execution_intent_router.get("")
def list_execution_intents(
    status: str | None = None,
    model_pk: int | None = None,
    code: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(ExecutionIntent, TradePlan).join(
        TradePlan, TradePlan.id == ExecutionIntent.plan_id)
    if status:
        query = query.filter(ExecutionIntent.status == status)
    if model_pk is not None:
        query = query.filter(TradePlan.model_pk == model_pk)
    if code:
        query = query.filter(TradePlan.code == code.strip())
    rows = query.order_by(ExecutionIntent.id.desc()).limit(limit).all()
    return {"items": [_serialize_intent(intent, plan) for intent, plan in rows]}
