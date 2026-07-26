"""全部 REST API 路由。"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..agents import engine
from ..data import market
from ..database import get_db
from ..models import (Account, AgentOutput, Decision, EquitySnapshot, Order,
                      Position, Run, Watchlist)
from ..trading import broker, portfolio

router = APIRouter(prefix="/api")


# ---------- 股池 ----------

class WatchlistAdd(BaseModel):
    code: str


@router.get("/watchlist")
def list_watchlist(db: Session = Depends(get_db)):
    items = []
    for item in db.query(Watchlist).order_by(Watchlist.created_at).all():
        quote = None
        try:
            quote = market.get_quote(item.code)
        except Exception:  # noqa: BLE001
            pass
        items.append({
            "id": item.id, "code": item.code, "name": item.name,
            "price": quote["price"] if quote else None,
            "pct_change": quote["pct_change"] if quote else None,
        })
    return items


@router.post("/watchlist")
def add_watchlist(body: WatchlistAdd, db: Session = Depends(get_db)):
    code = body.code.strip()
    if db.query(Watchlist).filter(Watchlist.code == code).first():
        raise HTTPException(400, "该股票已在股池中")
    try:
        quote = market.validate_code(code)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, f"行情数据获取失败: {err}") from err
    if quote is None:
        raise HTTPException(400, "无效代码(仅支持沪深主板/创业板/科创板普通 A 股,不含 ST)")
    item = Watchlist(code=code, name=quote["name"])
    db.add(item)
    db.commit()
    return {"id": item.id, "code": code, "name": quote["name"]}


@router.delete("/watchlist/{code}")
def remove_watchlist(code: str, db: Session = Depends(get_db)):
    item = db.query(Watchlist).filter(Watchlist.code == code).first()
    if item is None:
        raise HTTPException(404, "不在股池中")
    db.delete(item)
    db.commit()
    return {"ok": True}


# ---------- 账户 ----------

@router.get("/portfolio")
def get_portfolio(db: Session = Depends(get_db)):
    eq = portfolio.total_equity(db)
    positions = []
    for pos in db.query(Position).all():
        quote = None
        try:
            quote = market.get_quote(pos.code)
        except Exception:  # noqa: BLE001
            pass
        price = quote["price"] if quote else pos.avg_cost
        value = pos.total_qty * price
        pnl = (price - pos.avg_cost) * pos.total_qty
        positions.append({
            "code": pos.code, "name": pos.name,
            "total_qty": pos.total_qty, "available_qty": pos.available_qty,
            "avg_cost": round(pos.avg_cost, 3), "price": price,
            "market_value": round(value, 2), "pnl": round(pnl, 2),
            "pnl_pct": round((price / pos.avg_cost - 1) * 100, 2) if pos.avg_cost else 0,
            "pct_change": quote["pct_change"] if quote else None,
        })
    pnl_total = eq["total_equity"] - eq["initial_cash"]
    return {
        **eq,
        "total_pnl": round(pnl_total, 2),
        "total_pnl_pct": round(pnl_total / eq["initial_cash"] * 100, 2) if eq["initial_cash"] else 0,
        "positions": positions,
    }


@router.post("/account/reset")
def reset_account(db: Session = Depends(get_db)):
    db.query(Position).delete()
    db.query(Order).delete()
    db.query(EquitySnapshot).delete()
    db.query(Decision).delete()
    db.query(AgentOutput).delete()
    db.query(Run).delete()
    account = db.query(Account).first()
    if account:
        account.cash = account.initial_cash
    db.commit()
    return {"ok": True}


@router.get("/equity-curve")
def equity_curve(db: Session = Depends(get_db)):
    snaps = db.query(EquitySnapshot).order_by(EquitySnapshot.created_at).all()
    account = broker.get_account(db)
    points = [{
        "time": snap.created_at.strftime("%Y-%m-%d %H:%M"),
        "equity": snap.total_equity,
        "pct": round((snap.total_equity / account.initial_cash - 1) * 100, 2),
    } for snap in snaps]

    hs300 = []
    if snaps:
        try:
            df = market.get_hs300_history()
            start_date = snaps[0].created_at.strftime("%Y-%m-%d")
            df = df[df["日期"].astype(str) >= start_date]
            if not df.empty:
                base = float(df.iloc[0]["收盘"])
                hs300 = [{
                    "time": str(row["日期"]),
                    "pct": round((float(row["收盘"]) / base - 1) * 100, 2),
                } for _, row in df.iterrows()]
        except Exception:  # noqa: BLE001
            pass
    return {"equity": points, "hs300": hs300}


# ---------- 运行 ----------

@router.post("/runs/trigger")
def trigger_run(background: BackgroundTasks, db: Session = Depends(get_db)):
    if engine._run_lock:
        raise HTTPException(409, "已有决策流程在运行中")
    if not db.query(Watchlist).first() and not db.query(Position).first():
        raise HTTPException(400, "股池为空,请先添加自选股")
    background.add_task(engine.run_pipeline, "manual")
    return {"ok": True, "message": "决策流程已在后台启动"}


@router.get("/runs")
def list_runs(db: Session = Depends(get_db)):
    runs = db.query(Run).order_by(Run.id.desc()).limit(50).all()
    result = []
    for run in runs:
        decisions = db.query(Decision).filter(Decision.run_id == run.id).all()
        result.append({
            "id": run.id, "trigger": run.trigger, "status": run.status,
            "error": run.error,
            "started_at": run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": run.finished_at.strftime("%Y-%m-%d %H:%M:%S") if run.finished_at else None,
            "summary": [{"code": d.code, "name": d.name, "action": d.action} for d in decisions],
        })
    return result


@router.get("/runs/{run_id}")
def run_detail(run_id: int, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run 不存在")
    outputs = db.query(AgentOutput).filter(AgentOutput.run_id == run_id).all()
    decisions = db.query(Decision).filter(Decision.run_id == run_id).all()
    stocks: dict[str, dict] = {}
    for out in outputs:
        stock = stocks.setdefault(out.code, {"code": out.code, "agents": [], "decision": None})
        stock["agents"].append({
            "agent": out.agent, "input_summary": out.input_summary, "output": out.output,
            "created_at": out.created_at.strftime("%H:%M:%S"),
        })
    for dec in decisions:
        stock = stocks.setdefault(dec.code, {"code": dec.code, "agents": [], "decision": None})
        stock["name"] = dec.name
        stock["decision"] = {
            "action": dec.action, "target_position_pct": dec.target_position_pct,
            "confidence": dec.confidence, "reason": dec.reason, "error": dec.error,
        }
    return {
        "id": run.id, "trigger": run.trigger, "status": run.status, "error": run.error,
        "started_at": run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": run.finished_at.strftime("%Y-%m-%d %H:%M:%S") if run.finished_at else None,
        "stocks": list(stocks.values()),
    }


# ---------- 订单 ----------

@router.get("/orders")
def list_orders(db: Session = Depends(get_db)):
    orders = db.query(Order).order_by(Order.id.desc()).limit(200).all()
    return [{
        "id": order.id, "run_id": order.run_id, "code": order.code, "name": order.name,
        "side": order.side, "price": order.price, "qty": order.qty,
        "amount": order.amount, "fee": order.fee, "status": order.status,
        "reject_reason": order.reject_reason,
        "created_at": order.created_at.strftime("%Y-%m-%d %H:%M:%S"),
    } for order in orders]


# ---------- 状态 ----------

@router.get("/status")
def status():
    from .. import scheduler

    return {
        "running": engine._run_lock,
        "schedule_enabled": scheduler.is_enabled(),
        "schedule_times": scheduler.schedule_times(),
        "next_run": scheduler.next_run_time(),
    }
