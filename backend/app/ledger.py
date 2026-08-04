"""决策账本写入与样本统计。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy.orm import Session

from .models import Order, TradeLedger


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
    db.commit()
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
) -> TradeLedger:
    """记录一笔平仓事件；若有成本则计算实现盈亏。"""
    pnl = 0.0
    pnl_pct = 0.0
    if avg_cost and avg_cost > 0:
        pnl = (price - avg_cost) * qty
        pnl_pct = price / avg_cost - 1
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
        is_closed=True,
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 6),
        closed_at=datetime.now(),
        opened_at=None,
    )
    db.add(row)
    db.commit()
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
