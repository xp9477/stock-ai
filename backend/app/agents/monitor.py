"""盘中持仓监控:纯规则触发止盈/止损/深亏 LLM 复审,无任何无条件强制清仓。"""
import json
import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from ..config import settings
from ..data import market
from ..data.indicators import indicators_text
from ..models import Model, MonitorEvent, Position
from ..trading import broker
from . import engine, llm, prompts

logger = logging.getLogger(__name__)


def _review_model_id(db: Session, model_pk: int) -> str | None:
    """账户对应的复审模型:LLM 用自己,ensemble 用第一个成员。"""
    model = db.get(Model, model_pk)
    if model is None:
        return None
    if model.type == "llm":
        return model.model_id
    member_pks = json.loads(model.members or "[]")
    if not member_pks:
        return None
    member = db.get(Model, member_pks[0])
    return member.model_id if member else None


def _today_reviews(db: Session, model_pk: int, code: str) -> list[MonitorEvent]:
    today_start = datetime.combine(date.today(), datetime.min.time())
    return (db.query(MonitorEvent)
            .filter(MonitorEvent.model_pk == model_pk, MonitorEvent.code == code,
                    MonitorEvent.action.in_(["review_hold", "review_sell"]),
                    MonitorEvent.created_at >= today_start)
            .order_by(MonitorEvent.id.desc()).all())


def should_review(db: Session, model_pk: int, code: str, pnl_pct: float) -> str | None:
    """返回触发类型(stop_loss/take_profit/deep_loss)或 None。"""
    reviews = _today_reviews(db, model_pk, code)
    if pnl_pct <= settings.deep_loss_pct:
        # 深亏: 不限每日 1 次,但两次复审至少间隔 1 小时
        if reviews and reviews[0].created_at > datetime.now() - timedelta(hours=1):
            return None
        return "deep_loss"
    if pnl_pct <= settings.stop_loss_review_pct:
        return None if reviews else "stop_loss"
    if pnl_pct >= settings.take_profit_review_pct:
        return None if reviews else "take_profit"
    return None


def review_position(db: Session, pos: Position, price: float, pct_change: float,
                    pnl_pct: float, trigger: str) -> MonitorEvent:
    """单次 LLM 复审并执行结果。"""
    model_id = _review_model_id(db, pos.model_pk)
    trigger_label = {"stop_loss": "止损警戒", "take_profit": "止盈警戒",
                     "deep_loss": "深度亏损"}[trigger]
    try:
        kline_text = indicators_text(market.get_daily_kline(pos.code), days=5)
    except Exception as err:  # noqa: BLE001
        kline_text = f"(K线获取失败: {err})"
    reflections = engine.recent_reflections(db, pos.model_pk)
    review_input = (
        f"触发原因: {trigger_label}\n"
        f"股票: {pos.name}({pos.code})\n"
        f"当时买入理由: {pos.buy_reason or '(未记录)'}\n"
        f"持仓成本: {pos.avg_cost:.2f} 元, 现价: {price:.2f} 元, "
        f"浮动盈亏: {pnl_pct:+.1%}\n"
        f"持有 {pos.total_qty} 股 (今日可卖 {pos.available_qty} 股)\n\n"
        f"【近期走势】\n{kline_text}\n\n【历史经验教训】\n{reflections}"
    )

    action = "review_hold"
    detail = ""
    decision = None
    if model_id:
        try:
            output = llm.chat(prompts.REVIEW, review_input, model_id, retries=1)
            detail = output
            decision = llm.decide_with_fallback(output, model_id)
        except Exception as err:  # noqa: BLE001
            detail = f"复审 LLM 调用失败: {err}"
    else:
        detail = "无可用复审模型"

    if decision and decision["action"] == "sell":
        result = broker.sell(db, pos.model_pk, None, pos.code, pos.name,
                             price, pct_change, pos.available_qty)
        action = "review_sell" if result.ok else "review_hold"
        if not result.ok:
            detail += f"\n[卖出未成交: {result.reason}]"

    event = MonitorEvent(model_pk=pos.model_pk, code=pos.code, name=pos.name,
                         pnl_pct=round(pnl_pct, 4), trigger=trigger,
                         action=action, detail=detail)
    db.add(event)
    db.commit()
    return event


def run_monitor() -> int:
    """扫描所有账户持仓,触发复审。返回触发的事件数。"""
    if engine._run_lock:
        logger.info("决策流程运行中,跳过本次监控")
        return 0

    from ..database import SessionLocal

    db = SessionLocal()
    count = 0
    try:
        broker.settle_t1(db)
        for pos in db.query(Position).all():
            model = db.get(Model, pos.model_pk)
            if model is None or not model.enabled:
                continue
            quote = market.get_quote(pos.code)
            if quote is None or not pos.avg_cost:
                continue
            price, pct_change = quote["price"], quote["pct_change"]
            pnl_pct = (price - pos.avg_cost) / pos.avg_cost
            trigger = should_review(db, pos.model_pk, pos.code, pnl_pct)
            if trigger is None:
                continue
            logger.info("监控触发 %s: 模型#%s %s 盈亏 %.1f%%", trigger,
                        pos.model_pk, pos.code, pnl_pct * 100)
            review_position(db, pos, price, pct_change, pnl_pct, trigger)
            count += 1
    finally:
        db.close()
    return count
