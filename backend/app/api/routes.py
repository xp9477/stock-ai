"""全部 REST API 路由(多模型账户)。"""
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..agents import engine
from ..config import settings
from ..data import market
from ..database import get_db
from ..models import (Account, AgentOutput, Decision, EquitySnapshot, Model,
                      MonitorEvent, Order, Position, Reflection, Run, Watchlist)
from ..trading import broker, portfolio

router = APIRouter(prefix="/api")


# ---------- 模型管理 ----------

class ModelCreate(BaseModel):
    name: str
    type: str = "llm"  # llm / ensemble
    model_id: str = ""
    members: list[int] = []


class ModelUpdate(BaseModel):
    name: str | None = None
    model_id: str | None = None
    members: list[int] | None = None
    enabled: bool | None = None


@router.get("/models")
def list_models(db: Session = Depends(get_db)):
    result = []
    for model in db.query(Model).order_by(Model.id).all():
        eq = portfolio.total_equity(db, model.id)
        result.append({
            "id": model.id, "name": model.name, "model_id": model.model_id,
            "type": model.type, "members": json.loads(model.members or "[]"),
            "enabled": model.enabled,
            "total_equity": eq["total_equity"],
            "pnl_pct": round((eq["total_equity"] / eq["initial_cash"] - 1) * 100, 2)
            if eq["initial_cash"] else 0,
        })
    return result


@router.post("/models")
def create_model(body: ModelCreate, db: Session = Depends(get_db)):
    if db.query(Model).filter(Model.name == body.name).first():
        raise HTTPException(400, "名称已存在")
    if body.type == "llm" and not body.model_id:
        raise HTTPException(400, "LLM 模型必须填写 model_id")
    if body.type == "ensemble":
        if len(body.members) < 2:
            raise HTTPException(400, "合议组合至少选择 2 个成员模型")
        for pk in body.members:
            member = db.get(Model, pk)
            if member is None or member.type != "llm":
                raise HTTPException(400, f"成员 {pk} 不是有效的 LLM 模型")
    model = Model(name=body.name, type=body.type, model_id=body.model_id,
                  members=json.dumps(body.members))
    db.add(model)
    db.commit()
    from ..seed import ensure_account
    ensure_account(db, model.id)
    db.commit()
    return {"id": model.id}


@router.put("/models/{model_pk}")
def update_model(model_pk: int, body: ModelUpdate, db: Session = Depends(get_db)):
    model = db.get(Model, model_pk)
    if model is None:
        raise HTTPException(404, "模型不存在")
    if body.name is not None:
        model.name = body.name
    if body.model_id is not None:
        model.model_id = body.model_id
    if body.members is not None:
        if model.type == "ensemble" and len(body.members) < 2:
            raise HTTPException(400, "合议组合至少 2 个成员")
        model.members = json.dumps(body.members)
    if body.enabled is not None:
        model.enabled = body.enabled
    db.commit()
    return {"ok": True}


@router.delete("/models/{model_pk}")
def delete_model(model_pk: int, db: Session = Depends(get_db)):
    model = db.get(Model, model_pk)
    if model is None:
        raise HTTPException(404, "模型不存在")
    # 被合议组合引用的 LLM 模型不可删
    if model.type == "llm":
        for ens in db.query(Model).filter(Model.type == "ensemble").all():
            if model_pk in json.loads(ens.members or "[]"):
                raise HTTPException(400, f"被合议组合「{ens.name}」引用,请先移除")
    for table in (Account, Position, Order, Decision, AgentOutput,
                  EquitySnapshot, Reflection, MonitorEvent):
        db.query(table).filter(table.model_pk == model_pk).delete()
    db.delete(model)
    db.commit()
    return {"ok": True}


@router.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db)):
    rows = []
    for model in db.query(Model).filter(Model.enabled.is_(True)).order_by(Model.id).all():
        eq = portfolio.total_equity(db, model.id)
        snaps = (db.query(EquitySnapshot)
                 .filter(EquitySnapshot.model_pk == model.id)
                 .order_by(EquitySnapshot.created_at).all())
        max_drawdown = 0.0
        peak = 0.0
        for snap in snaps:
            peak = max(peak, snap.total_equity)
            if peak > 0:
                max_drawdown = min(max_drawdown, (snap.total_equity - peak) / peak)
        positions = db.query(Position).filter(Position.model_pk == model.id).count()
        rows.append({
            "id": model.id, "name": model.name, "type": model.type,
            "total_equity": eq["total_equity"],
            "pnl": round(eq["total_equity"] - eq["initial_cash"], 2),
            "pnl_pct": round((eq["total_equity"] / eq["initial_cash"] - 1) * 100, 2)
            if eq["initial_cash"] else 0,
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "position_count": positions,
        })
    rows.sort(key=lambda r: r["pnl_pct"], reverse=True)
    return rows


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
def get_portfolio(model_pk: int, db: Session = Depends(get_db)):
    if db.get(Model, model_pk) is None:
        raise HTTPException(404, "模型不存在")
    eq = portfolio.total_equity(db, model_pk)
    positions = []
    for pos in db.query(Position).filter(Position.model_pk == model_pk).all():
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
            "buy_reason": pos.buy_reason,
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
    """重置所有账户与全部记录。"""
    for table in (Position, Order, EquitySnapshot, Decision, AgentOutput,
                  Reflection, MonitorEvent, Run):
        db.query(table).delete()
    for account in db.query(Account).all():
        account.cash = settings.initial_cash
        account.initial_cash = settings.initial_cash
    db.commit()
    return {"ok": True}


@router.get("/equity-curve")
def equity_curve(db: Session = Depends(get_db)):
    """所有启用账户的收益率曲线 + 沪深300。"""
    series = []
    first_time = None
    for model in db.query(Model).filter(Model.enabled.is_(True)).order_by(Model.id).all():
        snaps = (db.query(EquitySnapshot)
                 .filter(EquitySnapshot.model_pk == model.id)
                 .order_by(EquitySnapshot.created_at).all())
        if not snaps:
            continue
        account = broker.get_account(db, model.id)
        if first_time is None or snaps[0].created_at < first_time:
            first_time = snaps[0].created_at
        series.append({
            "name": model.name, "type": model.type,
            "points": [{
                "time": snap.created_at.strftime("%Y-%m-%d %H:%M"),
                "pct": round((snap.total_equity / account.initial_cash - 1) * 100, 2),
            } for snap in snaps],
        })

    hs300 = []
    if first_time is not None:
        try:
            df = market.get_hs300_history()
            start_date = first_time.strftime("%Y-%m-%d")
            df = df[df["日期"].astype(str) >= start_date]
            if not df.empty:
                base = float(df.iloc[0]["收盘"])
                hs300 = [{
                    "time": str(row["日期"]),
                    "pct": round((float(row["收盘"]) / base - 1) * 100, 2),
                } for _, row in df.iterrows()]
        except Exception:  # noqa: BLE001
            pass
    return {"series": series, "hs300": hs300}


# ---------- 运行 ----------

@router.post("/runs/trigger")
def trigger_run(background: BackgroundTasks, db: Session = Depends(get_db)):
    if engine._run_lock:
        raise HTTPException(409, "已有决策流程在运行中")
    if not db.query(Watchlist).first() and not db.query(Position).first():
        raise HTTPException(400, "股池为空,请先添加自选股")
    background.add_task(engine.run_pipeline, "manual")
    return {"ok": True, "message": "全量决策流程已在后台启动"}


@router.get("/runs")
def list_runs(db: Session = Depends(get_db)):
    runs = db.query(Run).order_by(Run.id.desc()).limit(50).all()
    models = {m.id: m.name for m in db.query(Model).all()}
    result = []
    for run in runs:
        decisions = db.query(Decision).filter(Decision.run_id == run.id).all()
        result.append({
            "id": run.id, "trigger": run.trigger, "status": run.status,
            "error": run.error,
            "started_at": run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": run.finished_at.strftime("%Y-%m-%d %H:%M:%S") if run.finished_at else None,
            "summary": [{"model": models.get(d.model_pk, "?"), "code": d.code,
                         "name": d.name, "action": d.action} for d in decisions],
        })
    return result


@router.get("/runs/{run_id}")
def run_detail(run_id: int, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run 不存在")
    models = {m.id: m.name for m in db.query(Model).all()}
    outputs = db.query(AgentOutput).filter(AgentOutput.run_id == run_id).all()
    decisions = db.query(Decision).filter(Decision.run_id == run_id).all()

    by_model: dict[int, dict] = {}
    for out in outputs:
        slot = by_model.setdefault(out.model_pk, {
            "model_pk": out.model_pk, "model": models.get(out.model_pk, "?"),
            "market_report": None, "reflection": None, "stocks": {}})
        if out.code == "MARKET":
            slot["market_report"] = out.output
        elif out.code == "REFLECT":
            slot["reflection"] = out.output
        else:
            stock = slot["stocks"].setdefault(out.code, {"code": out.code,
                                                         "agents": [], "decision": None})
            stock["agents"].append({
                "agent": out.agent, "input_summary": out.input_summary,
                "output": out.output,
                "created_at": out.created_at.strftime("%H:%M:%S"),
            })
    for dec in decisions:
        slot = by_model.setdefault(dec.model_pk, {
            "model_pk": dec.model_pk, "model": models.get(dec.model_pk, "?"),
            "market_report": None, "reflection": None, "stocks": {}})
        stock = slot["stocks"].setdefault(dec.code, {"code": dec.code,
                                                     "agents": [], "decision": None})
        stock["name"] = dec.name
        stock["decision"] = {
            "action": dec.action, "target_position_pct": dec.target_position_pct,
            "confidence": dec.confidence, "reason": dec.reason, "error": dec.error,
        }
    result_models = []
    for slot in by_model.values():
        slot["stocks"] = list(slot["stocks"].values())
        result_models.append(slot)
    result_models.sort(key=lambda s: s["model_pk"])
    return {
        "id": run.id, "trigger": run.trigger, "status": run.status, "error": run.error,
        "started_at": run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": run.finished_at.strftime("%Y-%m-%d %H:%M:%S") if run.finished_at else None,
        "models": result_models,
    }


# ---------- 订单与监控事件 ----------

@router.get("/orders")
def list_orders(model_pk: int | None = None, db: Session = Depends(get_db)):
    query = db.query(Order)
    if model_pk is not None:
        query = query.filter(Order.model_pk == model_pk)
    orders = query.order_by(Order.id.desc()).limit(200).all()
    models = {m.id: m.name for m in db.query(Model).all()}
    return [{
        "id": order.id, "model": models.get(order.model_pk, "?"),
        "model_pk": order.model_pk, "run_id": order.run_id,
        "code": order.code, "name": order.name,
        "side": order.side, "price": order.price, "qty": order.qty,
        "amount": order.amount, "fee": order.fee, "status": order.status,
        "reject_reason": order.reject_reason,
        "created_at": order.created_at.strftime("%Y-%m-%d %H:%M:%S"),
    } for order in orders]


@router.get("/monitor-events")
def list_monitor_events(db: Session = Depends(get_db)):
    events = db.query(MonitorEvent).order_by(MonitorEvent.id.desc()).limit(100).all()
    models = {m.id: m.name for m in db.query(Model).all()}
    return [{
        "id": e.id, "model": models.get(e.model_pk, "?"), "model_pk": e.model_pk,
        "code": e.code, "name": e.name, "pnl_pct": round(e.pnl_pct * 100, 2),
        "trigger": e.trigger, "action": e.action, "detail": e.detail,
        "created_at": e.created_at.strftime("%Y-%m-%d %H:%M:%S"),
    } for e in events]


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
