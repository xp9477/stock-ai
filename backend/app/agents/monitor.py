"""盘中持仓监控。

监控只产生告警或待人工处理的复审建议，永远不能创建订单、改变现金或
改变持仓。-8%/-15% 等数值是事件触发候选参数，不是自动交易策略。
"""
import json
import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from ..data import market
from ..data.indicators import indicators_text
from ..models import Model, MonitorEvent, Position
from ..runtime_settings import get_setting
from ..trading import broker
from . import engine, llm

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


def _today_events(db: Session, model_pk: int, code: str,
                  actions: list[str] | None = None) -> list[MonitorEvent]:
    today_start = datetime.combine(date.today(), datetime.min.time())
    q = (db.query(MonitorEvent)
         .filter(MonitorEvent.model_pk == model_pk, MonitorEvent.code == code,
                 MonitorEvent.created_at >= today_start))
    if actions:
        q = q.filter(MonitorEvent.action.in_(actions))
    return q.order_by(MonitorEvent.id.desc()).all()


def should_review(db: Session, model_pk: int, code: str, pnl_pct: float) -> str | None:
    """返回触发类型(stop_loss/take_profit/deep_loss)或 None。"""
    deep_loss_pct = float(get_setting("risk.deep_loss_pct"))
    stop_loss_pct = float(get_setting("risk.stop_loss_review_pct"))
    take_profit_pct = float(get_setting("risk.take_profit_review_pct"))
    if pnl_pct <= deep_loss_pct:
        reviewed = _today_events(db, model_pk, code, ["alert", "review_hold", "review_required"])
        if any(e.trigger == "deep_loss" for e in reviewed):
            return None
        return "deep_loss"
    reviews = _today_events(db, model_pk, code, ["alert", "review_hold", "review_required"])
    reviewed_triggers = {e.trigger for e in reviews}
    if pnl_pct <= stop_loss_pct:
        return None if "stop_loss" in reviewed_triggers else "stop_loss"
    if pnl_pct >= take_profit_pct:
        return None if "take_profit" in reviewed_triggers else "take_profit"
    return None


def review_position(db: Session, pos: Position, price: float, pct_change: float,
                    pnl_pct: float, trigger: str) -> MonitorEvent:
    """生成告警/复审建议；无论配置和 LLM 输出如何都不成交。"""
    if bool(get_setting("risk.shallow_line_alert_only")) and trigger in ("stop_loss", "take_profit"):
        label = "止损警戒" if trigger == "stop_loss" else "止盈警戒"
        detail = (f"{label}: 浮盈亏 {pnl_pct:+.1%}，仅告警不自动卖出；"
                  f"留给日频策略处理（分层3）")
        event = MonitorEvent(model_pk=pos.model_pk, code=pos.code, name=pos.name,
                             pnl_pct=round(pnl_pct, 4), trigger=trigger,
                             action="alert", detail=detail)
        db.add(event)
        db.commit()
        from ..notifications import notify_monitor_event
        notify_monitor_event(event)
        return event

    model_id = _review_model_id(db, pos.model_pk)
    trigger_label = {"stop_loss": "止损警戒", "take_profit": "止盈警戒",
                     "deep_loss": "深度亏损"}[trigger]
    try:
        kline_text = indicators_text(market.get_daily_kline(pos.code), days=5)
    except Exception as err:  # noqa: BLE001
        kline_text = f"(K线获取失败: {err})"
    review_input = (
        f"触发原因: {trigger_label}\n"
        f"股票: {pos.name}({pos.code})\n"
        f"当时买入理由: {pos.buy_reason or '(未记录)'}\n"
        f"持仓成本: {pos.avg_cost:.2f} 元, 现价: {price:.2f} 元, "
        f"浮动盈亏: {pnl_pct:+.1%}\n"
        f"持有 {pos.total_qty} 股 (今日可卖 {pos.available_qty} 股)\n\n"
        f"【近期走势】\n{kline_text}\n\n"
        "只依据以上当前可审计事实给出复审建议；不得声称已经下单。"
    )

    action = "review_hold"
    detail = ""
    decision = None
    if model_id:
        try:
            output = llm.chat(str(get_setting("prompt.review")), review_input,
                              model_id, retries=1)
            detail = output
            decision = llm.decide_with_fallback(output, model_id)
        except Exception as err:  # noqa: BLE001
            detail = f"复审 LLM 调用失败: {err}"
    else:
        detail = "无可用复审模型"

    if decision and decision["action"] == "sell":
        action = "review_required"
        detail += ("\n[LLM 建议卖出；仅生成待人工处理建议，未创建订单。"
                   f" 今日可卖 {pos.available_qty} 股]")

    event = MonitorEvent(model_pk=pos.model_pk, code=pos.code, name=pos.name,
                         pnl_pct=round(pnl_pct, 4), trigger=trigger,
                         action=action, detail=detail)
    db.add(event)
    db.commit()
    from ..notifications import notify_monitor_event
    notify_monitor_event(event)
    return event


def run_monitor() -> int:
    """扫描 AI 账户持仓并生成事件；返回事件数。"""
    if engine.is_running():
        logger.info("决策流程运行中,跳过本次监控")
        return 0

    # 非交易时段没有盘中事件意义，也避免把盘后脏价写入告警。
    if not market.is_trading_session():
        logger.info("非交易时段,跳过盘中监控")
        return 0

    from ..database import SessionLocal

    db = SessionLocal()
    count = 0
    try:
        official_model_pks = broker.enabled_official_strategy_ids(db)
        broker.settle_t1(db, model_pks=official_model_pks)
        from ..trading.broker_snapshot import (
            BrokerSnapshotError,
            reconcile_configured_broker_portfolio,
        )
        try:
            broker_reference = reconcile_configured_broker_portfolio(
                db, official_model_pks,
            )
            if broker_reference is not None:
                db.commit()
        except BrokerSnapshotError as exc:
            db.rollback()
            logger.warning("券商参考状态不可用，跳过本轮监控: %s", exc)
            return 0
        for pos in db.query(Position).all():
            model = db.get(Model, pos.model_pk)
            if model is None or not model.enabled:
                continue
            # Only the explicit official strategy has a live monitoring
            # lifecycle.  Legacy LLM/rule/extra-ensemble positions remain
            # read-only historical evidence and must not trigger new advice.
            if model.type != "ensemble" or not model.is_official_strategy:
                continue
            # 交易级行情：扶摇快照 + 同源日 K/成本校验，失败会写审计日志
            quote = market.get_trade_quote(pos.code, avg_cost=pos.avg_cost or None)
            if quote is None or not pos.avg_cost:
                continue
            price, pct_change = quote["price"], quote["pct_change"]
            pnl_pct = (price - pos.avg_cost) / pos.avg_cost
            trigger = should_review(db, pos.model_pk, pos.code, pnl_pct)
            if trigger is None:
                continue
            # 严重复审仍用日 K 交叉提示，但不论结果都不会自动交易。
            if trigger == "deep_loss":
                close = market.last_close_price(pos.code)
                if close and close > 0:
                    kline_pnl = (close - pos.avg_cost) / pos.avg_cost
                    deep = float(get_setting("risk.deep_loss_pct"))
                    if kline_pnl > deep:
                        logger.warning(
                            "深亏实时价与日K不一致,仅告警 %s realtime=%.2f%% kline=%.2f%% "
                            "price=%.4f close=%.4f source=%s",
                            pos.code, pnl_pct * 100, kline_pnl * 100,
                            price, close, quote.get("source"),
                        )
                        event = MonitorEvent(
                            model_pk=pos.model_pk, code=pos.code, name=pos.name,
                            pnl_pct=round(pnl_pct, 4), trigger="deep_loss",
                            action="alert",
                            detail=(
                                f"实时浮亏 {pnl_pct:+.1%} 但日K收盘浮亏 {kline_pnl:+.1%} "
                                f"未达阈值，疑似脏报价，仅告警 "
                                f"(price={price:.4f} source={quote.get('source')})"
                            ),
                        )
                        db.add(event)
                        db.commit()
                        from ..notifications import notify_monitor_event
                        notify_monitor_event(event)
                        count += 1
                        continue
            logger.info(
                "监控触发 %s: 模型#%s %s 盈亏 %.1f%% price=%.4f source=%s",
                trigger, pos.model_pk, pos.code, pnl_pct * 100,
                price, quote.get("source"),
            )
            review_position(db, pos, price, pct_change, pnl_pct, trigger)
            count += 1
    finally:
        db.close()
    return count
