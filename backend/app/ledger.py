"""决策账本写入与样本统计。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy.orm import Session

from .models import Order, Position, TradeLedger


def factsheet_hash(sheet: dict) -> str:
    raw = json.dumps(sheet, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def record_open(
    db: Session,
    *,
    strategy_key: str,
    model_pk: int | None,
    code: str,
    name: str,
    qty: int,
    price: float,
    signal_source: str,
    confidence: float = 0.0,
    reason: str = "",
    fs_hash: str = "",
    order_id: int | None = None,
    autocommit: bool = True,
) -> TradeLedger:
    row = TradeLedger(
        strategy_key=strategy_key,
        model_pk=model_pk,
        code=code,
        name=name,
        side="open",
        qty=qty,
        price=price,
        signal_source=signal_source,
        confidence=confidence,
        reason=reason[:2000],
        factsheet_hash=fs_hash,
        order_id=order_id,
        is_closed=False,
        opened_at=datetime.now(),
    )
    db.add(row)
    if autocommit:
        db.commit()
    else:
        db.flush()
    db.refresh(row)
    return row


def record_close_from_sell(
    db: Session,
    *,
    strategy_key: str,
    model_pk: int | None,
    code: str,
    name: str,
    qty: int,
    price: float,
    signal_source: str,
    order_id: int | None = None,
    avg_cost: float | None = None,
    autocommit: bool = True,
) -> TradeLedger:
    """记录卖出事件；只有仓位真正归零时才算一笔完整平仓。"""
    pnl = 0.0
    pnl_pct = 0.0
    if avg_cost and avg_cost > 0:
        pnl = (price - avg_cost) * qty
        pnl_pct = price / avg_cost - 1
    still_open = db.query(Position.id).filter(
        Position.model_pk == model_pk,
        Position.code == code,
        Position.total_qty > 0,
    ).first()
    position_closed = still_open is None
    opened_at = None
    hold_days = 0
    if position_closed:
        open_rows = (
            db.query(TradeLedger)
            .filter(
                TradeLedger.strategy_key == strategy_key,
                TradeLedger.model_pk == model_pk,
                TradeLedger.code == code,
                TradeLedger.side == "open",
                TradeLedger.is_closed.is_(False),
            )
            .order_by(TradeLedger.opened_at, TradeLedger.id)
            .all()
        )
        dates = [r.opened_at for r in open_rows if r.opened_at]
        opened_at = min(dates) if dates else None
        hold_days = max((datetime.now().date() - opened_at.date()).days, 0) if opened_at else 0
        partial_closes_query = db.query(TradeLedger).filter(
            TradeLedger.strategy_key == strategy_key,
            TradeLedger.model_pk == model_pk,
            TradeLedger.code == code,
            TradeLedger.side == "close",
            TradeLedger.is_closed.is_(False),
        )
        if opened_at:
            partial_closes_query = partial_closes_query.filter(
                TradeLedger.created_at >= opened_at)
        pnl += sum(float(r.pnl or 0.0) for r in partial_closes_query.all())
        open_cost = sum(float(r.qty or 0) * float(r.price or 0.0) for r in open_rows)
        if open_cost > 0:
            pnl_pct = pnl / open_cost
        for open_row in open_rows:
            open_row.is_closed = True
            open_row.closed_at = datetime.now()
            open_row.hold_days = max(
                (open_row.closed_at.date() - open_row.opened_at.date()).days, 0
            ) if open_row.opened_at else 0

    row = TradeLedger(
        strategy_key=strategy_key,
        model_pk=model_pk,
        code=code,
        name=name,
        side="close",
        qty=qty,
        price=price,
        signal_source=signal_source,
        order_id=order_id,
        is_closed=position_closed,
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 6),
        closed_at=datetime.now(),
        opened_at=opened_at,
        hold_days=hold_days,
    )
    db.add(row)
    if autocommit:
        db.commit()
    else:
        db.flush()
    db.refresh(row)
    return row


def closed_trade_count(db: Session, strategy_key: str | None = None, model_pk: int | None = None) -> int:
    q = db.query(TradeLedger).filter(TradeLedger.side == "close", TradeLedger.is_closed.is_(True))
    if strategy_key:
        q = q.filter(TradeLedger.strategy_key == strategy_key)
    if model_pk is not None:
        q = q.filter(TradeLedger.model_pk == model_pk)
    return q.count()


def strategy_key_for_model(model_pk: int, model_type: str = "llm") -> str:
    return f"{model_type}:{model_pk}"
