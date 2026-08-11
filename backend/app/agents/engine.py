"""多模型条件交易计划引擎。

所有独立模型只读取同一份冻结且账户盲化的事实快照；最终交易员与风险复核
使用不同模型。流水线只生成可审计的 ``TradePlan``，绝不在分析阶段成交。
"""
import json
import hashlib
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..data import market
from ..data.indicators import indicators_text
from ..models import (AgentOutput, Decision, Model, Position, Run, RunMarketArtifact,
                      TradeLedger, Watchlist)
from ..runtime_settings import get_setting
from ..trading import broker, portfolio
from . import llm

logger = logging.getLogger(__name__)

# ---------- 运行锁 / 进度 / 取消 ----------

_run_lock = False
_cancel_requested = False
_progress_lock = threading.Lock()
_progress: dict[str, Any] = {
    "run_id": None,
    "phase": "idle",
    "message": "",
    "model_name": "",
    "model_pk": None,
    "model_index": 0,
    "model_total": 0,
    "code": "",
    "stock_name": "",
    "stock_index": 0,
    "stock_total": 0,
    "agent": "",
    "updated_at": None,
}


class PipelineCancelled(Exception):
    """用户请求协作式取消。"""


def get_progress() -> dict[str, Any]:
    with _progress_lock:
        return dict(_progress)


def is_running() -> bool:
    return bool(_run_lock)


def cancel_requested() -> bool:
    return bool(_cancel_requested)


def request_cancel() -> dict[str, Any]:
    """请求协作式取消。返回 {ok, message, run_id}。"""
    global _cancel_requested
    if not _run_lock:
        return {"ok": False, "message": "当前没有运行中的决策", "run_id": None}
    _cancel_requested = True
    with _progress_lock:
        rid = _progress.get("run_id")
        _progress["message"] = "取消已请求，将在当前 Agent 结束后停止"
        _progress["updated_at"] = datetime.now().isoformat(timespec="seconds")
    logger.info("协作式取消已请求 run_id=%s", rid)
    return {"ok": True, "message": "已请求停止，当前步骤结束后生效", "run_id": rid}


def _set_progress(**kwargs: Any) -> None:
    with _progress_lock:
        _progress.update(kwargs)
        _progress["updated_at"] = datetime.now().isoformat(timespec="seconds")


def _check_cancel() -> None:
    if _cancel_requested:
        raise PipelineCancelled("用户取消")


def _save_output(db: Session, run_id: int, model_pk: int, code: str, agent: str,
                 input_summary: str, output: str, *, system_prompt: str,
                 model_id: str):
    audit = llm.audit_metadata(system_prompt, input_summary, output, model_id)
    db.add(AgentOutput(run_id=run_id, model_pk=model_pk, code=code, agent=agent,
                       input_summary=input_summary, output=output, **audit))
    db.commit()


def _position_context(db: Session, model_pk: int, code: str) -> str:
    eq = portfolio.total_equity(db, model_pk)
    from ..trading import risk_contract
    contract = risk_contract.load_capital_contract()
    state = risk_contract.refresh_canary_state(
        db, model_pk, actual_total_equity=eq["total_equity"],
        account_initial_cash=eq["initial_cash"], contract=contract)
    pos = broker.get_position(db, model_pk, code)
    authorized_cash = max(state.risk_equity - eq["market_value"], 0.0)
    lines = [
        f"授权资金: {contract.authorized_capital:.0f} 元",
        f"策略风险权益: {state.risk_equity:.0f} 元",
        f"授权范围内可用资金: {authorized_cash:.0f} 元",
        f"股票敞口: {eq['market_value']:.0f}/{contract.max_stock_exposure:.0f} 元",
    ]
    if pos:
        quote = market.get_quote(code)
        price = quote["price"] if quote else pos.avg_cost
        pnl_pct = (price - pos.avg_cost) / pos.avg_cost if pos.avg_cost else 0
        lines += [
            f"当前持有 {pos.total_qty} 股 (今日可卖 {pos.available_qty} 股)",
            f"持仓成本 {pos.avg_cost:.2f} 元, 现价 {price:.2f} 元, 浮动盈亏 {pnl_pct:.1%}",
            f"该股仓位占授权资金 {pos.total_qty * price / contract.authorized_capital:.1%}",
        ]
        if pnl_pct <= float(get_setting("risk.stop_loss_alert_pct")):
            lines.append("⚠️ 警告: 该持仓浮亏已超过止损提示线,必须评估是否止损!")
    else:
        lines.append("当前未持有该股票")
    return "\n".join(filter(None, lines))


def _risk_context(db: Session, model_pk: int) -> str:
    """风险模型专用上下文；独立判断模型绝不能调用。"""
    from ..trading import risk_contract

    eq = portfolio.total_equity(db, model_pk)
    contract = risk_contract.load_capital_contract()
    state = risk_contract.refresh_canary_state(
        db, model_pk, actual_total_equity=eq["total_equity"],
        account_initial_cash=eq["initial_cash"], contract=contract)
    realized = sum(
        float(row.pnl or 0.0)
        for row in db.query(TradeLedger).filter(
            TradeLedger.model_pk == model_pk,
            TradeLedger.is_closed.is_(True),
        ).all()
    )
    unrealized = 0.0
    for pos in db.query(Position).filter(Position.model_pk == model_pk).all():
        value = portfolio.position_value(pos)
        unrealized += value - pos.avg_cost * pos.total_qty
    remaining = max(contract.canary_stop_drawdown - state.drawdown, 0.0)
    return "\n".join([
        f"Canary状态: {state.status}",
        f"授权资金: {contract.authorized_capital:.0f} 元",
        f"股票敞口: {eq['market_value']:.0f}/{contract.max_stock_exposure:.0f} 元",
        f"已实现盈亏: {realized:+.2f} 元",
        f"未实现盈亏: {unrealized:+.2f} 元",
        f"策略风险权益: {state.risk_equity:.2f} 元",
        f"高水位: {state.high_water:.2f} 元",
        f"当前回撤: {state.drawdown:.2f} 元",
        f"告警级别: {state.alert_level}（5000/10000 只告警）",
        f"距 15000 停止线剩余: {remaining:.2f} 元",
        "停止线只禁止新增风险，不会自动减仓或清仓。",
    ])


# ---------- 共享数据准备(每轮一次,与模型无关) ----------

def prepare_stock_inputs(code: str, name: str, peer_codes: list[str] | None = None) -> dict:
    """采集个股数据 + X1 事实底稿,同一轮内所有模型共享。"""
    from ..data.factsheet import build_factsheet, factsheet_text
    from ..ledger import factsheet_hash

    try:
        kline = market.get_daily_kline(code)
        tech_input = f"股票: {name}({code})\n\n{indicators_text(kline)}"
    except Exception as err:  # noqa: BLE001
        tech_input = f"股票: {name}({code})\n\n(K线数据获取失败: {err})"

    quote: dict[str, Any] = {}
    try:
        info = market.get_stock_info(code)
        quote = market.get_quote(code) or {}
        fund_input = (
            f"股票: {name}({code})\n"
            f"基本信息: {info}\n"
            f"最新估值: PE(动态)={quote.get('pe')}, PB={quote.get('pb')}, "
            f"总市值={quote.get('market_cap')}"
        )
    except Exception as err:  # noqa: BLE001
        fund_input = f"股票: {name}({code})\n\n(基本面数据获取失败: {err})"

    stock_news_items: list[dict[str, Any]] = []
    news_fingerprint = ""
    try:
        news_items = market.get_news(code, name=name)
        from ..data import news_rss
        stock_news_items = news_rss.news_for_stock(
            code, name=name, limit=20, include_general=False)
        news_fingerprint = hashlib.sha256(
            "\n".join(sorted(
                str(item.get("content_hash") or item.get("url") or item.get("title") or "")
                for item in stock_news_items
            )).encode("utf-8")
        ).hexdigest()
        if news_items:
            news_input = f"股票: {name}({code})\n近期新闻(RSS):\n" + "\n".join(
                f"- [{item.get('time', '')}] {item.get('title', '')}: {item.get('content', '')}"
                f"{' ·' + item['source'] if item.get('source') else ''}"
                for item in news_items
            )
        else:
            news_input = f"股票: {name}({code})\n\n(暂无 RSS 相关新闻；可在设置 → 数据源检查「新闻 RSS」)"
    except Exception as err:  # noqa: BLE001
        news_input = f"股票: {name}({code})\n\n(新闻数据获取失败: {err})"

    # X1：S2 因子 + 统一底稿（所有 AI 臂必读，禁止编造 missing）
    sheet = {}
    sheet_text = ""
    try:
        sheet = build_factsheet(code, name, peer_codes=peer_codes or [])
        sheet_text = factsheet_text(sheet)
        factors = sheet.get("factors") or {}
        if factors:
            fund_input += (
                f"\n\n【S2因子截面】score={factors.get('score')} rank={factors.get('rank')}"
                f"/{factors.get('universe_size')}\n"
                f"mom_short={factors.get('mom_short')} mom_mid={factors.get('mom_mid')} "
                f"low_vol={factors.get('low_vol')} ep={factors.get('ep')} "
                f"bp={factors.get('bp')} quality_roe={factors.get('quality_roe')}"
            )
    except Exception as err:  # noqa: BLE001
        logger.warning("factsheet %s: %s", code, err)
        sheet_text = f"(事实底稿构建失败: {err})"

    research_block = ""
    try:
        if bool(get_setting("research.inject_promoted_to_llm")):
            research_block = _promoted_research_context()
            if research_block:
                sheet_text = (sheet_text or "") + "\n\n" + research_block
    except Exception as err:  # noqa: BLE001
        logger.debug("research inject: %s", err)

    return {
        "tech": tech_input,
        "fund": fund_input,
        "news": news_input,
        "factsheet": sheet,
        "factsheet_text": sheet_text,
        "factsheet_hash": factsheet_hash(sheet) if sheet else "",
        "research_context": research_block,
        "stock_news_items": stock_news_items,
        "news_fingerprint": news_fingerprint,
        "reference_price": quote.get("price"),
        "reference_price_at": quote.get("quote_asof") or datetime.now().isoformat(),
        "data_cutoff_at": datetime.now(timezone.utc).isoformat(),
    }


def _promoted_research_context(db: Session | None = None) -> str:
    """Inject only promotions that still verify against immutable evidence.

    Historical ``rule`` models and their mutable ``members`` JSON are never a
    source of decision context.  A promoted hypothesis is shown only if its
    exact experiment, holdout reservation/access and result fingerprints pass
    the same validation used by the promotion endpoint.
    """
    from ..backtest import evidence
    from ..database import SessionLocal
    from ..models import ResearchHypothesis
    from ..research.service import _validated_promotion_experiment
    from ..research.spec import loads

    owns_session = db is None
    db = db or SessionLocal()
    try:
        rows = (
            db.query(ResearchHypothesis)
            .filter(ResearchHypothesis.status == "promoted")
            .order_by(ResearchHypothesis.id)
            .limit(8)
            .all()
        )
        if not rows:
            return ""
        lines = ["【不可变回测证据（可选参考，非强制）】"]
        for hypothesis in rows:
            try:
                spec = loads(hypothesis.spec_json)
                experiment = _validated_promotion_experiment(
                    db, hypothesis, spec)
                holdout = evidence.result_dict(evidence.get_result(
                    db, experiment.id, "holdout"))
                metrics = holdout["strategy"].get("metrics") or {}
            except Exception as error:  # noqa: BLE001 - unverified rows are omitted
                logger.warning(
                    "跳过无法复验的研究晋升 hypothesis=%s: %s",
                    hypothesis.id, error,
                )
                continue
            lines.append(
                f"- {hypothesis.title or spec.get('name') or hypothesis.id} "
                f"[experiment={experiment.id}, spec={experiment.spec_fingerprint[:12]}]: "
                f"mode={spec.get('mode')} "
                f"factors={spec.get('factors')} top_n={spec.get('top_n')} "
                f"rebalance={spec.get('rebalance')}；"
                f"holdout_sharpe={metrics.get('sharpe')} "
                f"holdout_max_drawdown={metrics.get('max_drawdown')}"
            )
        return "\n".join(lines) if len(lines) > 1 else ""
    finally:
        if owns_session:
            db.close()


# ---------- 新流水线：同一冻结事实 -> 独立判断 -> 单一条件计划 ----------


def _frozen_snapshot(
    code: str, name: str, inputs: dict[str, Any], market_overview: str,
) -> tuple[str, str]:
    """Build the exact account-blind payload shared by every judgment model."""
    payload = {
        "schema_version": "decision_snapshot_v1",
        "data_cutoff_at": inputs.get("data_cutoff_at"),
        "code": code,
        "name": name,
        "reference_price": inputs.get("reference_price"),
        "market_overview": market_overview,
        "technical_data": inputs.get("tech"),
        "fundamental_data": inputs.get("fund"),
        "direct_stock_news": inputs.get("stock_news_items") or [],
        "factsheet": inputs.get("factsheet") or {},
        "factsheet_hash": inputs.get("factsheet_hash") or "",
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    wrapped = f"snapshot_sha256={digest}\n{canonical}"
    return wrapped, digest


def _run_independent_judgment(
    *, run_id: int, model_pk: int, code: str, name: str,
    frozen_snapshot: str,
) -> dict[str, Any]:
    """One model, one call, one strict judgment; no account context or trading."""
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        _check_cancel()
        model = db.get(Model, model_pk)
        if model is None or not model.enabled or model.type != "llm":
            return {"model_pk": model_pk, "judgment": None, "error": "模型不可用"}
        _set_progress(
            phase="judgment", model_name=model.name, model_pk=model.id,
            code=code, stock_name=name, agent="independent_judgment",
            message=f"独立判断 · {model.name} · {name}",
        )
        system_prompt = str(get_setting("prompt.independent_judgment"))
        raw = llm.chat(system_prompt, frozen_snapshot, model.model_id)
        _save_output(
            db, run_id, model.id, code, "independent_judgment",
            frozen_snapshot, raw,
            system_prompt=system_prompt, model_id=model.model_id,
        )
        judgment = llm.parse_independent_judgment(raw)
        return {
            "model_pk": model.id,
            "model_name": model.name,
            "model_id": model.model_id,
            "judgment": judgment,
            "raw": raw,
            "error": "" if judgment else "独立判断不符合严格 JSON 契约",
        }
    except PipelineCancelled:
        raise
    except Exception as err:  # noqa: BLE001
        logger.exception("独立判断失败 model=%s code=%s", model_pk, code)
        return {"model_pk": model_pk, "judgment": None, "error": str(err)}
    finally:
        db.close()


def _member_ids(ensemble: Model) -> list[int]:
    try:
        raw = json.loads(ensemble.members or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(int(value) for value in raw if isinstance(value, int)))


def _next_plan_window(now_utc: datetime) -> tuple[datetime, datetime]:
    """Return the next proven trade-day continuous-auction window."""
    local = now_utc.astimezone(ZoneInfo("Asia/Shanghai"))
    candidate = local.date() + timedelta(days=1)
    trade_day = None
    for _ in range(14):
        if market.is_trade_date(candidate):
            trade_day = candidate
            break
        candidate += timedelta(days=1)
    if trade_day is None:
        raise RuntimeError("未来 14 日内无法证明下一个交易日")
    hour, minute = str(get_setting("signal.default_valid_until")).split(":")
    valid_from = datetime(
        trade_day.year, trade_day.month, trade_day.day,
        9, 30, tzinfo=ZoneInfo("Asia/Shanghai"),
    ).astimezone(timezone.utc)
    deadline = datetime(
        trade_day.year, trade_day.month, trade_day.day,
        int(hour), int(minute), tzinfo=ZoneInfo("Asia/Shanghai"),
    ).astimezone(timezone.utc)
    if deadline <= valid_from:
        raise ValueError("signal.default_valid_until must be after 09:30")
    return valid_from, deadline


def _next_plan_deadline(now_utc: datetime) -> datetime:
    """Compatibility helper for callers that only need the deadline."""
    return _next_plan_window(now_utc)[1]


def _aware_datetime(value: Any, *, assume_shanghai: bool = False) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(
            tzinfo=ZoneInfo("Asia/Shanghai") if assume_shanghai else timezone.utc)
    return value.astimezone(timezone.utc)


def _risk_did_not_escalate(trader: dict[str, Any], risk: dict[str, Any]) -> bool:
    """Risk review may preserve/reduce risk, never manufacture or enlarge a buy."""
    if risk["action"] == "buy" and trader["action"] != "buy":
        return False
    if risk["action"] == trader["action"] == "buy":
        if risk["target_position_pct"] > trader["target_position_pct"] + 1e-12:
            return False
        if risk["max_buy_price"] > trader["max_buy_price"] + 1e-12:
            return False
        trader_expiry = _aware_datetime(trader.get("valid_until"))
        risk_expiry = _aware_datetime(risk.get("valid_until"))
        if trader_expiry is None or risk_expiry is None or risk_expiry > trader_expiry:
            return False
    return True


def _save_hold_decision(
    db: Session, run_id: int, ensemble: Model, code: str, name: str,
    reason: str, *, error: str = "",
) -> Decision:
    decision = Decision(
        run_id=run_id, model_pk=ensemble.id, code=code, name=name,
        action="hold", target_position_pct=0.0, confidence=0.0,
        reason=reason, error=error,
    )
    db.add(decision)
    db.commit()
    return decision


def _create_ensemble_candidate(
    db: Session,
    *,
    run_id: int,
    ensemble: Model,
    code: str,
    name: str,
    inputs: dict[str, Any],
    frozen_snapshot: str,
    snapshot_hash: str,
    judgments_by_model: dict[int, dict[str, Any]],
) -> tuple[Decision, Any | None]:
    """Use two distinct member models for final trading and risk review."""
    from ..backtest.shadow import verify_run_market_artifact

    if (
        ensemble.type != "ensemble"
        or not ensemble.enabled
        or not ensemble.is_official_strategy
    ):
        return _save_hold_decision(
            db, run_id, ensemble, code, name,
            "Model is not the enabled unique official strategy",
            error="not_official_strategy",
        ), None

    artifact = (
        db.query(RunMarketArtifact)
        .filter(RunMarketArtifact.run_id == run_id)
        .first()
    )
    if artifact is None:
        return _save_hold_decision(
            db, run_id, ensemble, code, name,
            "Run is missing its shared frozen market artifact",
            error="missing_run_market_artifact",
        ), None
    try:
        artifact_payload = verify_run_market_artifact(artifact)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return _save_hold_decision(
            db, run_id, ensemble, code, name,
            "Run market evidence is invalid; candidate creation failed closed",
            error=f"invalid_run_market_artifact:{error}",
        ), None
    input_cutoff = _aware_datetime(inputs.get("data_cutoff_at"))
    artifact_cutoff = _aware_datetime(artifact.data_cutoff_at)
    if (
        input_cutoff is None
        or artifact_cutoff is None
        or input_cutoff != artifact_cutoff
        or code not in artifact_payload["analysis_universe"]
    ):
        return _save_hold_decision(
            db, run_id, ensemble, code, name,
            "Candidate input does not match the Run cutoff/universe",
            error="run_market_artifact_binding_mismatch",
        ), None

    member_ids = _member_ids(ensemble)
    valid = [judgments_by_model[mid] for mid in member_ids
             if mid in judgments_by_model and judgments_by_model[mid].get("judgment")]
    quorum = max(2, math.ceil(len(member_ids) * 2 / 3))
    if len(valid) < quorum:
        return _save_hold_decision(
            db, run_id, ensemble, code, name,
            f"独立判断有效票不足 {len(valid)}/{quorum}，fail closed",
            error="independent_judgment_quorum_failed",
        ), None

    final_model = db.get(Model, valid[0]["model_pk"])
    risk_model = db.get(Model, valid[1]["model_pk"])
    if final_model is None or risk_model is None:
        return _save_hold_decision(
            db, run_id, ensemble, code, name, "最终/风险模型不可用",
            error="decision_models_unavailable",
        ), None

    now_utc = datetime.now(timezone.utc)
    valid_from, system_deadline = _next_plan_window(now_utc)
    compact_judgments = [{
        "model": row["model_name"],
        "model_id": row["model_id"],
        "judgment": row["judgment"],
    } for row in valid]
    trader_input = (
        f"【冻结事实】\n{frozen_snapshot}\n\n"
        f"【独立判断】\n{json.dumps(compact_judgments, ensure_ascii=False, sort_keys=True)}\n\n"
        f"【授权账户状态】\n{_position_context(db, ensemble.id, code)}\n\n"
        f"【系统允许的最晚有效期】\n{system_deadline.isoformat()}"
    )
    trader_prompt = str(get_setting("prompt.final_trader"))
    trader_raw = llm.chat(trader_prompt, trader_input, final_model.model_id)
    _save_output(
        db, run_id, ensemble.id, code, "final_trader", trader_input, trader_raw,
        system_prompt=trader_prompt, model_id=final_model.model_id)
    trader = llm.parse_trade_decision(trader_raw)
    if trader is None:
        return _save_hold_decision(
            db, run_id, ensemble, code, name,
            "最终交易员输出不符合条件计划契约",
            error="final_trader_contract_invalid",
        ), None

    risk_input = (
        f"【冻结事实哈希】\n{snapshot_hash}\n\n"
        f"【独立判断】\n{json.dumps(compact_judgments, ensure_ascii=False, sort_keys=True)}\n\n"
        f"【交易员条件计划】\n{json.dumps(trader, ensure_ascii=False, sort_keys=True)}\n\n"
        f"【完整账户风险状态】\n{_risk_context(db, ensemble.id)}"
    )
    risk_prompt = str(get_setting("prompt.risk_review"))
    risk_raw = llm.chat(risk_prompt, risk_input, risk_model.model_id)
    _save_output(
        db, run_id, ensemble.id, code, "risk_review", risk_input, risk_raw,
        system_prompt=risk_prompt, model_id=risk_model.model_id)
    reviewed = llm.parse_trade_decision(risk_raw)
    if reviewed is None or not _risk_did_not_escalate(trader, reviewed):
        return _save_hold_decision(
            db, run_id, ensemble, code, name,
            "风险审查输出无效或扩大了风险，fail closed",
            error="risk_review_contract_invalid",
        ), None

    action, target_pct, risk_note = portfolio.apply_risk_limits(
        db, ensemble.id, code, reviewed["action"], reviewed["target_position_pct"])
    thesis = reviewed["thesis"]
    if risk_note:
        thesis = f"{thesis} [代码边界: {risk_note}]"
    decision = Decision(
        run_id=run_id,
        model_pk=ensemble.id,
        code=code,
        name=name,
        action=action,
        target_position_pct=target_pct,
        confidence=reviewed["confidence"],
        reason=thesis,
    )
    db.add(decision)
    db.flush()
    if action not in {"buy", "sell"}:
        db.commit()
        return decision, None

    reference_price = inputs.get("reference_price")
    try:
        reference_price = float(reference_price)
    except (TypeError, ValueError):
        reference_price = 0.0
    if reference_price <= 0:
        decision.action = "hold"
        decision.target_position_pct = 0.0
        decision.error = "missing_reference_price"
        decision.reason += " [参考价格缺失，未生成计划]"
        db.commit()
        return decision, None

    expiry = (_aware_datetime(reviewed.get("valid_until"))
              if action == "buy" else system_deadline)
    if expiry is None or expiry <= now_utc:
        decision.action = "hold"
        decision.target_position_pct = 0.0
        decision.error = "invalid_plan_expiry"
        decision.reason += " [有效期无效，未生成计划]"
        db.commit()
        return decision, None
    expiry = min(expiry, system_deadline)

    from ..trading.trade_plans import create_plan_from_decision
    policy_snapshot = {
        "classification": "provisional",
        "snapshot_hash": snapshot_hash,
        "news_fingerprint": inputs.get("news_fingerprint") or "",
        "gap_lookback_days": int(get_setting("signal.gap_lookback_days")),
        "gap_percentile": float(get_setting("signal.gap_percentile")),
        "gap_min_samples": int(get_setting("signal.gap_min_samples")),
        "hard_price_deviation_pct": float(
            get_setting("signal.hard_price_deviation_pct")),
        "max_positions": int(get_setting("signal.max_positions")),
        "final_model_id": final_model.model_id,
        "risk_model_id": risk_model.model_id,
        "independent_model_ids": [row["model_id"] for row in valid],
        "prompt_hashes": {
            key: hashlib.sha256(str(get_setting(key)).encode("utf-8")).hexdigest()
            for key in (
                "prompt.independent_judgment", "prompt.final_trader",
                "prompt.risk_review",
            )
        },
    }
    data_cutoff = artifact_cutoff
    assert data_cutoff is not None
    ref_at = _aware_datetime(
        inputs.get("reference_price_at"), assume_shanghai=True) or data_cutoff
    plan = create_plan_from_decision(
        db,
        decision,
        reference_price=reference_price,
        reference_price_at=ref_at,
        reference_price_kind="analysis_quote",
        max_buy_price=reviewed.get("max_buy_price") if action == "buy" else None,
        data_cutoff_at=data_cutoff,
        valid_from_at=valid_from,
        expires_at=expiry,
        invalidation_conditions={
            "conditions": reviewed["invalidation_conditions"],
            "material_news": "review_required",
            "dynamic_opening_gap": "review_required",
        },
        policy_snapshot=policy_snapshot,
        factsheet_hash=inputs.get("factsheet_hash") or "",
        idempotency_key=f"run:{run_id}:ensemble:{ensemble.id}:code:{code}",
        commit=False,
    )
    db.commit()
    return decision, plan


# ---------- 总入口 ----------


def run_pipeline(trigger: str = "manual") -> int | None:
    """生成一轮前瞻候选计划；绝不在分析阶段成交。"""
    global _run_lock, _cancel_requested
    if _run_lock:
        logger.warning("上一轮决策仍在运行,跳过本轮")
        return None
    _run_lock = True
    _cancel_requested = False

    from ..database import SessionLocal

    db = SessionLocal()
    run = Run(trigger=trigger)
    db.add(run)
    db.commit()
    run_id = run.id
    _set_progress(
        run_id=run_id, phase="prepare", message="准备数据…",
        model_name="", model_pk=None, model_index=0, model_total=0,
        code="", stock_name="", stock_index=0, stock_total=0, agent="",
    )
    cancelled = False
    try:
        official_model_pks = broker.enabled_official_strategy_ids(db)
        broker.settle_t1(db, model_pks=official_model_pks)
        _check_cancel()

        # Watchlist 只是观察列表。新标的必须先通过确定性的可交易/板块/
        # ST/一手可负担性检查；已有持仓无论是否仍合格都要保留，以便分析卖出。
        held_positions = (db.query(Position)
                          .join(Model, Model.id == Position.model_pk)
                          .filter(
                              Model.type == "ensemble",
                              Model.is_official_strategy.is_(True),
                          ).all())
        targets: dict[str, str] = {
            pos.code: pos.name for pos in held_positions if pos.total_qty > 0
        }
        excluded_ineligible: list[dict[str, str]] = []
        eligible_quotes: dict[str, dict[str, Any]] = {}
        # Existing positions remain in the analysis universe so they can be
        # sold, but only those that independently pass the same deterministic
        # eligibility gate enter the mechanical shadow universe.
        for code in list(targets):
            eligible_quote, _reason = market.strategy_eligible_quote(code)
            if eligible_quote is not None:
                eligible_quotes[code] = dict(eligible_quote)
        for item in db.query(Watchlist).all():
            if item.code in targets:
                continue
            eligible_quote, reason = market.strategy_eligible_quote(item.code)
            if eligible_quote is None:
                excluded_ineligible.append({"code": item.code, "reason": reason})
                continue
            eligible_quotes[item.code] = dict(eligible_quote)
            targets[item.code] = str(eligible_quote.get("name") or item.name)

        peer_codes = list(targets.keys())
        stock_total = len(targets)
        _set_progress(
            phase="prepare", stock_total=stock_total,
            message=f"采集事实底稿 · {stock_total} 只",
        )
        stock_inputs = {}
        for i, (code, name) in enumerate(targets.items(), start=1):
            _check_cancel()
            _set_progress(
                phase="prepare", code=code, stock_name=name,
                stock_index=i, stock_total=stock_total,
                message=f"事实底稿 {i}/{stock_total} · {name}({code})",
            )
            stock_inputs[code] = prepare_stock_inputs(
                code, name, peer_codes=peer_codes)

        try:
            market_overview = market.market_overview_text()
        except Exception as err:  # noqa: BLE001
            market_overview = f"(市场概览不可用: {err})"

        # One Run has one upper information boundary.  Per-stock collection
        # completion times must never become different candidate cutoffs.
        run_cutoff = datetime.now(timezone.utc)
        for inputs in stock_inputs.values():
            inputs["data_cutoff_at"] = run_cutoff.isoformat()
        from ..backtest.shadow import freeze_engine_run_artifact
        freeze_engine_run_artifact(
            db,
            run_id=run_id,
            data_cutoff_at=run_cutoff,
            analysis_codes=list(targets),
            eligible_quotes=eligible_quotes,
        )

        llm_models = (db.query(Model)
                      .filter(Model.enabled.is_(True), Model.type == "llm")
                      .order_by(Model.id).all())
        ensembles = (db.query(Model)
                     .filter(
                         Model.enabled.is_(True),
                         Model.type == "ensemble",
                         Model.is_official_strategy.is_(True),
                     )
                     .order_by(Model.id).all())
        model_total = len(llm_models)
        if len(llm_models) < 2:
            run.status = "failed"
            run.error = "至少需要 2 个启用的独立 LLM，单模型不能形成候选计划"
            _set_progress(phase="failed", message=run.error, agent="")
            return run_id
        if not ensembles:
            run.status = "failed"
            run.error = "没有启用的合议策略账户；独立判断模型不能直接持仓或成交"
            _set_progress(phase="failed", message=run.error, agent="")
            return run_id

        frozen: dict[str, tuple[str, str]] = {
            code: _frozen_snapshot(code, targets[code], stock_inputs[code], market_overview)
            for code in targets
        }

        # 每个模型对同一快照只调用一次；不再扮演九个角色或注入反思记忆。
        llm_ids = [m.id for m in llm_models]
        llm_names = {m.id: m.name for m in llm_models}
        task_total = len(llm_ids) * max(len(targets), 1)
        max_workers = min(task_total, 4)
        _set_progress(
            phase="model", model_total=model_total,
            message=f"独立判断 {model_total} 模型 × {len(targets)} 股票",
        )
        errors: list[str] = []
        judgments: dict[str, dict[int, dict[str, Any]]] = {
            code: {} for code in targets
        }
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _run_independent_judgment,
                    run_id=run_id,
                    model_pk=mid,
                    code=code,
                    name=targets[code],
                    frozen_snapshot=frozen[code][0],
                ): (mid, code)
                for mid in llm_ids
                for code in targets
            }
            for fut in as_completed(futures):
                mid, code = futures[fut]
                try:
                    row = fut.result()
                    judgments[code][mid] = row
                    if row.get("error"):
                        errors.append(f"{llm_names.get(mid)}({code}): {row['error']}")
                except PipelineCancelled:
                    for f in futures:
                        f.cancel()
                    raise
                except Exception as err:  # noqa: BLE001
                    logger.exception("独立判断任务失败 %s %s", llm_names.get(mid), code)
                    errors.append(f"{llm_names.get(mid)}({code}): {err}")

        valid_count = sum(
            1 for by_model in judgments.values() for row in by_model.values()
            if row.get("judgment")
        )
        if targets and valid_count == 0:
            run.status = "failed"
            run.error = "全部独立判断失败: " + "; ".join(errors)[:500]
            _set_progress(phase="failed", message=run.error[:200], agent="")
            return run_id

        _check_cancel()
        for ensemble in ensembles:
            for code, name in targets.items():
                _check_cancel()
                _set_progress(
                    phase="ensemble", model_name=ensemble.name,
                    model_pk=ensemble.id, code=code, stock_name=name,
                    agent="final_trader", message=f"条件计划 · {ensemble.name} · {name}",
                )
                try:
                    _create_ensemble_candidate(
                        db,
                        run_id=run_id,
                        ensemble=ensemble,
                        code=code,
                        name=name,
                        inputs=stock_inputs[code],
                        frozen_snapshot=frozen[code][0],
                        snapshot_hash=frozen[code][1],
                        judgments_by_model=judgments[code],
                    )
                except PipelineCancelled:
                    raise
                except Exception as err:  # noqa: BLE001
                    logger.exception("条件计划生成失败 ensemble=%s code=%s",
                                     ensemble.name, code)
                    _save_hold_decision(
                        db, run_id, ensemble, code, name,
                        "条件计划生成失败，fail closed", error=str(err))

        for ensemble in ensembles:
            portfolio.snapshot_equity(db, ensemble.id)

        from collections import Counter
        from ..models import TradePlan
        decs = db.query(Decision).filter(Decision.run_id == run_id).all()
        action_counts = Counter(d.action for d in decs)
        plans = db.query(TradePlan).filter(TradePlan.run_id == run_id).all()
        plan_counts = Counter(p.side for p in plans)
        run.result_json = json.dumps({
            "kind": "pipeline",
            "trigger": trigger,
            "stock_total": stock_total,
            "llm_models": len(llm_models),
            "ensembles": len(ensembles),
            "buy": 0,
            "sell": 0,
            "hold": action_counts.get("hold", 0),
            "trade_n": 0,
            "decision_n": len(decs),
            "candidate_n": len(plans),
            "candidate_buy": plan_counts.get("buy", 0),
            "candidate_sell": plan_counts.get("sell", 0),
            "excluded_ineligible": excluded_ineligible,
            "signal_buy": plan_counts.get("buy", 0),
            "signal_sell": plan_counts.get("sell", 0),
            "execution_mode": "human_confirmation",
        }, ensure_ascii=False)
        run.status = "done"
        _set_progress(phase="done", message="完成", agent="")
    except PipelineCancelled:
        cancelled = True
        logger.info("决策流程已协作取消 run_id=%s", run_id)
        run.status = "cancelled"
        run.error = "用户取消"
        _set_progress(phase="cancelled", message="已取消", agent="")
    except Exception as err:  # noqa: BLE001
        logger.exception("决策流程失败")
        run.status = "failed"
        run.error = str(err)
        _set_progress(phase="failed", message=str(err)[:200], agent="")
    finally:
        run.finished_at = datetime.now()
        db.commit()
        db.close()
        _run_lock = False
        _cancel_requested = False
        # 保留最后进度快照供前端短暂展示，run_id 仍在
        if not cancelled and run.status == "done":
            _set_progress(phase="idle", message="")
    return run_id
