"""多模型决策引擎:每个 LLM 模型独立跑 分析师->辩论->交易员->风控 流水线,
之后合议组合按成员决策纯代码合成;含市场环境分析师与反思记忆。

P0：进程内进度快照 + 协作式取消（当前 Agent 结束后不再开新票/新 Agent）。
LLM 模型之间线程池并发（各自独立 Session），合议在全部 LLM 完成后串行合成。
"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..data import market
from ..data.indicators import indicators_text
from ..models import (AgentOutput, Decision, Model, Order, Position,
                      Reflection, Run, Watchlist)
from ..runtime_settings import get_setting
from ..trading import broker, portfolio
from . import llm

logger = logging.getLogger(__name__)

MARKET_CODE = "MARKET"
REFLECT_CODE = "REFLECT"

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


def _verbose() -> bool:
    try:
        return bool(get_setting("debug.pipeline_verbose"))
    except Exception:  # noqa: BLE001
        return False


def _save_output(db: Session, run_id: int, model_pk: int, code: str, agent: str,
                 input_summary: str, output: str):
    db.add(AgentOutput(run_id=run_id, model_pk=model_pk, code=code, agent=agent,
                       input_summary=input_summary[:2000], output=output))
    db.commit()


def _position_context(db: Session, model_pk: int, code: str) -> str:
    eq = portfolio.total_equity(db, model_pk)
    pos = broker.get_position(db, model_pk, code)
    lines = [
        f"总资产: {eq['total_equity']:.0f} 元",
        f"可用资金: {eq['cash']:.0f} 元",
        f"总仓位: {eq['market_value'] / eq['total_equity']:.1%}" if eq['total_equity'] else "总仓位: 0%",
    ]
    if pos:
        quote = market.get_quote(code)
        price = quote["price"] if quote else pos.avg_cost
        pnl_pct = (price - pos.avg_cost) / pos.avg_cost if pos.avg_cost else 0
        lines += [
            f"当前持有 {pos.total_qty} 股 (今日可卖 {pos.available_qty} 股)",
            f"持仓成本 {pos.avg_cost:.2f} 元, 现价 {price:.2f} 元, 浮动盈亏 {pnl_pct:.1%}",
            f"该股仓位占总资产 {pos.total_qty * price / eq['total_equity']:.1%}" if eq['total_equity'] else "",
        ]
        if pnl_pct <= float(get_setting("risk.stop_loss_alert_pct")):
            lines.append("⚠️ 警告: 该持仓浮亏已超过止损提示线,必须评估是否止损!")
    else:
        lines.append("当前未持有该股票")
    return "\n".join(filter(None, lines))


def recent_reflections(db: Session, model_pk: int, limit: int = 5) -> str:
    rows = (db.query(Reflection).filter(Reflection.model_pk == model_pk)
            .order_by(Reflection.id.desc()).limit(limit).all())
    if not rows:
        return "(暂无历史经验)"
    return "\n".join(f"- {r.content}" for r in reversed(rows))


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

    try:
        news_items = market.get_news(code, name=name)
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
    }


def _promoted_research_context() -> str:
    """已晋升研究策略摘要（只读提示，不强制跟单）。"""
    from ..database import SessionLocal
    from ..models import Model
    import json

    db = SessionLocal()
    try:
        rows = (
            db.query(Model)
            .filter(Model.type == "rule", Model.enabled.is_(True),
                    Model.model_id.like("res_%"))
            .order_by(Model.id)
            .limit(8)
            .all()
        )
        if not rows:
            return ""
        lines = ["【已验证研究策略（可选参考，非强制）】"]
        for m in rows:
            try:
                meta = json.loads(m.members or "{}")
            except json.JSONDecodeError:
                meta = {}
            spec = meta.get("spec") if isinstance(meta, dict) else {}
            if not isinstance(spec, dict):
                spec = {}
            lines.append(
                f"- {m.name}({m.model_id}): mode={spec.get('mode')} "
                f"factors={spec.get('factors')} top_n={spec.get('top_n')} "
                f"rebalance={spec.get('rebalance')} events={spec.get('events')}"
            )
        return "\n".join(lines)
    finally:
        db.close()


# ---------- 单模型流水线 ----------

def _step_agent(db: Session, run_id: int, model: Model, code: str, name: str,
                agent: str, system_key: str, user_content: str) -> str:
    """单 Agent：进度 → 取消检查 → LLM → 落库。"""
    _check_cancel()
    _set_progress(
        agent=agent,
        code=code,
        stock_name=name,
        message=f"{model.name} · {name or code} · {agent}",
        phase="agent",
    )
    if _verbose():
        logger.info("pipeline agent=%s model=%s code=%s", agent, model.name, code)
    t0 = time.perf_counter()
    report = llm.chat(str(get_setting(system_key)), user_content, model.model_id)
    elapsed = time.perf_counter() - t0
    _save_output(db, run_id, model.id, code, agent, user_content, report)
    if _verbose():
        logger.info("pipeline agent=%s done in %.1fs", agent, elapsed)
    _check_cancel()
    return report


def market_report(db: Session, run_id: int, model: Model) -> str:
    _check_cancel()
    _set_progress(
        phase="market", agent="market", code=MARKET_CODE, stock_name="大盘",
        message=f"{model.name} · 大盘环境",
    )
    try:
        overview = market.market_overview_text()
    except Exception as err:  # noqa: BLE001
        overview = f"(大盘数据获取失败: {err})"
    report = llm.chat(str(get_setting("prompt.market")), overview, model.model_id)
    _save_output(db, run_id, model.id, MARKET_CODE, "market", overview, report)
    _check_cancel()
    return report


def analyze_stock(db: Session, run_id: int, model: Model, code: str, name: str,
                  inputs: dict, market_ctx: str, reflections: str) -> Decision:
    """对单只股票执行完整流水线(指定模型),返回落库后的最终决策。"""
    _check_cancel()
    p = lambda k: f"prompt.{k}"  # noqa: E731

    tech_report = _step_agent(
        db, run_id, model, code, name, "technical", p("technical"), inputs["tech"])
    fund_report = _step_agent(
        db, run_id, model, code, name, "fundamental", p("fundamental"), inputs["fund"])
    news_report = _step_agent(
        db, run_id, model, code, name, "news", p("news"), inputs["news"])

    reports = (
        f"【技术面报告】\n{tech_report}\n\n"
        f"【基本面报告】\n{fund_report}\n\n"
        f"【新闻情绪报告】\n{news_report}"
    )

    debate_log = ""
    for round_no in (1, 2):
        bull_input = f"{reports}\n\n【辩论记录】\n{debate_log or '(辩论开始,你先发言)'}"
        bull_view = _step_agent(
            db, run_id, model, code, name, f"bull_{round_no}", p("bull"), bull_input)
        debate_log += f"\n多头(第{round_no}轮): {bull_view}\n"

        bear_input = f"{reports}\n\n【辩论记录】\n{debate_log}"
        bear_view = _step_agent(
            db, run_id, model, code, name, f"bear_{round_no}", p("bear"), bear_input)
        debate_log += f"\n空头(第{round_no}轮): {bear_view}\n"

    position_ctx = _position_context(db, model.id, code)
    factsheet_block = inputs.get("factsheet_text") or ""
    trader_input = (
        f"股票: {name}({code})\n\n【大盘环境】\n{market_ctx}\n\n"
        f"{factsheet_block}\n\n{reports}\n\n"
        f"【多空辩论】\n{debate_log}\n\n【账户状态】\n{position_ctx}\n\n"
        f"【历史经验教训】\n{reflections}"
    )
    trader_output = _step_agent(
        db, run_id, model, code, name, "trader", p("trader"), trader_input)
    trader_decision = llm.decide_with_fallback(trader_output, model.model_id)
    if trader_decision is None:
        trader_decision = {"action": "hold", "target_position_pct": 0.0,
                           "confidence": 0.0, "reason": "交易员输出解析失败,降级为持有"}

    risk_input = (
        f"股票: {name}({code})\n\n【大盘环境】\n{market_ctx}\n\n【分析师报告】\n{reports}\n\n"
        f"【交易员决策】\n{trader_output}\n\n【账户状态】\n{position_ctx}"
    )
    risk_output = _step_agent(
        db, run_id, model, code, name, "risk", p("risk"), risk_input)
    final = llm.decide_with_fallback(risk_output, model.model_id)
    if final is None:
        final = trader_decision

    # 取消点：撮合前再检查，避免半票乱下单
    _check_cancel()
    return finalize_decision(db, run_id, model.id, code, name, final)


def finalize_decision(db: Session, run_id: int, model_pk: int, code: str,
                      name: str, final: dict) -> Decision:
    """硬性风控 + 落库 + 执行,LLM 模型与合议组合共用。"""
    action, target_pct, risk_note = portfolio.apply_risk_limits(
        db, model_pk, code, final["action"], final["target_position_pct"])
    reason = final["reason"]
    if risk_note:
        reason = f"{reason} [系统风控: {risk_note}]"

    decision = Decision(run_id=run_id, model_pk=model_pk, code=code, name=name,
                        action=action, target_position_pct=target_pct,
                        confidence=final["confidence"], reason=reason)
    db.add(decision)
    db.commit()

    portfolio.execute_decision(db, model_pk, run_id, code, name, action,
                               target_pct, reason=final["reason"])
    return decision


# ---------- 反思 ----------

def reflect(db: Session, run_id: int, model: Model):
    """回顾该模型近 5 笔成交的决策理由 vs 实际盈亏,生成经验教训。"""
    orders = (db.query(Order)
              .filter(Order.model_pk == model.id, Order.status == "filled")
              .order_by(Order.id.desc()).limit(5).all())
    if not orders:
        return
    lines = []
    for order in reversed(orders):
        pos = broker.get_position(db, model.id, order.code)
        if pos:
            quote = market.get_quote(order.code)
            price = quote["price"] if quote else pos.avg_cost
            pnl = (price - pos.avg_cost) / pos.avg_cost if pos.avg_cost else 0
            outcome = f"仍持有,现浮动盈亏 {pnl:+.1%}"
        else:
            outcome = "已清仓"
        dec = (db.query(Decision)
               .filter(Decision.model_pk == model.id, Decision.code == order.code,
                       Decision.created_at <= order.created_at)
               .order_by(Decision.id.desc()).first())
        reason = dec.reason if dec else "(未记录)"
        lines.append(
            f"- {order.created_at:%m-%d} {'买入' if order.side == 'buy' else '卖出'} "
            f"{order.name} {order.qty}股 @{order.price},理由: {reason[:80]};结果: {outcome}")
    review_input = "近期交易记录:\n" + "\n".join(lines)
    try:
        output = llm.chat(str(get_setting("prompt.reflect")), review_input, model.model_id)
    except Exception as err:  # noqa: BLE001
        logger.warning("模型 %s 反思失败: %s", model.name, err)
        return
    _save_output(db, run_id, model.id, REFLECT_CODE, "reflect", review_input, output)
    if "样本不足" not in output:
        db.add(Reflection(model_pk=model.id, run_id=run_id, content=output.strip()[:1000]))
        db.commit()


# ---------- 合议合成 ----------

def synthesize_ensemble(db: Session, run_id: int, ensemble: Model,
                        targets: dict[str, str]):
    """按成员模型当轮最终决策合成合议决策(零 LLM 调用)。"""
    member_pks = json.loads(ensemble.members or "[]")
    if not member_pks:
        return
    for code, name in targets.items():
        votes = []
        for pk in member_pks:
            dec = (db.query(Decision)
                   .filter(Decision.run_id == run_id, Decision.model_pk == pk,
                           Decision.code == code, Decision.error == "")
                   .order_by(Decision.id.desc()).first())
            if dec:
                member = db.get(Model, pk)
                votes.append((member.name if member else str(pk), dec))
        if not votes:
            continue

        counts: dict[str, list] = {}
        for member_name, dec in votes:
            counts.setdefault(dec.action, []).append((member_name, dec))
        best_action, best_votes = max(counts.items(), key=lambda kv: len(kv[1]))
        # 多数票需过半,否则 hold(含 2 成员分歧/三方平票)
        if len(best_votes) * 2 <= len(votes):
            best_action, best_votes = "hold", []

        stance = ";".join(f"{name}:{dec.action}" for name, dec in votes)
        if best_action == "hold":
            # 多数 hold：保留目标仓位均值，便于 UI 区分「继续持有」与「观望」
            # 无多数共识时 best_votes 为空 → 观望 0
            if best_votes:
                avg_pct = sum(d.target_position_pct for _, d in best_votes) / len(best_votes)
                avg_conf = sum(d.confidence for _, d in best_votes) / len(best_votes)
                final = {
                    "action": "hold",
                    "target_position_pct": avg_pct,
                    "confidence": avg_conf,
                    "reason": f"合议多数决 hold [{stance}]",
                }
            else:
                final = {
                    "action": "hold",
                    "target_position_pct": 0.0,
                    "confidence": 0.0,
                    "reason": f"合议无多数共识,观望 [{stance}]",
                }
        else:
            avg_pct = sum(d.target_position_pct for _, d in best_votes) / len(best_votes)
            avg_conf = sum(d.confidence for _, d in best_votes) / len(best_votes)
            final = {"action": best_action, "target_position_pct": avg_pct,
                     "confidence": avg_conf,
                     "reason": f"合议多数决 {best_action} [{stance}]"}
        finalize_decision(db, run_id, ensemble.id, code, name, final)


# ---------- 单模型任务（供线程池调用，自建 Session） ----------


def _run_one_llm_model(
    *,
    run_id: int,
    model_pk: int,
    model_index: int,
    model_total: int,
    targets: dict[str, str],
    stock_inputs: dict[str, dict],
) -> None:
    """在独立线程/Session 中跑完一个 LLM 模型的全股池流水线。"""
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        _check_cancel()
        model = db.get(Model, model_pk)
        if model is None or not model.enabled:
            return
        _set_progress(
            phase="model", model_name=model.name, model_pk=model.id,
            model_index=model_index, model_total=model_total,
            message=f"模型 {model_index}/{model_total} · {model.name}",
        )
        try:
            market_ctx = market_report(db, run_id, model)
        except PipelineCancelled:
            raise
        except Exception as err:  # noqa: BLE001
            logger.exception("模型 %s 大盘分析失败", model.name)
            market_ctx = f"(大盘分析失败: {err})"
        reflections = recent_reflections(db, model.id)
        stock_total = len(targets)
        for si, (code, name) in enumerate(targets.items(), start=1):
            _check_cancel()
            _set_progress(
                phase="stock", model_name=model.name, model_pk=model.id,
                model_index=model_index, model_total=model_total,
                code=code, stock_name=name,
                stock_index=si, stock_total=stock_total,
                message=f"{model.name} · 股票 {si}/{stock_total} · {name}",
            )
            try:
                analyze_stock(db, run_id, model, code, name,
                              stock_inputs[code], market_ctx, reflections)
            except PipelineCancelled:
                raise
            except Exception as err:  # noqa: BLE001
                logger.exception("模型 %s 分析 %s 失败", model.name, code)
                db.add(Decision(run_id=run_id, model_pk=model.id, code=code,
                                name=name, action="hold", reason="",
                                error=str(err)))
                db.commit()
        _check_cancel()
        try:
            _set_progress(phase="reflect", agent="reflect",
                          message=f"{model.name} · 反思")
            reflect(db, run_id, model)
        except PipelineCancelled:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("模型 %s 反思失败", model.name)
    finally:
        db.close()


# ---------- 总入口 ----------


def run_pipeline(trigger: str = "manual") -> int | None:
    """执行一轮全量决策(所有启用模型)。返回 run_id;若上一轮未结束返回 None。"""
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
        broker.settle_t1(db)
        _check_cancel()

        # 股池 + 所有账户持仓合并去重
        targets: dict[str, str] = {}
        for item in db.query(Watchlist).all():
            targets[item.code] = item.name
        for pos in db.query(Position).all():
            targets.setdefault(pos.code, pos.name)

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

        llm_models = (db.query(Model)
                      .filter(Model.enabled.is_(True), Model.type == "llm").all())
        ensembles = (db.query(Model)
                     .filter(Model.enabled.is_(True), Model.type == "ensemble").all())
        model_total = len(llm_models)
        if not llm_models:
            run.status = "failed"
            run.error = "无启用的 LLM 模型，无法决策（合议依赖成员 LLM）"
            _set_progress(phase="failed", message=run.error, agent="")
            return run_id

        # 模型级并发：各 LLM 独立 Session，互不影响；合议等全部完成后再合成
        llm_ids = [m.id for m in llm_models]
        llm_names = {m.id: m.name for m in llm_models}
        max_workers = min(len(llm_ids), 4)
        _set_progress(
            phase="model", model_total=model_total,
            message=f"并发决策 {model_total} 个 LLM（workers={max_workers}）",
        )
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _run_one_llm_model,
                    run_id=run_id,
                    model_pk=mid,
                    model_index=i,
                    model_total=model_total,
                    targets=targets,
                    stock_inputs=stock_inputs,
                ): mid
                for i, mid in enumerate(llm_ids, start=1)
            }
            for fut in as_completed(futures):
                mid = futures[fut]
                try:
                    fut.result()
                except PipelineCancelled:
                    # 取消：尽量取消其余任务
                    for f in futures:
                        f.cancel()
                    raise
                except Exception as err:  # noqa: BLE001
                    logger.exception("模型并发任务失败 %s", llm_names.get(mid))
                    errors.append(f"{llm_names.get(mid)}: {err}")

        if errors and len(errors) == len(llm_ids):
            run.status = "failed"
            run.error = "全部 LLM 失败: " + "; ".join(errors)[:500]
            _set_progress(phase="failed", message=run.error[:200], agent="")
            return run_id

        _check_cancel()
        for ensemble in ensembles:
            _check_cancel()
            _set_progress(
                phase="ensemble", model_name=ensemble.name, model_pk=ensemble.id,
                message=f"合议合成 · {ensemble.name}",
            )
            try:
                synthesize_ensemble(db, run_id, ensemble, targets)
            except PipelineCancelled:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("合议 %s 合成失败", ensemble.name)

        for model in db.query(Model).filter(Model.enabled.is_(True)).all():
            portfolio.snapshot_equity(db, model.id)
        # 汇总买卖计数，供决策列表一眼可读
        from collections import Counter
        decs = db.query(Decision).filter(Decision.run_id == run_id).all()
        action_counts = Counter(d.action for d in decs)
        trade_n = action_counts.get("buy", 0) + action_counts.get("sell", 0)
        run.result_json = json.dumps({
            "kind": "pipeline",
            "trigger": trigger,
            "stock_total": stock_total,
            "llm_models": len(llm_models),
            "ensembles": len(ensembles),
            "buy": action_counts.get("buy", 0),
            "sell": action_counts.get("sell", 0),
            "hold": action_counts.get("hold", 0),
            "trade_n": trade_n,
            "decision_n": len(decs),
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
