"""Factor snapshots, factsheets, backtests, and ledger statistics API."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import EquitySnapshot, Watchlist
from ..runtime_settings import get_setting

analytics_router = APIRouter()

@analytics_router.get("/factors/snapshot")
def factors_snapshot(db: Session = Depends(get_db)):
    """共享股池最新 S2 因子截面 + 综合分排名。"""
    from ..factors.panel import latest_factor_snapshot
    from ..factors.score import select_top_n

    codes = [w.code for w in db.query(Watchlist).all()]
    if not codes:
        return {"items": [], "top_n": [], "message": "股池为空"}
    snap = latest_factor_snapshot(codes)
    if snap.empty:
        return {"items": [], "top_n": [], "message": "因子计算失败或数据不足"}
    items = []
    for _, row in snap.iterrows():
        items.append({
            "code": row["code"],
            "date": str(row.get("date", ""))[:10],
            "mom_short": _f(row.get("mom_short")),
            "mom_mid": _f(row.get("mom_mid")),
            "low_vol": _f(row.get("low_vol")),
            "ep": _f(row.get("ep")),
            "bp": _f(row.get("bp")),
            "quality_roe": _f(row.get("quality_roe")),
            "rev_1m": _f(row.get("rev_1m")),
            "low_turn": _f(row.get("low_turn")),
            "growth_roe": _f(row.get("growth_roe")),
            "size_proxy": _f(row.get("size_proxy")),
            "score": _f(row.get("score")),
            "n_factors": int(row.get("n_factors") or 0),
        })
    items.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0)))
    from ..runtime_settings import get_setting
    top_n = int(get_setting("factor.top_n"))
    top = select_top_n(snap, n=top_n)
    return {"items": items, "top_n": top, "top_n_size": top_n}


@analytics_router.get("/factsheet/{code}")
def get_factsheet(code: str, db: Session = Depends(get_db)):
    from ..data.factsheet import build_factsheet, factsheet_text

    item = db.query(Watchlist).filter(Watchlist.code == code).first()
    name = item.name if item else ""
    peers = [w.code for w in db.query(Watchlist).all()]
    sheet = build_factsheet(code, name, peer_codes=peers)
    return {"sheet": sheet, "text": factsheet_text(sheet)}


class BacktestBody(BaseModel):
    years: int = 3
    top_n: int | None = None
    codes: list[str] | None = None


@analytics_router.post("/backtest/run")
def run_backtest(body: BacktestBody, db: Session = Depends(get_db)):
    """Run one recorded development experiment; keep its holdout sealed."""
    from datetime import date, datetime, time, timedelta, timezone

    import pandas as pd

    from ..backtest import evidence
    from ..backtest.validation import split_development_holdout
    from ..config import settings
    from ..factors.panel import build_factor_panel

    codes = sorted(set(body.codes or [w.code for w in db.query(Watchlist).all()]))
    if len(codes) < 2:
        raise HTTPException(400, "至少需要 2 只股票才能回测")
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=365 * max(1, min(body.years, 8)) + 60)
    panel = build_factor_panel(codes, start=start, end=end)
    if panel.empty:
        raise HTTPException(502, "无法构建因子面板（检查 FUYAO_API_KEY 与股池代码）")
    from ..runtime_settings import get_setting as _gs
    top_n = body.top_n or int(_gs("factor.top_n"))
    spec = {
        "name": "s2_factor_weekly",
        "mode": "factor_cross_section",
        "factors": [],
        "top_n": int(top_n),
        "rebalance": settings.factor_rebalance,
        "events": [],
    }
    try:
        development, holdout = split_development_holdout(panel)
        if development.empty or holdout.empty:
            raise ValueError("开发期/封存保留样本切分失败，数据不足")
        holdout_start = pd.to_datetime(holdout["date"], errors="coerce").min()
        if pd.isna(holdout_start):
            raise ValueError("封存保留样本缺少有效日期")
        development_cutoff = datetime.combine(
            holdout_start.date(), time.min, tzinfo=timezone.utc)
        experiment = evidence.run_reproducible_development(
            db,
            panel=development,
            spec=spec,
            universe=codes,
            data_cutoff_at=development_cutoff,
        )
        result_row = evidence.get_result(db, experiment.id, "development")
        persisted = evidence.result_dict(result_row)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {
        "codes": codes,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "experiment_id": experiment.id,
        "result_fingerprint": result_row.result_fingerprint,
        "data_cutoff_at": experiment.data_cutoff_at.isoformat(),
        "fingerprints": {
            "code": experiment.code_fingerprint,
            "config": experiment.config_fingerprint,
            "data": experiment.data_fingerprint,
            "universe": experiment.universe_fingerprint,
        },
        "equal_weight": persisted["anchor"],
        "factor_weekly": persisted["strategy"],
        "holdout_reserved": True,
    }


@analytics_router.get("/ledger/stats")
def ledger_stats(db: Session = Depends(get_db)):
    from ..ledger import closed_trade_count
    from ..models import TradeLedger

    total_closed = closed_trade_count(db)
    by_strategy: dict[str, int] = {}
    rows = (db.query(TradeLedger.strategy_key)
            .filter(TradeLedger.side == "close", TradeLedger.is_closed.is_(True)).all())
    for (sk,) in rows:
        by_strategy[sk] = by_strategy.get(sk, 0) + 1
    min_closed = int(get_setting("race.min_closed_trades"))
    min_days = int(get_setting("race.min_trade_days"))
    # 用权益快照的不同日历日近似「交易日」样本
    day_rows = db.query(EquitySnapshot.created_at).all()
    trade_days = len({
        (r[0].strftime("%Y-%m-%d") if r[0] else "")
        for r in day_rows if r[0]
    })
    return {
        "closed_trades": total_closed,
        "trade_days": trade_days,
        "by_strategy": by_strategy,
        "min_closed_trades": min_closed,
        "min_trade_days": min_days,
        "sample_ok": total_closed >= min_closed and trade_days >= min_days,
    }

def _f(value):
    try:
        if value is None:
            return None
        v = float(value)
        if v != v:
            return None
        return round(v, 6)
    except (TypeError, ValueError):
        return None
