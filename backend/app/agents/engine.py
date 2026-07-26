"""多模型决策引擎:每个 LLM 模型独立跑 分析师->辩论->交易员->风控 流水线,
之后合议组合按成员决策纯代码合成;含市场环境分析师与反思记忆。"""
import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..config import settings
from ..data import market
from ..data.indicators import indicators_text
from ..models import (AgentOutput, Decision, Model, Order, Position,
                      Reflection, Run, Watchlist)
from ..trading import broker, portfolio
from . import llm, prompts

logger = logging.getLogger(__name__)

MARKET_CODE = "MARKET"
REFLECT_CODE = "REFLECT"


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
        if pnl_pct <= settings.stop_loss_alert_pct:
            lines.append("⚠️ 警告: 该持仓浮亏已超过 10%,必须评估是否止损!")
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

def prepare_stock_inputs(code: str, name: str) -> dict:
    """采集个股数据文本,同一轮内所有模型共享。"""
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
        news_items = market.get_news(code)
        news_input = f"股票: {name}({code})\n近期新闻:\n" + "\n".join(
            f"- [{item['time']}] {item['title']}: {item['content']}" for item in news_items
        )
    except Exception as err:  # noqa: BLE001
        news_input = f"股票: {name}({code})\n\n(新闻数据获取失败: {err})"

    return {"tech": tech_input, "fund": fund_input, "news": news_input}


# ---------- 单模型流水线 ----------

def market_report(db: Session, run_id: int, model: Model) -> str:
    try:
        overview = market.market_overview_text()
    except Exception as err:  # noqa: BLE001
        overview = f"(大盘数据获取失败: {err})"
    report = llm.chat(prompts.MARKET, overview, model.model_id)
    _save_output(db, run_id, model.id, MARKET_CODE, "market", overview, report)
    return report


def analyze_stock(db: Session, run_id: int, model: Model, code: str, name: str,
                  inputs: dict, market_ctx: str, reflections: str) -> Decision:
    """对单只股票执行完整流水线(指定模型),返回落库后的最终决策。"""
    model_id = model.model_id

    tech_report = llm.chat(prompts.TECHNICAL, inputs["tech"], model_id)
    _save_output(db, run_id, model.id, code, "technical", inputs["tech"], tech_report)

    fund_report = llm.chat(prompts.FUNDAMENTAL, inputs["fund"], model_id)
    _save_output(db, run_id, model.id, code, "fundamental", inputs["fund"], fund_report)

    news_report = llm.chat(prompts.NEWS, inputs["news"], model_id)
    _save_output(db, run_id, model.id, code, "news", inputs["news"], news_report)

    reports = (
        f"【技术面报告】\n{tech_report}\n\n"
        f"【基本面报告】\n{fund_report}\n\n"
        f"【新闻情绪报告】\n{news_report}"
    )

    debate_log = ""
    for round_no in (1, 2):
        bull_input = f"{reports}\n\n【辩论记录】\n{debate_log or '(辩论开始,你先发言)'}"
        bull_view = llm.chat(prompts.BULL, bull_input, model_id)
        _save_output(db, run_id, model.id, code, f"bull_{round_no}", f"第{round_no}轮", bull_view)
        debate_log += f"\n多头(第{round_no}轮): {bull_view}\n"

        bear_input = f"{reports}\n\n【辩论记录】\n{debate_log}"
        bear_view = llm.chat(prompts.BEAR, bear_input, model_id)
        _save_output(db, run_id, model.id, code, f"bear_{round_no}", f"第{round_no}轮", bear_view)
        debate_log += f"\n空头(第{round_no}轮): {bear_view}\n"

    position_ctx = _position_context(db, model.id, code)
    trader_input = (
        f"股票: {name}({code})\n\n【大盘环境】\n{market_ctx}\n\n{reports}\n\n"
        f"【多空辩论】\n{debate_log}\n\n【账户状态】\n{position_ctx}\n\n"
        f"【历史经验教训】\n{reflections}"
    )
    trader_output = llm.chat(prompts.TRADER, trader_input, model_id)
    _save_output(db, run_id, model.id, code, "trader", position_ctx, trader_output)
    trader_decision = llm.parse_decision_json(trader_output)
    if trader_decision is None:
        trader_decision = {"action": "hold", "target_position_pct": 0.0,
                           "confidence": 0.0, "reason": "交易员输出解析失败,降级为持有"}

    risk_input = (
        f"股票: {name}({code})\n\n【大盘环境】\n{market_ctx}\n\n【分析师报告】\n{reports}\n\n"
        f"【交易员决策】\n{trader_output}\n\n【账户状态】\n{position_ctx}"
    )
    risk_output = llm.chat(prompts.RISK, risk_input, model_id)
    _save_output(db, run_id, model.id, code, "risk", "审核交易员决策", risk_output)
    final = llm.parse_decision_json(risk_output)
    if final is None:
        final = trader_decision

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
        output = llm.chat(prompts.REFLECT, review_input, model.model_id)
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
            final = {"action": "hold", "target_position_pct": 0.0, "confidence": 0.0,
                     "reason": f"合议无多数共识或共识为持有,观望 [{stance}]"}
        else:
            avg_pct = sum(d.target_position_pct for _, d in best_votes) / len(best_votes)
            avg_conf = sum(d.confidence for _, d in best_votes) / len(best_votes)
            final = {"action": best_action, "target_position_pct": avg_pct,
                     "confidence": avg_conf,
                     "reason": f"合议多数决 {best_action} [{stance}]"}
        finalize_decision(db, run_id, ensemble.id, code, name, final)


# ---------- 总入口 ----------

_run_lock = False


def run_pipeline(trigger: str = "manual") -> int | None:
    """执行一轮全量决策(所有启用模型)。返回 run_id;若上一轮未结束返回 None。"""
    global _run_lock
    if _run_lock:
        logger.warning("上一轮决策仍在运行,跳过本轮")
        return None
    _run_lock = True

    from ..database import SessionLocal

    db = SessionLocal()
    run = Run(trigger=trigger)
    db.add(run)
    db.commit()
    run_id = run.id
    try:
        broker.settle_t1(db)

        # 股池 + 所有账户持仓合并去重
        targets: dict[str, str] = {}
        for item in db.query(Watchlist).all():
            targets[item.code] = item.name
        for pos in db.query(Position).all():
            targets.setdefault(pos.code, pos.name)

        stock_inputs = {code: prepare_stock_inputs(code, name)
                        for code, name in targets.items()}

        llm_models = (db.query(Model)
                      .filter(Model.enabled.is_(True), Model.type == "llm").all())
        ensembles = (db.query(Model)
                     .filter(Model.enabled.is_(True), Model.type == "ensemble").all())

        for model in llm_models:
            try:
                market_ctx = market_report(db, run_id, model)
            except Exception as err:  # noqa: BLE001
                logger.exception("模型 %s 大盘分析失败", model.name)
                market_ctx = f"(大盘分析失败: {err})"
            reflections = recent_reflections(db, model.id)
            for code, name in targets.items():
                try:
                    analyze_stock(db, run_id, model, code, name,
                                  stock_inputs[code], market_ctx, reflections)
                except Exception as err:  # noqa: BLE001
                    logger.exception("模型 %s 分析 %s 失败", model.name, code)
                    db.add(Decision(run_id=run_id, model_pk=model.id, code=code,
                                    name=name, action="hold", reason="",
                                    error=str(err)))
                    db.commit()
            try:
                reflect(db, run_id, model)
            except Exception:  # noqa: BLE001
                logger.exception("模型 %s 反思失败", model.name)

        for ensemble in ensembles:
            try:
                synthesize_ensemble(db, run_id, ensemble, targets)
            except Exception:  # noqa: BLE001
                logger.exception("合议 %s 合成失败", ensemble.name)

        for model in db.query(Model).filter(Model.enabled.is_(True)).all():
            portfolio.snapshot_equity(db, model.id)
        run.status = "done"
    except Exception as err:  # noqa: BLE001
        logger.exception("决策流程失败")
        run.status = "failed"
        run.error = str(err)
    finally:
        run.finished_at = datetime.now()
        db.commit()
        db.close()
        _run_lock = False
    return run_id
