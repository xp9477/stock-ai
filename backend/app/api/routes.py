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
            "model_id": model.model_id,
            "lane": "rule" if model.type == "rule" else "ai",
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
            "source": item.source, "miss_count": item.miss_count,
            "select_reason": item.select_reason,
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


@router.post("/watchlist/auto-select")
def trigger_auto_select(background: BackgroundTasks, db: Session = Depends(get_db)):
    from ..agents import selector as selector_mod
    from ..agents import engine as engine_mod

    if selector_mod._select_lock or engine_mod._run_lock:
        raise HTTPException(409, "决策或选股流程正在运行,请稍后")
    if not db.query(Model).filter(Model.enabled.is_(True), Model.type == "llm").first():
        raise HTTPException(400, "无启用的 LLM 模型")
    background.add_task(selector_mod.run_selector, "manual")
    return {"ok": True, "message": "自动选股已启动"}


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
    from ..models import TradeLedger

    for table in (Position, Order, EquitySnapshot, Decision, AgentOutput,
                  Reflection, MonitorEvent, TradeLedger, Run):
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
            "market_report": None, "reflection": None, "selector_report": None,
            "stocks": {}})
        if out.code == "MARKET":
            slot["market_report"] = out.output
        elif out.code == "REFLECT":
            slot["reflection"] = out.output
        elif out.code == "SELECT":
            slot["selector_report"] = out.output
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
            "market_report": None, "reflection": None, "selector_report": None,
            "stocks": {}})
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
    from ..runtime_settings import get_setting

    return {
        "running": engine._run_lock,
        "schedule_enabled": scheduler.is_enabled(),
        "schedule_times": scheduler.schedule_times(),
        "next_run": scheduler.next_run_time(),
        "pool_max": get_setting("selector.pool_max"),
        "factor_top_n": get_setting("factor.top_n"),
        "race_min_trade_days": get_setting("race.min_trade_days"),
        "race_min_closed_trades": get_setting("race.min_closed_trades"),
        "fuyao_configured": bool(str(get_setting("secrets.fuyao_api_key")).strip()),
        "llm_configured": bool(str(get_setting("secrets.llm_api_key")).strip()),
        "data_primary": "fuyao",
        "news_source": "rss",
    }


# ---------- 运行时设置 ----------

class SettingsUpdate(BaseModel):
    values: dict  # key -> value


class SettingsReset(BaseModel):
    keys: list[str] | None = None
    group: str | None = None


@router.get("/settings")
def get_settings_api(group: str | None = None, db: Session = Depends(get_db)):
    """列出配置注册表 + 当前有效值（按流水线分组）。"""
    from ..runtime_settings import list_settings
    from ..settings_registry import GROUPS

    items = list_settings(group=group, db=db)
    groups = GROUPS if group is None else [g for g in GROUPS if g["id"] == group]
    return {"groups": groups, "items": items}


@router.put("/settings")
def put_settings_api(body: SettingsUpdate, db: Session = Depends(get_db)):
    """批量更新配置覆盖。"""
    from .. import scheduler
    from ..runtime_settings import set_settings

    if not body.values:
        raise HTTPException(400, "values 不能为空")
    try:
        result = set_settings(body.values, db)
    except KeyError as err:
        raise HTTPException(400, str(err)) from err
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    if result.get("reload_scheduler"):
        try:
            scheduler.reload_jobs()
        except Exception as err:  # noqa: BLE001
            return {**result, "scheduler_reload_error": str(err)}
    return {"ok": True, **result}


@router.post("/settings/reset")
def reset_settings_api(body: SettingsReset = SettingsReset(),
                       db: Session = Depends(get_db)):
    """恢复默认（删除覆盖）。可按 keys 或 group。"""
    from .. import scheduler
    from ..runtime_settings import reset_settings

    if not body.keys and not body.group:
        # 全量重置需显式 group=all 防止误触 — 这里允许 keys=[] 时重置全部
        pass
    try:
        result = reset_settings(keys=body.keys, group=body.group, db=db)
    except KeyError as err:
        raise HTTPException(400, str(err)) from err
    if result.get("reload_scheduler"):
        try:
            scheduler.reload_jobs()
        except Exception as err:  # noqa: BLE001
            return {**result, "scheduler_reload_error": str(err)}
    return {"ok": True, **result}


# ---------- 规则组前瞻调仓 ----------

@router.post("/rules/rebalance")
def rules_rebalance(db: Session = Depends(get_db)):
    """手动触发全部规则策略调仓（S2 周频 + 池内等权）。"""
    from ..runtime_settings import get_setting
    from ..strategies.rule_runner import rebalance_all_rules

    if not str(get_setting("secrets.fuyao_api_key")).strip():
        raise HTTPException(400, "未配置扶摇 API Key（设置 → 密钥）")
    if not db.query(Watchlist).first():
        raise HTTPException(400, "股池为空")
    # 确保规则账户存在
    from ..seed import ensure_rule_strategies
    ensure_rule_strategies(db)
    db.commit()
    return rebalance_all_rules(db)


@router.post("/rules/rebalance/{model_id}")
def rules_rebalance_one(model_id: str, db: Session = Depends(get_db)):
    from ..runtime_settings import get_setting
    from ..strategies.rule_runner import rebalance_strategy

    if not str(get_setting("secrets.fuyao_api_key")).strip():
        raise HTTPException(400, "未配置扶摇 API Key（设置 → 密钥）")
    try:
        return rebalance_strategy(db, model_id)
    except ValueError as err:
        raise HTTPException(404, str(err)) from err
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, str(err)) from err


@router.get("/rules/status")
def rules_status(db: Session = Depends(get_db)):
    from ..strategies.rule_runner import RULE_MODEL_IDS, is_rebalance_day

    rows = []
    for mid in RULE_MODEL_IDS:
        model = db.query(Model).filter(Model.type == "rule", Model.model_id == mid).first()
        if model is None:
            rows.append({"model_id": mid, "exists": False})
            continue
        eq = portfolio.total_equity(db, model.id)
        pos_n = db.query(Position).filter(Position.model_pk == model.id).count()
        rows.append({
            "model_id": mid,
            "exists": True,
            "id": model.id,
            "name": model.name,
            "enabled": model.enabled,
            "total_equity": eq["total_equity"],
            "cash": eq["cash"],
            "position_count": pos_n,
            "pnl_pct": round((eq["total_equity"] / eq["initial_cash"] - 1) * 100, 2)
            if eq["initial_cash"] else 0,
        })
    return {
        "is_rebalance_day": is_rebalance_day(),
        "schedule": "周一 14:50（交易日）",
        "top_n": _rt_get("factor.top_n"),
        "strategies": rows,
    }


# ---------- 因子 / 回测 / 事实底稿 / 账本 ----------


@router.get("/factors/snapshot")
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


@router.get("/factsheet/{code}")
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


@router.post("/backtest/run")
def run_backtest(body: BacktestBody, db: Session = Depends(get_db)):
    """对股池（或指定 codes）跑：池内等权锚 + S2 周频前 N。"""
    from datetime import date, timedelta

    from ..backtest.engine import run_equal_weight_buyhold, run_factor_weekly
    from ..factors.panel import build_factor_panel

    codes = body.codes or [w.code for w in db.query(Watchlist).all()]
    if len(codes) < 2:
        raise HTTPException(400, "至少需要 2 只股票才能回测")
    start = date.today() - timedelta(days=365 * max(1, min(body.years, 8)) + 60)
    panel = build_factor_panel(codes, start=start, end=date.today())
    if panel.empty:
        raise HTTPException(502, "无法构建因子面板（检查 FUYAO_API_KEY 与股池代码）")
    # 需要 close 列
    if "close" not in panel.columns and "收盘" in panel.columns:
        panel = panel.rename(columns={"收盘": "close"})
    eq_bt = run_equal_weight_buyhold(panel)
    from ..runtime_settings import get_setting as _gs
    fac_bt = run_factor_weekly(panel, top_n=body.top_n or int(_gs("factor.top_n")))
    return {
        "codes": codes,
        "start": start.isoformat(),
        "equal_weight": eq_bt.to_dict(),
        "factor_weekly": fac_bt.to_dict(),
    }


@router.get("/ledger/stats")
def ledger_stats(db: Session = Depends(get_db)):
    from ..ledger import closed_trade_count
    from ..models import TradeLedger

    total_closed = closed_trade_count(db)
    by_strategy: dict[str, int] = {}
    rows = (db.query(TradeLedger.strategy_key)
            .filter(TradeLedger.side == "close", TradeLedger.is_closed.is_(True)).all())
    for (sk,) in rows:
        by_strategy[sk] = by_strategy.get(sk, 0) + 1
    min_closed = int(_rt_get("race.min_closed_trades"))
    return {
        "closed_trades": total_closed,
        "by_strategy": by_strategy,
        "min_closed_trades": min_closed,
        "min_trade_days": int(_rt_get("race.min_trade_days")),
        "sample_ok": total_closed >= min_closed,
    }


def _rt_get(key: str):
    from ..runtime_settings import get_setting
    return get_setting(key)


def _f(value):
    try:
        if value is None:
            return None
        v = float(value)
        if v != v:  # NaN
            return None
        return round(v, 6)
    except (TypeError, ValueError):
        return None

