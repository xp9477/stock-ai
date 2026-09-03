"""Application service for conditional trade plans.

The service stops at an ``ExecutionIntent`` ticket.  It does not call the
broker: analysis, information/price/capital gates and ticket issuance are
separate auditable facts.  Manual confirmation is optional.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..data import market, news_rss
from ..models import ExecutionIntent, GateCheck, TradePlan, utc_now
from ..runtime_settings import get_setting
from . import portfolio
from .trade_plans import (
    PASS,
    REVIEW_REQUIRED,
    PriceGateResult,
    evaluate_price_gate,
    historical_gap_threshold,
    record_gate,
)

logger = logging.getLogger(__name__)


UTC = timezone.utc
SHANGHAI = ZoneInfo("Asia/Shanghai")
TERMINAL_STATUSES = {
    "expired", "invalidated_price", "invalidated_condition", "rejected",
    "superseded", "executed", "cancelled",
}


class PlanBlocked(RuntimeError):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _json_object(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _as_utc(value: datetime | str | None, *, assume_tz=UTC) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip().replace("Z", "+00:00")
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=assume_tz)
    return value.astimezone(UTC)


def _policy_value(plan: TradePlan, key: str, fallback_setting: str) -> Any:
    snapshot = _json_object(plan.policy_snapshot_json)
    return snapshot.get(key, get_setting(fallback_setting))


def serialize_plan(plan: TradePlan, *, gates: list[GateCheck] | None = None) -> dict[str, Any]:
    def iso(value: datetime | None) -> str | None:
        aware = _as_utc(value)
        return aware.isoformat() if aware else None

    result = {
        "id": plan.id,
        "decision_id": plan.decision_id,
        "run_id": plan.run_id,
        "model_pk": plan.model_pk,
        "code": plan.code,
        "name": plan.name,
        "side": plan.side,
        "status": plan.status,
        "status_reason_code": plan.status_reason_code,
        "status_reason": plan.status_reason,
        "version": plan.version,
        "lock_version": plan.lock_version,
        "target_position_pct": plan.target_position_pct,
        "max_buy_price": plan.max_buy_price,
        "reference_price": plan.reference_price,
        "reference_price_kind": plan.reference_price_kind,
        "reference_price_at": iso(plan.reference_price_at),
        "data_cutoff_at": iso(plan.data_cutoff_at),
        "valid_from_at": iso(plan.valid_from_at),
        "expires_at": iso(plan.expires_at),
        "confidence": plan.confidence,
        "thesis": plan.thesis,
        "invalidation_conditions": _json_object(plan.invalidation_conditions_json),
        "policy_snapshot": _json_object(plan.policy_snapshot_json),
        "factsheet_hash": plan.factsheet_hash,
        "approved_at": iso(plan.approved_at),
        "created_at": iso(plan.created_at),
        "updated_at": iso(plan.updated_at),
    }
    if gates is not None:
        result["gates"] = [{
            "id": gate.id,
            "gate_type": gate.gate_type,
            "outcome": gate.outcome,
            "reason_code": gate.reason_code,
            "reason": gate.reason,
            "checked_at": iso(gate.checked_at),
            "quote_price": gate.quote_price,
            "quote_asof": iso(gate.quote_asof),
            "opening_gap_pct": gate.opening_gap_pct,
            "dynamic_gap_threshold_pct": gate.dynamic_gap_threshold_pct,
            "signal_price_deviation_pct": gate.signal_price_deviation_pct,
            "source_results": _json_object(gate.source_results_json),
        } for gate in gates]
    return result


def review_preopen_information(
    db: Session,
    plan: TradePlan,
    *,
    human_official_confirmed: bool = False,
    force_refresh: bool = True,
    idempotency_key: str | None = None,
    commit: bool = False,
) -> GateCheck:
    """Compare the current stock-news snapshot with the analysis snapshot.

    RSS remains supplemental.  Until an official disclosure provider exists,
    the gate passes only when the local user explicitly confirms an external
    official-announcement check.  Only an additive direct-stock-news item is
    material here.  Feed truncation, expiry, or a temporarily missing source
    must not masquerade as "new" information.
    """
    snapshot = news_rss.stock_news_gate_snapshot(
        plan.code, plan.name, force_refresh=force_refresh)
    policy = _json_object(plan.policy_snapshot_json)
    original_scope = str(policy.get("news_scope") or "")
    original_fingerprint = str(policy.get("news_fingerprint") or "")
    current_fingerprint = str(snapshot.get("fingerprint") or "")
    current_item_ids = {
        str(i.get("content_hash") or i.get("url") or i.get("title") or "")
        for i in snapshot.get("items") or []
        if i.get("content_hash") or i.get("url") or i.get("title")
    }
    has_direct_baseline = (
        original_scope == "direct_stock_only_v1"
        and isinstance(policy.get("news_item_ids"), list)
    )
    original_item_ids = {
        str(item_id) for item_id in policy.get("news_item_ids") or []
        if str(item_id)
    } if has_direct_baseline else set()
    new_item_ids = sorted(current_item_ids - original_item_ids)

    if not has_direct_baseline:
        outcome = REVIEW_REQUIRED
        reason_code = "unverifiable_analysis_news_scope"
        reason = "原始分析未冻结直接个股新闻集合，无法可靠做增量比较，必须重新分析"
        next_status = outcome
    elif not original_fingerprint:
        outcome = REVIEW_REQUIRED
        reason_code = "missing_analysis_news_fingerprint"
        reason = "原始分析缺少资讯指纹，无法证明盘前信息未变化，必须重新分析"
        next_status = outcome
    elif not snapshot.get("rss_coverage_ok"):
        outcome = "blocked_information"
        reason_code = "rss_coverage_unavailable"
        reason = "盘前补充资讯源不可用，旧计划不能继续"
        next_status = outcome
    elif new_item_ids:
        outcome = REVIEW_REQUIRED
        reason_code = "new_direct_news"
        reason = "分析截止后出现新的个股相关新闻，旧计划需要重新分析"
        next_status = outcome
    elif (
        not snapshot.get("official_coverage")
        and bool(get_setting("execution.require_human_information_check"))
        and not human_official_confirmed
    ):
        outcome = "blocked_information"
        reason_code = "official_disclosure_unverified"
        reason = "尚未接入可靠正式披露源，必须人工核对公告后才能批准"
        next_status = outcome
    else:
        outcome = PASS
        reason_code = "information_gate_passed"
        reason = "盘前增量资讯已复核；正式公告由可靠源或用户明确确认"
        next_status = "preopen_validated"

    check = record_gate(
        db,
        plan,
        gate_type="preopen_information",
        outcome=outcome,
        reason_code=reason_code,
        reason=reason,
        required_sources=["rss_supplemental", "official_disclosure_or_human"],
        source_results={
            "rss": snapshot.get("source_results") or [],
            "rss_coverage_ok": bool(snapshot.get("rss_coverage_ok")),
            "official_coverage": bool(snapshot.get("official_coverage")),
            "human_official_confirmed": human_official_confirmed,
            "analysis_news_fingerprint_present": bool(original_fingerprint),
            "analysis_news_scope": original_scope,
            "analysis_news_item_count": len(original_item_ids),
            "current_news_item_count": len(current_item_ids),
        },
        new_information_ids=new_item_ids,
        input_hash=current_fingerprint,
        idempotency_key=idempotency_key,
        next_status=next_status,
        commit=commit,
    )
    return check


def _quote_asof(quote: dict[str, Any]) -> datetime | None:
    return _as_utc(quote.get("quote_asof"), assume_tz=SHANGHAI)


def validate_plan_price(
    db: Session,
    plan: TradePlan,
    *,
    quote: dict[str, Any] | None = None,
    daily_bars: Any | None = None,
    now: datetime | None = None,
    require_session: bool = True,
    enforce_valid_from: bool = True,
    idempotency_key: str | None = None,
    commit: bool = False,
) -> tuple[GateCheck, PriceGateResult, dict[str, Any] | None]:
    checked_at = _as_utc(now) or datetime.now(UTC)
    quote = quote if quote is not None else market.get_execution_quote(
        plan.code, force_refresh=True, require_session=require_session)
    daily_bars = daily_bars if daily_bars is not None else market.get_daily_kline(plan.code)

    lookback = int(_policy_value(plan, "gap_lookback_days", "signal.gap_lookback_days"))
    percentile = float(_policy_value(plan, "gap_percentile", "signal.gap_percentile"))
    min_samples = int(_policy_value(plan, "gap_min_samples", "signal.gap_min_samples"))
    hard = float(_policy_value(
        plan, "hard_price_deviation_pct", "signal.hard_price_deviation_pct"))
    threshold = historical_gap_threshold(
        daily_bars,
        lookback=lookback,
        percentile=percentile,
        min_samples=min_samples,
        completed_before=checked_at.astimezone(SHANGHAI).date(),
    )
    quote_asof = _quote_asof(quote or {})
    max_age = float(get_setting("execution.max_quote_age_seconds"))
    evaluation = evaluate_price_gate(
        current_price=(quote or {}).get("price"),
        reference_price=plan.reference_price,
        max_buy_price=plan.max_buy_price,
        opening_price=(quote or {}).get("open"),
        previous_close=(quote or {}).get("prev_close"),
        dynamic_gap_threshold=threshold,
        expires_at=_as_utc(plan.expires_at),
        valid_from_at=_as_utc(plan.valid_from_at),
        enforce_valid_from=enforce_valid_from,
        now=checked_at,
        quote_asof=quote_asof,
        max_quote_age_seconds=max_age,
        hard_deviation_threshold=hard,
        side=plan.side,
    )
    next_status = "awaiting_approval" if evaluation.outcome == PASS else None
    check = record_gate(
        db,
        plan,
        gate_type="pretrade_quote",
        evaluation=evaluation,
        metrics={
            "gap_lookback_days": lookback,
            "gap_percentile": percentile,
            "gap_min_samples": min_samples,
            "hard_price_deviation_pct": hard,
            "quote_source": (quote or {}).get("source"),
        },
        input_hash=hashlib.sha256(json.dumps(
            quote or {}, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest(),
        idempotency_key=idempotency_key,
        next_status=next_status,
        commit=commit,
    )
    return check, evaluation, quote


def approve_plan(
    db: Session,
    plan: TradePlan,
    *,
    expected_lock_version: int,
    idempotency_key: str,
    confirmed: bool,
    human_official_confirmed: bool,
    quote: dict[str, Any] | None = None,
    daily_bars: Any | None = None,
    now: datetime | None = None,
    require_session: bool = True,
    enforce_valid_from: bool = True,
    approved_by: str = "local_user",
    commit: bool = True,
) -> ExecutionIntent:
    """Run machine gates and create one execution ticket.

    This function never fills the ticket and never calls ``broker``.
    """
    existing = (db.query(ExecutionIntent)
                .filter(ExecutionIntent.idempotency_key == idempotency_key).first())
    if existing is not None:
        if existing.plan_id != plan.id:
            raise IdempotencyConflict(
                "approval idempotency key belongs to another trade plan")
        return existing
    require_manual = bool(get_setting("execution.require_manual_confirmation"))
    require_official = bool(get_setting("execution.require_human_information_check"))
    if require_manual and not confirmed:
        raise PlanBlocked("awaiting_approval", "必须明确确认该条件计划")
    if plan.status in TERMINAL_STATUSES:
        raise PlanBlocked(plan.status, plan.status_reason or "计划已经失效")
    if plan.lock_version != expected_lock_version:
        raise PlanBlocked("version_conflict", "计划已被更新，请刷新后重新确认")
    if require_manual and require_official and not human_official_confirmed:
        raise PlanBlocked("blocked_information", "必须确认已经核对正式公告与重大新闻")

    info = review_preopen_information(
        db, plan,
        human_official_confirmed=human_official_confirmed or not require_official,
        force_refresh=True,
        idempotency_key=f"{idempotency_key}:information", commit=False)
    if info.outcome != PASS:
        if commit:
            db.commit()
        raise PlanBlocked(plan.status, info.reason)

    _, evaluation, refreshed_quote = validate_plan_price(
        db, plan, quote=quote, daily_bars=daily_bars, now=now,
        require_session=require_session,
        enforce_valid_from=enforce_valid_from,
        idempotency_key=f"{idempotency_key}:price", commit=False)
    if evaluation.outcome != PASS or refreshed_quote is None:
        if commit:
            db.commit()
        raise PlanBlocked(plan.status, evaluation.reason)

    approved_at = _as_utc(now) or datetime.now(UTC)
    quote_time = _quote_asof(refreshed_quote)
    if quote_time is None:
        raise PlanBlocked("blocked_quote", "成交报价缺少时间戳")

    from .broker_snapshot import (
        BrokerSnapshotError,
        reconcile_configured_broker_portfolio,
    )
    try:
        broker_reference = reconcile_configured_broker_portfolio(
            db, [plan.model_pk],
        )
    except BrokerSnapshotError as exc:
        reason = f"券商组合参考不可用: {exc}"
        record_gate(
            db,
            plan,
            gate_type="broker_reconciliation",
            outcome="blocked_broker",
            reason_code="broker_reference_unavailable",
            reason=reason,
            checked_at=approved_at,
            metrics={"reference_ready": False},
            input_hash=hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            idempotency_key=f"{idempotency_key}:broker",
            next_status="blocked_capital",
            commit=False,
        )
        if commit:
            db.commit()
        raise PlanBlocked("blocked_capital", reason) from exc
    if broker_reference is not None:
        broker_json = json.dumps(
            broker_reference, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        )
        record_gate(
            db,
            plan,
            gate_type="broker_reconciliation",
            outcome=PASS,
            reason_code="broker_reference_ready",
            reason="Fresh EMT simulation portfolio projected before authorization",
            checked_at=approved_at,
            metrics=broker_reference,
            input_hash=hashlib.sha256(broker_json.encode("utf-8")).hexdigest(),
            idempotency_key=f"{idempotency_key}:broker",
            commit=False,
        )

    authorization = portfolio.authorize_execution_intent(
        db,
        model_pk=plan.model_pk,
        code=plan.code,
        side=plan.side,
        target_position_pct=plan.target_position_pct,
        approval_quote=refreshed_quote,
        authorization_time=approved_at,
        require_session=require_session,
    )
    authorization_payload = {
        "allowed": authorization.allowed,
        "reason_code": authorization.reason_code,
        "reason": authorization.reason,
        "side": authorization.side,
        "authorized_target_position_pct": (
            authorization.authorized_target_position_pct),
        "authorized_notional": authorization.authorized_notional,
        "authorized_qty": authorization.authorized_qty,
        "estimated_fee": authorization.estimated_fee,
        "risk_snapshot": authorization.risk_snapshot,
    }
    authorization_json = json.dumps(
        authorization_payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )
    record_gate(
        db,
        plan,
        gate_type="pretrade_capital",
        outcome=PASS if authorization.allowed else "blocked_capital",
        reason_code=authorization.reason_code,
        reason=authorization.reason,
        checked_at=approved_at,
        metrics=authorization_payload,
        input_hash=hashlib.sha256(authorization_json.encode("utf-8")).hexdigest(),
        idempotency_key=f"{idempotency_key}:capital",
        next_status=None if authorization.allowed else "blocked_capital",
        commit=False,
    )
    if not authorization.allowed:
        if commit:
            db.commit()
        raise PlanBlocked("blocked_capital", authorization.reason)

    intent = ExecutionIntent(
        plan_id=plan.id,
        status="ticket_ready",
        idempotency_key=idempotency_key,
        approved_by=approved_by,
        approved_at=approved_at,
        approval_quote_price=float(refreshed_quote["price"]),
        approval_quote_asof=quote_time,
        authorized_target_position_pct=(
            authorization.authorized_target_position_pct),
        authorized_notional=authorization.authorized_notional,
        authorized_qty=authorization.authorized_qty,
        estimated_fee=authorization.estimated_fee,
        risk_snapshot_json=json.dumps(
            authorization.risk_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
        expires_at=_as_utc(plan.expires_at),
    )
    db.add(intent)
    plan.status = "ticket_ready"
    auto_issued = approved_by == "system"
    plan.status_reason_code = "auto_approved" if auto_issued else "human_approved"
    plan.status_reason = (
        "机器门禁通过，已自动生成待执行票据；尚未向券商下单"
        if auto_issued
        else "全部门禁通过，已生成待执行票据；尚未成交"
    )
    plan.approved_at = approved_at
    plan.lock_version += 1
    plan.updated_at = utc_now()
    db.flush()
    if commit:
        db.commit()
    return intent


def maybe_auto_issue_ticket(
    db: Session,
    plan: TradePlan,
    *,
    now: datetime | None = None,
) -> ExecutionIntent | None:
    """Legacy auto-ticket hook, kept fail-closed for future orchestration.

    It may only run inside the plan's valid trading window and it never claims
    that a human checked official disclosures.  Production safe defaults keep
    this path disabled until a separately audited execution worker exists.
    """
    if bool(get_setting("execution.require_manual_confirmation")):
        return None
    existing = (
        db.query(ExecutionIntent)
        .filter(ExecutionIntent.plan_id == plan.id)
        .first()
    )
    if existing is not None:
        return existing
    try:
        return approve_plan(
            db,
            plan,
            expected_lock_version=plan.lock_version,
            idempotency_key=f"auto-approve:plan:{plan.id}",
            confirmed=True,
            human_official_confirmed=False,
            now=now,
            require_session=True,
            enforce_valid_from=True,
            approved_by="system",
            commit=True,
        )
    except PlanBlocked as error:
        logger.info(
            "自动出票未通过 plan=%s status=%s: %s",
            plan.id, error.status, error.reason,
        )
        return None
    except Exception:  # noqa: BLE001
        logger.exception("自动出票异常 plan=%s", plan.id)
        return None


def _fill_via_emt(
    db: Session, plan: TradePlan, intent: ExecutionIntent,
) -> ExecutionIntent:
    import time

    from ..config import settings as app_settings
    from . import emt_orders
    from .broker_snapshot import (
        BrokerSnapshotError,
        load_broker_snapshot,
        reconcile_snapshot_projection,
    )

    price = float(intent.approval_quote_price or 0.0)
    if plan.side == "buy" and plan.max_buy_price:
        cap = float(plan.max_buy_price)
        price = min(price, cap) if price > 0 else cap
    if price <= 0:
        logger.warning("EMT 委托跳过 plan=%s: 无有效限价", plan.id)
        return intent

    result = emt_orders.submit_simulation_order(
        side=plan.side,
        code=plan.code,
        quantity=int(intent.authorized_qty),
        price=price,
    )
    if not result.get("accepted"):
        plan.status_reason_code = "emt_order_rejected"
        plan.status_reason = f"EMT 模拟委托未接受: {result.get('error') or 'unknown'}"
        db.commit()
        logger.warning("EMT 委托失败 plan=%s: %s", plan.id, plan.status_reason)
        return intent

    deadline = time.monotonic() + float(app_settings.broker_order_timeout_seconds) + 30
    seen = False
    while time.monotonic() < deadline:
        try:
            snapshot = load_broker_snapshot(
                app_settings.broker_snapshot_path,
                max_age_seconds=max(
                    int(app_settings.broker_snapshot_max_age_seconds), 120),
            )
        except BrokerSnapshotError:
            time.sleep(1)
            continue
        if plan.side == "buy":
            seen = any(
                str(row.get("ticker") or "") == plan.code
                and int(row.get("total_qty") or 0) > 0
                for row in snapshot.positions
            )
        else:
            seen = any(
                str(row.get("ticker") or "") == plan.code
                for row in snapshot.trades
            )
        if seen:
            try:
                reconcile_snapshot_projection(
                    db,
                    model_pk=plan.model_pk,
                    snapshot=snapshot,
                    initial_equity=float(app_settings.broker_snapshot_initial_equity),
                )
            except Exception:  # noqa: BLE001
                logger.exception("EMT 成交后回写账户失败 plan=%s", plan.id)
            intent.status = "executed"
            plan.status = "executed"
            plan.status_reason_code = "emt_filled"
            plan.status_reason = (
                f"EMT 模拟盘已成交，委托号 {result.get('order_emt_id')}"
            )
            plan.lock_version += 1
            db.commit()
            logger.info("EMT 模拟成交 plan=%s emt_id=%s", plan.id, result.get("order_emt_id"))
            return intent
        time.sleep(1)

    plan.status_reason_code = "emt_order_pending"
    plan.status_reason = (
        f"EMT 已报单 {result.get('order_emt_id')}，等待下一轮快照回写持仓"
    )
    db.commit()
    logger.info("EMT 报单已接受但尚未回写 plan=%s", plan.id)
    return intent


def maybe_auto_fill_ticket(
    db: Session,
    plan: TradePlan,
    intent: ExecutionIntent,
) -> ExecutionIntent:
    """Safety pause: an execution ticket never becomes an order here.

    The file-based EMT request channel cannot yet prove exactly-once order
    submission or match partial fills to a durable local order ledger.  Keep
    the compatibility hook as a no-op until that state machine exists.
    """
    del db  # explicit: this safety path performs no persistence or side effect
    logger.info(
        "自动成交安全冻结 plan=%s intent=%s；仅保留人工票据",
        plan.id, intent.id,
    )
    return intent


def reject_plan(
    db: Session, plan: TradePlan, *, reason: str, expected_lock_version: int,
    commit: bool = True,
) -> TradePlan:
    if plan.lock_version != expected_lock_version:
        raise PlanBlocked("version_conflict", "计划已被更新，请刷新后重试")
    if plan.status in TERMINAL_STATUSES:
        raise PlanBlocked(plan.status, plan.status_reason or "计划已经结束")
    plan.status = "rejected"
    plan.status_reason_code = "human_rejected"
    plan.status_reason = reason.strip() or "用户拒绝"
    plan.lock_version += 1
    plan.updated_at = utc_now()
    if commit:
        db.commit()
    return plan
