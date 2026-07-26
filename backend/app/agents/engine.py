"""多 Agent 决策引擎:分析师 -> 多空辩论 -> 交易员 -> 风控 -> 执行。"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..config import settings
from ..data import market
from ..data.indicators import indicators_text
from ..models import AgentOutput, Decision, Position, Run, Watchlist
from ..trading import broker, portfolio
from . import llm, prompts

logger = logging.getLogger(__name__)


def _save_output(db: Session, run_id: int, code: str, agent: str,
                 input_summary: str, output: str):
    db.add(AgentOutput(run_id=run_id, code=code, agent=agent,
                       input_summary=input_summary[:2000], output=output))
    db.commit()


def _position_context(db: Session, code: str) -> str:
    eq = portfolio.total_equity(db)
    pos = db.query(Position).filter(Position.code == code).first()
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


def analyze_stock(db: Session, run_id: int, code: str, name: str) -> Decision:
    """对单只股票执行完整多 Agent 流水线,返回落库后的最终决策。"""

    # ---- 1. 三位分析师 ----
    try:
        kline = market.get_daily_kline(code)
        tech_input = f"股票: {name}({code})\n\n{indicators_text(kline)}"
    except Exception as err:  # noqa: BLE001
        tech_input = f"股票: {name}({code})\n\n(K线数据获取失败: {err})"
    tech_report = llm.chat(prompts.TECHNICAL, tech_input)
    _save_output(db, run_id, code, "technical", tech_input, tech_report)

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
    fund_report = llm.chat(prompts.FUNDAMENTAL, fund_input)
    _save_output(db, run_id, code, "fundamental", fund_input, fund_report)

    try:
        news_items = market.get_news(code)
        news_input = f"股票: {name}({code})\n近期新闻:\n" + "\n".join(
            f"- [{item['time']}] {item['title']}: {item['content']}" for item in news_items
        )
    except Exception as err:  # noqa: BLE001
        news_input = f"股票: {name}({code})\n\n(新闻数据获取失败: {err})"
    news_report = llm.chat(prompts.NEWS, news_input)
    _save_output(db, run_id, code, "news", news_input, news_report)

    reports = (
        f"【技术面报告】\n{tech_report}\n\n"
        f"【基本面报告】\n{fund_report}\n\n"
        f"【新闻情绪报告】\n{news_report}"
    )

    # ---- 2. 多空辩论 2 轮 ----
    debate_log = ""
    for round_no in (1, 2):
        bull_input = f"{reports}\n\n【辩论记录】\n{debate_log or '(辩论开始,你先发言)'}"
        bull_view = llm.chat(prompts.BULL, bull_input)
        _save_output(db, run_id, code, f"bull_{round_no}", f"第{round_no}轮", bull_view)
        debate_log += f"\n多头(第{round_no}轮): {bull_view}\n"

        bear_input = f"{reports}\n\n【辩论记录】\n{debate_log}"
        bear_view = llm.chat(prompts.BEAR, bear_input)
        _save_output(db, run_id, code, f"bear_{round_no}", f"第{round_no}轮", bear_view)
        debate_log += f"\n空头(第{round_no}轮): {bear_view}\n"

    # ---- 3. 交易员 ----
    position_ctx = _position_context(db, code)
    trader_input = (
        f"股票: {name}({code})\n\n{reports}\n\n"
        f"【多空辩论】\n{debate_log}\n\n【账户状态】\n{position_ctx}"
    )
    trader_output = llm.chat(prompts.TRADER, trader_input)
    _save_output(db, run_id, code, "trader", position_ctx, trader_output)
    trader_decision = llm.parse_decision_json(trader_output)
    if trader_decision is None:
        trader_decision = {"action": "hold", "target_position_pct": 0.0,
                           "confidence": 0.0, "reason": "交易员输出解析失败,降级为持有"}

    # ---- 4. 风控经理 ----
    risk_input = (
        f"股票: {name}({code})\n\n【分析师报告】\n{reports}\n\n"
        f"【交易员决策】\n{trader_output}\n\n【账户状态】\n{position_ctx}"
    )
    risk_output = llm.chat(prompts.RISK, risk_input)
    _save_output(db, run_id, code, "risk", "审核交易员决策", risk_output)
    final = llm.parse_decision_json(risk_output)
    if final is None:
        final = trader_decision

    # ---- 5. 硬性风控 + 执行 ----
    action, target_pct, risk_note = portfolio.apply_risk_limits(
        db, code, final["action"], final["target_position_pct"])
    reason = final["reason"]
    if risk_note:
        reason = f"{reason} [系统风控: {risk_note}]"

    decision = Decision(run_id=run_id, code=code, name=name, action=action,
                        target_position_pct=target_pct,
                        confidence=final["confidence"], reason=reason)
    db.add(decision)
    db.commit()

    portfolio.execute_decision(db, run_id, code, name, action, target_pct)
    return decision


_run_lock = False


def run_pipeline(trigger: str = "manual") -> int | None:
    """执行一轮完整决策。返回 run_id;若上一轮未结束返回 None。"""
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
        broker.settle_t1(db)  # 恢复 T+1 可卖数量(按新交易日)

        # 股池 + 当前持仓合并去重
        targets: dict[str, str] = {}
        for item in db.query(Watchlist).all():
            targets[item.code] = item.name
        for pos in db.query(Position).all():
            targets.setdefault(pos.code, pos.name)

        for code, name in targets.items():
            try:
                analyze_stock(db, run_id, code, name)
            except Exception as err:  # noqa: BLE001
                logger.exception("分析 %s 失败", code)
                db.add(Decision(run_id=run_id, code=code, name=name, action="hold",
                                reason="", error=str(err)))
                db.commit()

        portfolio.snapshot_equity(db)
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
