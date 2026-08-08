"""盘中持仓监控（分层 3）:

- 深亏: 硬规则强制自动砍仓（不经 LLM）——仅交易时段 + 报价经校验
- 浅止损/止盈线: 仅告警；若关闭 alert_only 则走 LLM 复审
- 规则账户不参与盘中监控
"""
import json
import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from ..data import market
from ..data.indicators import indicators_text
from ..ledger import record_close_from_sell, strategy_key_for_model
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
    shallow_only = bool(get_setting("risk.shallow_line_alert_only"))

    if pnl_pct <= deep_loss_pct:
        # 深亏强制砍: 每日至多一次自动执行
        forced = _today_events(db, model_pk, code, ["force_sell", "review_sell"])
        if forced:
            return None
        return "deep_loss"
    if shallow_only:
        # 浅线仅告警：每日每线一次
        alerts = _today_events(db, model_pk, code, ["alert"])
        alert_triggers = {e.trigger for e in alerts}
        if pnl_pct <= stop_loss_pct and "stop_loss" not in alert_triggers:
            return "stop_loss"
        if pnl_pct >= take_profit_pct and "take_profit" not in alert_triggers:
            return "take_profit"
        return None
    # 兼容旧路径：浅线 LLM 复审
    reviews = _today_events(db, model_pk, code, ["review_hold", "review_sell"])
    if pnl_pct <= stop_loss_pct:
        return None if reviews else "stop_loss"
    if pnl_pct >= take_profit_pct:
        return None if reviews else "take_profit"
    return None


def _force_sell_deep_loss(db: Session, pos: Position, price: float,
                          pct_change: float, pnl_pct: float) -> MonitorEvent:
    """深亏硬规则：不经 LLM，能卖多少卖多少。"""
    avg_cost = pos.avg_cost
    model = db.get(Model, pos.model_pk)
    sk = strategy_key_for_model(pos.model_pk, model.type if model else "llm")
    result = broker.sell(db, pos.model_pk, None, pos.code, pos.name,
                         price, pct_change, pos.available_qty)
    if result.ok and result.order:
        record_close_from_sell(
            db, strategy_key=sk, model_pk=pos.model_pk, code=pos.code,
            name=pos.name, qty=result.order.qty, price=price,
            signal_source="deep_loss", order_id=result.order.id, avg_cost=avg_cost,
        )
        action = "force_sell"
        deep = float(get_setting("risk.deep_loss_pct"))
        detail = (f"深亏硬规则自动卖出 {result.order.qty} 股 @{price:.2f}，"
                  f"浮亏 {pnl_pct:+.1%}（阈值 {deep:.0%}）")
    else:
        action = "alert"
        detail = f"深亏触发但未能卖出: {result.reason}"
    event = MonitorEvent(model_pk=pos.model_pk, code=pos.code, name=pos.name,
                         pnl_pct=round(pnl_pct, 4), trigger="deep_loss",
                         action=action, detail=detail)
    db.add(event)
    db.commit()
    return event


def review_position(db: Session, pos: Position, price: float, pct_change: float,
                    pnl_pct: float, trigger: str) -> MonitorEvent:
    """浅线告警或（非 alert_only 时）LLM 复审。"""
    if trigger == "deep_loss" and bool(get_setting("risk.deep_loss_auto_execute")):
        return _force_sell_deep_loss(db, pos, price, pct_change, pnl_pct)

    if bool(get_setting("risk.shallow_line_alert_only")) and trigger in ("stop_loss", "take_profit"):
        label = "止损警戒" if trigger == "stop_loss" else "止盈警戒"
        detail = (f"{label}: 浮盈亏 {pnl_pct:+.1%}，仅告警不自动卖出；"
                  f"留给日频策略处理（分层3）")
        event = MonitorEvent(model_pk=pos.model_pk, code=pos.code, name=pos.name,
                             pnl_pct=round(pnl_pct, 4), trigger=trigger,
                             action="alert", detail=detail)
        db.add(event)
        db.commit()
        return event

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
            output = llm.chat(str(get_setting("prompt.review")), review_input,
                              model_id, retries=1)
            detail = output
            decision = llm.decide_with_fallback(output, model_id)
        except Exception as err:  # noqa: BLE001
            detail = f"复审 LLM 调用失败: {err}"
    else:
        detail = "无可用复审模型"

    if decision and decision["action"] == "sell":
        avg_cost = pos.avg_cost
        # 复审卖出也校验报价
        if market.sanitize_quote(
            {"price": price, "code": pos.code},
            code=pos.code, avg_cost=avg_cost,
        ) is None:
            action = "review_hold"
            detail += "\n[报价异常，拒绝卖出]"
        else:
            result = broker.sell(db, pos.model_pk, None, pos.code, pos.name,
                                 price, pct_change, pos.available_qty)
            action = "review_sell" if result.ok else "review_hold"
            if not result.ok:
                detail += f"\n[卖出未成交: {result.reason}]"
            elif result.order:
                model = db.get(Model, pos.model_pk)
                record_close_from_sell(
                    db, strategy_key=strategy_key_for_model(
                        pos.model_pk, model.type if model else "llm"),
                    model_pk=pos.model_pk, code=pos.code, name=pos.name,
                    qty=result.order.qty, price=price, signal_source="review",
                    order_id=result.order.id, avg_cost=avg_cost,
                )

    event = MonitorEvent(model_pk=pos.model_pk, code=pos.code, name=pos.name,
                         pnl_pct=round(pnl_pct, 4), trigger=trigger,
                         action=action, detail=detail)
    db.add(event)
    db.commit()
    return event


def run_monitor() -> int:
    """扫描 AI 账户持仓,触发复审/强制砍仓。返回触发的事件数。"""
    if engine.is_running():
        logger.info("决策流程运行中,跳过本次监控")
        return 0

    # 硬门禁：非交易日 / 非连续竞价时段绝不强平（防盘后脏报价）
    if not market.is_trading_session():
        logger.info("非交易时段,跳过盘中监控")
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
            # 规则臂按周频调仓，不参与盘中 LLM 复审/深亏砍仓
            if model.type == "rule":
                continue
            # 交易级行情：扶摇优先 + 腾讯交叉 + 成本/日K校验，失败会写 ERROR 审计日志
            quote = market.get_trade_quote(
                pos.code, avg_cost=pos.avg_cost or None, require_cross_check=True,
            )
            if quote is None or not pos.avg_cost:
                continue
            price, pct_change = quote["price"], quote["pct_change"]
            pnl_pct = (price - pos.avg_cost) / pos.avg_cost
            trigger = should_review(db, pos.model_pk, pos.code, pnl_pct)
            if trigger is None:
                continue
            # 深亏必须再次用日 K 印证（双保险）
            if trigger == "deep_loss":
                close = market.last_close_price(pos.code)
                if close and close > 0:
                    kline_pnl = (close - pos.avg_cost) / pos.avg_cost
                    deep = float(get_setting("risk.deep_loss_pct"))
                    if kline_pnl > deep:
                        logger.warning(
                            "深亏实时价与日K不一致,跳过强平 %s realtime=%.2f%% kline=%.2f%% "
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
                                f"未达阈值，疑似脏报价，仅告警不砍仓 "
                                f"(price={price:.4f} source={quote.get('source')})"
                            ),
                        )
                        db.add(event)
                        db.commit()
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
