"""策略对照台：在跑臂的夏普/回撤/样本门槛/锚角色（P2）。

排序规则（产品 4.8）：
  主序夏普；副显示收益/回撤/超额；夺冠须 sample_ok 且夏普第一；未达标不授冠。
锚：pool_equal 固定 role=anchor，不参与挤占晋升名额。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ..backtest.metrics import compute_metrics, mark_sample_ok
from ..ledger import closed_trade_count, strategy_key_for_model
from ..models import EquitySnapshot, Model, Order, Position
from ..runtime_settings import get_setting
from ..trading import portfolio
from .rule_runner import POOL_EQUAL, RULE_MODEL_IDS, S2_WEEKLY, is_rebalance_day

# model_id → 生命周期/来源元数据（研究晋升 P3 再扩）
_META: dict[str, dict[str, str]] = {
    POOL_EQUAL: {
        "role": "anchor",
        "source": "builtin",
        "lifecycle": "live",
        "desc": "股池全部标的等权 · 躺平锚 · 不占晋升名额",
    },
    S2_WEEKLY: {
        "role": "competitive",
        "source": "builtin",
        "lifecycle": "live",
        "desc": "六因子截面 z 分等权 · 周频前 N · 可竞赛可退役",
    },
}


def _equity_series(db: Session, model_pk: int) -> pd.Series:
    snaps = (
        db.query(EquitySnapshot)
        .filter(EquitySnapshot.model_pk == model_pk)
        .order_by(EquitySnapshot.created_at)
        .all()
    )
    if not snaps:
        return pd.Series(dtype=float)
    # 按日去重取末日净值，避免同日多快照扭曲夏普
    by_day: dict[str, float] = {}
    for s in snaps:
        day = s.created_at.strftime("%Y-%m-%d") if s.created_at else ""
        by_day[day] = float(s.total_equity)
    if not by_day:
        return pd.Series(dtype=float)
    idx = sorted(by_day.keys())
    return pd.Series([by_day[d] for d in idx], index=pd.to_datetime(idx))


def _last_rebalance_at(db: Session, model_pk: int) -> str | None:
    row = (
        db.query(Order)
        .filter(Order.model_pk == model_pk, Order.status == "filled")
        .order_by(Order.id.desc())
        .first()
    )
    if row is None or not row.created_at:
        return None
    return row.created_at.strftime("%Y-%m-%d %H:%M")


def _closed_for_model(db: Session, model: Model) -> int:
    sk = strategy_key_for_model(model.id, "rule")
    return int(closed_trade_count(db, strategy_key=sk) or 0)


def _arm_payload(
    db: Session,
    model: Model | None,
    *,
    mid: str,
    role: str,
    source: str,
    lifecycle: str,
    desc: str,
    min_days: int,
    min_trades: int,
) -> dict[str, Any]:
    if model is None:
        return {
            "model_id": mid,
            "exists": False,
            "role": role,
            "source": source,
            "lifecycle": "missing",
            "desc": desc,
            "name": mid,
            "enabled": False,
        }
    eq = portfolio.total_equity(db, model.id)
    series = _equity_series(db, model.id)
    closed = _closed_for_model(db, model)
    metrics = compute_metrics(series, closed_trades=closed)
    metrics = mark_sample_ok(metrics, min_days, min_trades)
    pnl_pct = (
        round((eq["total_equity"] / eq["initial_cash"] - 1) * 100, 2)
        if eq["initial_cash"] else 0.0
    )
    pos_n = db.query(Position).filter(Position.model_pk == model.id).count()
    return {
        "model_id": mid,
        "exists": True,
        "id": model.id,
        "name": model.name,
        "enabled": model.enabled,
        "role": role,
        "source": source,
        "lifecycle": lifecycle if model.enabled else "disabled",
        "desc": desc,
        "total_equity": eq["total_equity"],
        "cash": eq["cash"],
        "position_count": pos_n,
        "pnl_pct": pnl_pct,
        "sharpe": metrics["sharpe"],
        "max_drawdown_pct": round(float(metrics["max_drawdown"]) * 100, 2),
        "ann_return_pct": round(float(metrics["ann_return"]) * 100, 2),
        "trade_days": metrics["n_days"],
        "closed_trades": closed,
        "sample_ok": metrics["sample_ok"],
        "last_rebalance_at": _last_rebalance_at(db, model.id),
        "excess_vs_anchor_pct": None,
        "crown": False,
    }


def build_strategy_board(db: Session) -> dict[str, Any]:
    min_days = int(get_setting("race.min_trade_days"))
    min_trades = int(get_setting("race.min_closed_trades"))
    top_n = int(get_setting("factor.top_n"))

    arms: list[dict[str, Any]] = []
    anchor_pnl: float | None = None

    # 内置规则
    for mid in RULE_MODEL_IDS:
        model = db.query(Model).filter(Model.type == "rule", Model.model_id == mid).first()
        meta = _META.get(mid, {
            "role": "competitive", "source": "builtin",
            "lifecycle": "live", "desc": "",
        })
        arm = _arm_payload(
            db, model, mid=mid,
            role=meta["role"], source=meta["source"],
            lifecycle=meta["lifecycle"], desc=meta["desc"],
            min_days=min_days, min_trades=min_trades,
        )
        if mid == POOL_EQUAL and arm.get("exists"):
            anchor_pnl = arm.get("pnl_pct")
        arms.append(arm)

    # 研究晋升臂
    for model in (
        db.query(Model)
        .filter(Model.type == "rule", Model.model_id.like("res_%"))
        .order_by(Model.id)
        .all()
    ):
        arms.append(_arm_payload(
            db, model, mid=model.model_id,
            role="competitive", source="research",
            lifecycle="live" if model.enabled else "retired",
            desc="研究晋升 · 规格驱动截面/事件",
            min_days=min_days, min_trades=min_trades,
        ))

    # 相对锚超额（百分点差）
    for arm in arms:
        if not arm.get("exists") or arm.get("role") == "anchor":
            continue
        if anchor_pnl is not None:
            arm["excess_vs_anchor_pct"] = round(
                float(arm.get("pnl_pct") or 0) - float(anchor_pnl), 2
            )

    # 排序：可竞赛臂按夏普降序；锚固定置顶或单独展示
    anchors = [a for a in arms if a.get("role") == "anchor"]
    competitors = [a for a in arms if a.get("role") != "anchor"]
    competitors.sort(
        key=lambda a: (
            0 if a.get("exists") else 1,
            -(float(a.get("sharpe") or 0) if a.get("exists") else 0),
            -(float(a.get("pnl_pct") or 0) if a.get("exists") else 0),
        )
    )

    # 授冠：sample_ok 中夏普最高的可竞赛臂
    eligible = [
        a for a in competitors
        if a.get("exists") and a.get("enabled") and a.get("sample_ok")
    ]
    champion_id = None
    if eligible:
        champ = max(eligible, key=lambda a: float(a.get("sharpe") or 0))
        champion_id = champ.get("model_id")
        for a in competitors:
            if a.get("model_id") == champion_id:
                a["crown"] = True

    ordered = anchors + competitors

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "race": {
            "min_trade_days": min_days,
            "min_closed_trades": min_trades,
        },
        "anchor_model_id": POOL_EQUAL,
        "champion_model_id": champion_id,
        "is_rebalance_day": is_rebalance_day(),
        "schedule": "周一 14:50（交易日）",
        "top_n": top_n,
        "sort": "sharpe_desc_sample_crown",
        "arms": ordered,
    }
