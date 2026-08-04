"""规则组前瞻交易：S2 周频前 N 等权 + 池内等权锚。

与 AI 组共用模拟撮合 / 账户 / 排行榜；零 LLM。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from ..data import fuyao_client as fuyao
from ..factors.panel import latest_factor_snapshot
from ..factors.score import select_top_n
from ..ledger import record_close_from_sell, record_open, strategy_key_for_model
from ..models import Model, Position, Watchlist
from ..trading import broker, portfolio

logger = logging.getLogger(__name__)

# model_id 约定（seed 写入）
S2_WEEKLY = "s2_weekly"
POOL_EQUAL = "pool_equal"
RULE_MODEL_IDS = (S2_WEEKLY, POOL_EQUAL)


def _pool(db: Session) -> list[tuple[str, str]]:
    """[(code, name), ...]"""
    return [(w.code, w.name) for w in db.query(Watchlist).order_by(Watchlist.id).all()]


def _prices(codes: list[str]) -> dict[str, dict[str, float]]:
    """code -> {price, pct_change, name?} 来自扶摇快照。"""
    if not codes:
        return {}
    if not fuyao.available():
        raise RuntimeError("FUYAO_API_KEY 未配置，规则组无法取价")
    out: dict[str, dict[str, float]] = {}
    # 批量接口可能有数量上限，按 50 切
    for i in range(0, len(codes), 50):
        chunk = codes[i:i + 50]
        items = fuyao.prices_snapshot(chunk)
        for it in items:
            code = fuyao.from_thscode(it["thscode"])
            price = it.get("last_price") or it.get("close_price")
            if not price or float(price) <= 0:
                continue
            pct = it.get("price_change_ratio_pct") or 0.0
            out[code] = {
                "price": float(price),
                "pct_change": float(pct),
                "name": it.get("name") or "",
            }
    return out


def _target_codes_s2(db: Session, codes: list[str]) -> list[str]:
    if not codes:
        return []
    snap = latest_factor_snapshot(codes)
    if snap.empty:
        logger.warning("S2 因子截面为空，本周跳过 s2_weekly")
        return []
    from ..runtime_settings import get_setting
    n = min(int(get_setting("factor.top_n")), len(codes))
    return select_top_n(snap, n=n)


def _target_codes_equal(codes: list[str]) -> list[str]:
    return list(codes)


def _equal_weights(targets: list[str]) -> dict[str, float]:
    if not targets:
        return {}
    w = 1.0 / len(targets)
    # 单票硬顶：若 1/n > max_position，压缩到上限并等权缩放
    from ..runtime_settings import get_setting as _gs
    cap = float(_gs("risk.max_position_pct"))
    if w > cap:
        w = cap
    return {c: w for c in targets}


def _rebalance_to_weights(
    db: Session,
    model: Model,
    weights: dict[str, float],
    names: dict[str, str],
    prices: dict[str, dict[str, float]],
    signal_source: str,
) -> dict[str, Any]:
    """将账户调到目标权重（先卖后买）。"""
    model_pk = model.id
    sk = strategy_key_for_model(model_pk, "rule")
    broker.settle_t1(db)

    sells: list[dict] = []
    buys: list[dict] = []
    skipped: list[str] = []

    # 1) 清掉不在目标中的持仓
    positions = db.query(Position).filter(Position.model_pk == model_pk).all()
    for pos in list(positions):
        if pos.code in weights:
            continue
        px = prices.get(pos.code)
        if not px:
            skipped.append(f"sell {pos.code}: no price")
            continue
        avg_cost = pos.avg_cost
        result = broker.sell(
            db, model_pk, None, pos.code, pos.name,
            px["price"], px["pct_change"], pos.available_qty,
        )
        if result.ok and result.order:
            record_close_from_sell(
                db, strategy_key=sk, model_pk=model_pk, code=pos.code,
                name=pos.name, qty=result.order.qty, price=px["price"],
                signal_source=signal_source, order_id=result.order.id,
                avg_cost=avg_cost,
            )
            sells.append({"code": pos.code, "qty": result.order.qty, "price": px["price"]})
        else:
            skipped.append(f"sell {pos.code}: {result.reason}")

    # 2) 按目标权重调仓
    eq = portfolio.total_equity(db, model_pk)
    total = eq["total_equity"] or settings.initial_cash

    for code, target_pct in weights.items():
        px = prices.get(code)
        if not px:
            skipped.append(f"adjust {code}: no price")
            continue
        name = names.get(code) or px.get("name") or code
        price = px["price"]
        pos = broker.get_position(db, model_pk, code)
        current_val = (pos.total_qty * price) if pos else 0.0
        target_val = target_pct * total
        delta = target_val - current_val

        # 小于约半手的金额变动忽略
        if abs(delta) < price * 50:
            continue

        if delta < 0 and pos:
            qty = int(abs(delta) / price / 100) * 100
            if target_pct <= 0.005:
                qty = pos.available_qty
            if qty <= 0:
                continue
            qty = min(qty, pos.available_qty)
            if qty <= 0:
                skipped.append(f"sell {code}: T+1 or lot")
                continue
            avg_cost = pos.avg_cost
            result = broker.sell(
                db, model_pk, None, code, name, price, px["pct_change"], qty,
            )
            if result.ok and result.order:
                record_close_from_sell(
                    db, strategy_key=sk, model_pk=model_pk, code=code, name=name,
                    qty=result.order.qty, price=price, signal_source=signal_source,
                    order_id=result.order.id, avg_cost=avg_cost,
                )
                sells.append({"code": code, "qty": result.order.qty, "price": price})
            else:
                skipped.append(f"sell {code}: {result.reason}")
        elif delta > 0:
            # 刷新权益（卖出后现金变了）
            eq = portfolio.total_equity(db, model_pk)
            total = eq["total_equity"] or total
            target_val = target_pct * total
            pos = broker.get_position(db, model_pk, code)
            current_val = (pos.total_qty * price) if pos else 0.0
            buy_amount = max(target_val - current_val, 0)
            if buy_amount < price * 100:
                continue
            result = broker.buy(
                db, model_pk, None, code, name, price, px["pct_change"],
                buy_amount, reason=f"{signal_source} rebalance target {target_pct:.1%}",
            )
            if result.ok and result.order:
                record_open(
                    db, strategy_key=sk, model_pk=model_pk, code=code, name=name,
                    qty=result.order.qty, price=price, signal_source=signal_source,
                    confidence=1.0, reason=f"target_pct={target_pct:.3f}",
                    order_id=result.order.id,
                )
                buys.append({"code": code, "qty": result.order.qty, "price": price})
            else:
                skipped.append(f"buy {code}: {result.reason}")

    portfolio.snapshot_equity(db, model_pk)
    eq = portfolio.total_equity(db, model_pk)
    return {
        "model_pk": model_pk,
        "name": model.name,
        "model_id": model.model_id,
        "targets": weights,
        "sells": sells,
        "buys": buys,
        "skipped": skipped,
        "equity": eq,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def rebalance_strategy(db: Session, model_id: str) -> dict[str, Any]:
    """对单个规则策略调仓。"""
    model = (
        db.query(Model)
        .filter(Model.type == "rule", Model.model_id == model_id, Model.enabled.is_(True))
        .first()
    )
    if model is None:
        raise ValueError(f"规则策略不存在或未启用: {model_id}")

    pool = _pool(db)
    if not pool:
        return {
            "model_id": model_id,
            "ok": False,
            "error": "股池为空",
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    codes = [c for c, _ in pool]
    names = {c: n for c, n in pool}
    prices = _prices(codes)
    # 持仓中可能有已移出池子的票，也要能卖
    for pos in db.query(Position).filter(Position.model_pk == model.id).all():
        if pos.code not in prices:
            try:
                extra = _prices([pos.code])
                prices.update(extra)
            except Exception:  # noqa: BLE001
                pass
            names.setdefault(pos.code, pos.name)

    if model_id == S2_WEEKLY:
        targets = _target_codes_s2(db, codes)
        signal = "s2_weekly"
    elif model_id == POOL_EQUAL:
        targets = _target_codes_equal(codes)
        signal = "pool_equal"
    else:
        raise ValueError(f"未知规则策略: {model_id}")

    # 目标必须有行情
    targets = [c for c in targets if c in prices]
    weights = _equal_weights(targets)
    if not weights:
        return {
            "model_id": model_id,
            "ok": False,
            "error": "无有效目标持仓（因子或行情不足）",
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    result = _rebalance_to_weights(db, model, weights, names, prices, signal)
    result["ok"] = True
    return result


def rebalance_all_rules(db: Session) -> dict[str, Any]:
    """全部启用规则策略调仓（周频任务入口）。"""
    results = []
    for mid in RULE_MODEL_IDS:
        model = (
            db.query(Model)
            .filter(Model.type == "rule", Model.model_id == mid, Model.enabled.is_(True))
            .first()
        )
        if model is None:
            continue
        try:
            results.append(rebalance_strategy(db, mid))
        except Exception as err:  # noqa: BLE001
            logger.exception("规则调仓失败 %s", mid)
            results.append({
                "model_id": mid,
                "ok": False,
                "error": str(err),
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
    return {
        "date": date.today().isoformat(),
        "results": results,
    }


def is_rebalance_day(day: date | None = None) -> bool:
    """默认周一调仓（与 factor_rebalance=W-MON 一致）。"""
    day = day or date.today()
    # Monday = 0
    return day.weekday() == 0
