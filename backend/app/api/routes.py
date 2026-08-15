"""全部 REST API 路由(多模型账户)。"""
import json
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agents import engine
from ..config import settings
from ..data import market
from ..database import get_db
from ..models import (Account, AgentOutput, Decision, EquitySnapshot, Model,
                      MonitorEvent, Order, Position, Reflection, Run, Watchlist)
from ..trading import broker, portfolio
from .research_routes import research_router
from .analytics_routes import analytics_router
from .trade_plan_routes import execution_intent_router, trade_plan_router

router = APIRouter(prefix="/api")


# ---------- 模型管理 ----------

class ModelCreate(BaseModel):
    name: str
    type: Literal["llm", "ensemble"] = "llm"
    model_id: str = ""
    members: list[int] = Field(default_factory=list)


class ModelUpdate(BaseModel):
    name: str | None = None
    model_id: str | None = None
    members: list[int] | None = None
    enabled: bool | None = None


def _validate_ensemble_members(db: Session, members: list[int]) -> list[int]:
    unique = list(dict.fromkeys(members))
    if len(unique) < 2:
        raise HTTPException(400, "合议组合至少选择 2 个不同成员模型")
    found = db.query(Model).filter(
        Model.id.in_(unique), Model.type == "llm").all()
    if {m.id for m in found} != set(unique):
        raise HTTPException(400, "合议成员必须全部是有效的 LLM 模型")
    return unique


@router.get("/models")
def list_models(db: Session = Depends(get_db)):
    result = []
    for model in db.query(Model).order_by(Model.id).all():
        # 只有正式 ensemble 策略拥有可运行资金；LLM 是顾问，rule 是历史证据。
        eq = (
            portfolio.total_equity(db, model.id)
            if model.type == "ensemble" and model.is_official_strategy
            else None
        )
        try:
            members_raw = json.loads(model.members or "[]")
        except json.JSONDecodeError:
            members_raw = []
        # 合议成员必须是 list[int]；研究晋升规则臂把 meta 存在 members，前端只展示 model_id
        members = members_raw if isinstance(members_raw, list) else []
        result.append({
            "id": model.id, "name": model.name, "model_id": model.model_id,
            "type": model.type, "members": members,
            "enabled": model.enabled,
            "is_official_strategy": model.is_official_strategy,
            "role": (
                "advisor" if model.type == "llm"
                else "strategy" if model.is_official_strategy
                else "historical_ensemble" if model.type == "ensemble"
                else "historical_evidence"
            ),
            "total_equity": eq["total_equity"] if eq else None,
            "pnl_pct": (
                round((eq["total_equity"] / eq["initial_cash"] - 1) * 100, 2)
                if eq and eq["initial_cash"] else None
            ),
        })
    return result


@router.post("/models")
def create_model(body: ModelCreate, db: Session = Depends(get_db)):
    if db.query(Model).filter(Model.name == body.name).first():
        raise HTTPException(400, "名称已存在")
    if body.type == "llm" and not body.model_id:
        raise HTTPException(400, "LLM 模型必须填写 model_id")
    if body.type == "ensemble":
        if db.query(Model).filter(Model.type == "ensemble").first():
            raise HTTPException(
                409,
                "只允许一个官方 ensemble 策略账户；请修改现有策略成员，不要新建资金账户",
            )
        body.members = _validate_ensemble_members(db, body.members)
    model = Model(
        name=body.name,
        type=body.type,
        model_id=body.model_id,
        members=json.dumps(body.members),
        is_official_strategy=body.type == "ensemble",
    )
    db.add(model)
    db.commit()
    if model.type == "ensemble":
        from ..seed import ensure_account
        ensure_account(db, model.id)
        db.commit()
    return {"id": model.id}


@router.put("/models/{model_pk}")
def update_model(model_pk: int, body: ModelUpdate, db: Session = Depends(get_db)):
    model = db.get(Model, model_pk)
    if model is None:
        raise HTTPException(404, "模型不存在")
    if model.type == "rule":
        raise HTTPException(
            status_code=410,
            detail={
                "code": "capitalized_rule_racing_retired",
                "reason": "历史规则模型是只读审计证据，不能更新、启用或改写",
            },
        )
    if body.name is not None:
        duplicate = db.query(Model).filter(
            Model.name == body.name, Model.id != model_pk).first()
        if duplicate:
            raise HTTPException(400, "名称已存在")
        model.name = body.name
    if body.model_id is not None:
        model.model_id = body.model_id
    if body.members is not None:
        members = (
            _validate_ensemble_members(db, body.members)
            if model.type == "ensemble" else body.members
        )
        model.members = json.dumps(members)
    if body.enabled is not None:
        if body.enabled and model.type == "ensemble":
            other_enabled = db.query(Model).filter(
                Model.type == "ensemble",
                Model.enabled.is_(True),
                Model.id != model_pk,
            ).first()
            if other_enabled:
                raise HTTPException(409, "已有启用的官方 ensemble 策略账户")
        model.enabled = body.enabled
    if model.type == "ensemble":
        # SessionLocal disables autoflush. Without this explicit flush, a
        # legacy migration that disables one of two active ensembles still
        # counts both rows and leaves the remaining strategy uncapitalized.
        db.flush()
        official_count = db.query(Model).filter(
            Model.is_official_strategy.is_(True)).count()
        enabled_ensembles = db.query(Model).filter(
            Model.type == "ensemble", Model.enabled.is_(True)).all()
        if official_count == 0 and len(enabled_ensembles) == 1:
            enabled_ensembles[0].is_official_strategy = True
    db.commit()
    return {"ok": True}


@router.delete("/models/{model_pk}")
def delete_model(model_pk: int, db: Session = Depends(get_db)):
    model = db.get(Model, model_pk)
    if model is None:
        raise HTTPException(404, "模型不存在")
    if model.type == "rule":
        raise HTTPException(
            status_code=410,
            detail={
                "code": "capitalized_rule_racing_retired",
                "reason": "历史规则模型是只读审计证据，不能删除或改变状态",
            },
        )
    # 被合议组合引用的 LLM 模型不可删
    if model.type == "llm":
        for ens in db.query(Model).filter(Model.type == "ensemble").all():
            try:
                members = json.loads(ens.members or "[]")
            except json.JSONDecodeError:
                members = []
            if isinstance(members, list) and model_pk in members:
                raise HTTPException(400, f"被合议组合「{ens.name}」引用,请先移除")
    from ..models import (CanaryState, TradeLedger, TradePlan)

    evidence_tables = (
        Position, Order, Decision, AgentOutput, EquitySnapshot,
        Reflection, MonitorEvent, TradeLedger, TradePlan, CanaryState,
    )
    if any(db.query(table).filter(table.model_pk == model_pk).first()
           for table in evidence_tables):
        model.enabled = False
        db.commit()
        return {
            "ok": True,
            "archived": True,
            "message": "模型已有前瞻证据，已停用并保留历史，未删除。",
        }
    db.query(Account).filter(Account.model_pk == model_pk).delete()
    db.delete(model)
    db.commit()
    return {"ok": True, "archived": False}


@router.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db)):
    rows = []
    for model in (db.query(Model)
                  .filter(
                      Model.enabled.is_(True),
                      Model.type == "ensemble",
                      Model.is_official_strategy.is_(True),
                  )
                  .order_by(Model.id).all()):
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
            "lane": "official",
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
    from ..runtime_settings import get_setting
    pool_max = int(get_setting("selector.pool_max"))
    current_n = db.query(Watchlist).count()
    if current_n >= pool_max:
        raise HTTPException(
            400,
            f"股池已满（{current_n}/{pool_max}）。请先移除部分标的，或在设置中提高股池上限。",
        )
    try:
        quote = market.validate_code(code)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, f"行情数据获取失败: {err}") from err
    if quote is None:
        raise HTTPException(400, "无效代码(仅支持沪深主板/创业板/科创板普通 A 股,不含 ST)")
    item = Watchlist(code=code, name=quote["name"], source="manual")
    db.add(item)
    db.commit()
    return {"id": item.id, "code": code, "name": quote["name"]}


@router.post("/watchlist/auto-select")
def trigger_auto_select(background: BackgroundTasks, db: Session = Depends(get_db)):
    from ..agents import selector as selector_mod
    from ..agents import engine as engine_mod

    if selector_mod._select_lock or engine_mod.is_running():
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
    model = db.get(Model, model_pk)
    if model is None:
        raise HTTPException(404, "模型不存在")
    if model.type == "rule":
        raise HTTPException(
            status_code=410,
            detail={
                "code": "capitalized_rule_racing_retired",
                "reason": "历史规则模型没有可运行资金账户；请使用只读历史策略视图",
            },
        )
    if model.type != "ensemble":
        raise HTTPException(400, "独立 LLM 是判断顾问，不拥有资金或持仓账户")
    if not model.is_official_strategy:
        raise HTTPException(
            status_code=410,
            detail={
                "code": "historical_ensemble_not_capitalized",
                "reason": "该 ensemble 不是唯一官方策略，仅保留为历史证据",
            },
        )
    eq = portfolio.total_equity(db, model_pk)
    positions = []
    # EMT 模拟盘会塞 total_qty=0、name=not_ready 的占位行。它们不是持仓。
    for pos in db.query(Position).filter(
        Position.model_pk == model_pk,
        Position.total_qty > 0,
    ).all():
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
    """拒绝抹除前瞻证据。

    重新开始必须创建一个新的实验纪元并保留旧的 Run、LLM 输入输出、
    计划、门禁、票据、订单和账本。该数据模型落地前，宁可关闭重置，
    也不能让一次按钮点击改写策略的历史表现。
    """
    del db
    raise HTTPException(
        status_code=409,
        detail={
            "status": "immutable_evidence",
            "reason": "为保护前瞻验证证据，已禁用清库式账户重置；请创建新的实验纪元。",
        },
    )


@router.post("/account/repair-bad-quotes")
def repair_bad_quotes(dry_run: bool = False, db: Session = Depends(get_db)):
    """回滚因脏报价（如全市场价=7）触发的误强平，恢复持仓与现金。"""
    from ..trading.repair import repair_bad_quote_force_sells

    return repair_bad_quote_force_sells(db, dry_run=dry_run)


@router.get("/equity-curve")
def equity_curve(db: Session = Depends(get_db)):
    """所有启用账户的收益率曲线 + 沪深300。"""
    series = []
    first_time = None
    for model in (db.query(Model)
                  .filter(
                      Model.enabled.is_(True),
                      Model.type == "ensemble",
                      Model.is_official_strategy.is_(True),
                  )
                  .order_by(Model.id).all()):
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
    if engine.is_running():
        raise HTTPException(409, "已有决策流程在运行中")
    if not db.query(Watchlist).first() and not db.query(Position).first():
        raise HTTPException(400, "股池为空,请先添加自选股")
    if not db.query(Model).filter(Model.enabled.is_(True), Model.type == "llm").first():
        raise HTTPException(400, "无启用的 LLM 模型，请到「模型与策略」启用或添加")
    from ..runtime_settings import get_setting
    if not str(get_setting("secrets.llm_api_key")).strip():
        raise HTTPException(400, "未配置 LLM API Key（设置 → 密钥）")
    background.add_task(engine.run_pipeline, "manual")
    return {"ok": True, "message": "全量决策流程已在后台启动"}


@router.post("/runs/cancel")
def cancel_run():
    """协作式取消当前决策：当前 Agent 结束后停止，已落库保留，撮合前再检查。"""
    result = engine.request_cancel()
    if not result.get("ok"):
        raise HTTPException(409, result.get("message") or "无法取消")
    return result


def _parse_run_result(run: Run) -> dict | None:
    raw = getattr(run, "result_json", None) or ""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _run_list_summary(db: Session, run: Run, models: dict[int, str]) -> list[dict]:
    """列表摘要：优先买卖；选股用 result；失败不塞满 hold。"""
    result = _parse_run_result(run)
    if run.trigger == "selector":
        if result and result.get("kind") == "selector":
            items = []
            for code in result.get("added") or []:
                items.append({
                    "model": result.get("model") or "选股",
                    "code": code, "name": code, "action": "buy",
                    "target_position_pct": 0,
                    "label": "入池",
                })
            for code in result.get("removed") or []:
                items.append({
                    "model": result.get("model") or "选股",
                    "code": code, "name": code, "action": "sell",
                    "target_position_pct": 0,
                    "label": "移出",
                })
            if not items and run.status == "done":
                items.append({
                    "model": result.get("model") or "选股",
                    "code": "", "name": "股池无变动", "action": "hold",
                    "target_position_pct": 0,
                })
            return items[:40]
        # 历史选股无 result_json：有报告则给占位
        if run.status == "done":
            ao = (db.query(AgentOutput)
                  .filter(AgentOutput.run_id == run.id, AgentOutput.code == "SELECT")
                  .first())
            if ao:
                return [{
                    "model": models.get(ao.model_pk, "选股"),
                    "code": "", "name": "选股报告已生成（点进详情）",
                    "action": "hold", "target_position_pct": 0,
                }]
        return []

    decisions = db.query(Decision).filter(Decision.run_id == run.id).all()
    trades = [d for d in decisions if d.action in ("buy", "sell")]
    # 列表只带买卖，避免 30×N 条 hold 把页面撑爆
    rows = [{
        "model": models.get(d.model_pk, "?"), "code": d.code, "name": d.name,
        "action": d.action, "target_position_pct": d.target_position_pct,
    } for d in trades[:40]]
    if not rows and decisions:
        # 无买卖时给一条占位，前端显示「本轮无买卖」
        hold_n = sum(1 for d in decisions if d.action == "hold")
        rows.append({
            "model": "", "code": "", "name": f"无买卖 · {hold_n} 条观望",
            "action": "hold", "target_position_pct": 0,
        })
    return rows


@router.get("/runs")
def list_runs(db: Session = Depends(get_db)):
    runs = db.query(Run).order_by(Run.id.desc()).limit(50).all()
    models = {m.id: m.name for m in db.query(Model).all()}
    result = []
    for run in runs:
        result.append({
            "id": run.id, "trigger": run.trigger, "status": run.status,
            "error": run.error,
            "result": _parse_run_result(run),
            "started_at": run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": run.finished_at.strftime("%Y-%m-%d %H:%M:%S") if run.finished_at else None,
            "summary": _run_list_summary(db, run, models),
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
        "result": _parse_run_result(run),
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
def status(db: Session = Depends(get_db)):
    from .. import scheduler
    from ..agents import selector as selector_mod
    from ..config import settings
    from ..runtime_settings import get_setting
    from ..trading.broker_snapshot import broker_snapshot_status

    progress = engine.get_progress()
    pool_size = db.query(Watchlist).count()
    llm_n = db.query(Model).filter(Model.enabled.is_(True), Model.type == "llm").count()
    official_count = db.query(Model).filter(
        Model.is_official_strategy.is_(True)).count()
    ens_n = db.query(Model).filter(
        Model.enabled.is_(True),
        Model.type == "ensemble",
        Model.is_official_strategy.is_(True),
    ).count()
    authorized_capital = float(get_setting("capital.authorized_capital"))
    max_stock_exposure = float(get_setting("capital.max_stock_exposure"))
    broker_sync = broker_snapshot_status(
        settings.broker_snapshot_path,
        max_age_seconds=settings.broker_snapshot_max_age_seconds,
        max_total_asset=settings.broker_snapshot_max_total_asset,
    )
    live_funds_blockers = [
        "official_disclosure_provider_not_configured",
        "broker_execution_not_configured",
        "authentication_not_configured",
        "shadow_benchmark_not_automated",
        "minimum_forward_validation_not_proven",
    ]
    if official_count != 1:
        live_funds_blockers.append("official_strategy_not_unique")
    elif ens_n != 1:
        live_funds_blockers.append("official_strategy_disabled")
    return {
        "running": engine.is_running(),
        "selecting": selector_mod.is_selecting(),
        "cancel_requested": engine.cancel_requested(),
        "current_run_id": progress.get("run_id") if engine.is_running() else None,
        "progress": progress if engine.is_running() else None,
        "schedule_enabled": scheduler.is_enabled(),
        "schedule_times": scheduler.schedule_times(),
        "next_run": scheduler.next_run_time(),
        "pool_max": get_setting("selector.pool_max"),
        "pool_size": pool_size,
        "factor_top_n": get_setting("factor.top_n"),
        "execution_mode": (
            "manual_ticket_only"
            if bool(get_setting("execution.require_manual_confirmation"))
            else "auto_fill"
            if bool(get_setting("execution.auto_fill_tickets"))
            else "auto_ticket"
        ),
        "broker_sync": broker_sync,
        "official_disclosure_provider": False,
        "live_funds_ready": False,
        "live_funds_blockers": live_funds_blockers,
        "live_funds_blocker_details": {
            "official_disclosure_provider_not_configured":
                "尚未接入官方公告源，重大信息门禁需要人工确认",
            "broker_execution_not_configured":
                "未接入真实券商执行，系统只生成待人工复核的执行票据",
            "authentication_not_configured":
                "API 当前没有身份认证；即使默认仅监听本机，也不得视为实盘就绪",
            "shadow_benchmark_not_automated":
                "零资金机械基准模型已实现，但生产 Run 尚未自动冻结 snapshot 或追加可信 mark",
            "minimum_forward_validation_not_proven":
                "系统尚不能证明已经完成至少半个月、无回看篡改的前瞻模拟验证",
            "official_strategy_not_unique":
                "数据库没有且仅有一个显式官方策略；旧库多 ensemble 时资金授权失败关闭",
            "official_strategy_disabled":
                "唯一官方策略当前已停用，旧计划也不能生成资金票据",
        },
        "authorized_capital": authorized_capital,
        "max_stock_exposure": max_stock_exposure,
        "destructive_reset_enabled": False,
        "fuyao_configured": bool(str(get_setting("secrets.fuyao_api_key")).strip()),
        "llm_configured": bool(str(get_setting("secrets.llm_api_key")).strip()),
        "bark_enabled": bool(get_setting("notifications.bark.enabled")),
        "bark_configured": bool(
            str(get_setting("notifications.bark.device_key")).strip()),
        "llm_enabled_count": llm_n,
        "ensemble_enabled_count": ens_n,
        "official_strategy_count": official_count,
        # 兼容旧客户端；新 UI 不再依赖规则赛马字段。
        "rule_enabled_count": 0,
        "rule_has_positions": False,
        "capitalized_rule_racing_enabled": False,
        "data_primary": "fuyao",
        "news_source": "rss",
        "debug_show_io_default": bool(get_setting("debug.show_agent_io_default")),
    }


@router.get("/datasources")
def datasources_list():
    """数据源角色与配置态（不含网络探测）。"""
    from ..data.datasources import list_sources_status
    from ..data.news_rss import RSS_FEEDS

    return {
        "sources": list_sources_status(),
        "rss_feeds": [
            {"id": f["id"], "name": f["name"], "url": f["url"],
             "region": f.get("region", "")}
            for f in RSS_FEEDS
        ],
    }


@router.post("/datasources/probe")
def datasources_probe(source_id: str | None = None):
    """立即探测数据源健康（可选 source_id=fuyao|sina|tushare|rss）。"""
    from ..data.datasources import SOURCE_META, probe_all

    if source_id and source_id not in {m["id"] for m in SOURCE_META}:
        raise HTTPException(400, f"未知数据源: {source_id}")
    return {"results": probe_all(source_id)}


@router.post("/notifications/bark/test")
def bark_test_notification():
    """Send a real setup test; allowed while the enabled switch is still off."""
    from ..notifications.bark import BarkError, send_test

    try:
        return send_test()
    except BarkError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/logs")
def get_logs(
    limit: int = 200,
    level: str | None = None,
    run_id: int | None = None,
    q: str | None = None,
):
    """全局系统日志（新→旧）。"""
    from ..system_log import list_logs, purge_old
    from ..runtime_settings import get_setting

    purge_old()
    return {
        "items": list_logs(limit=limit, level=level, run_id=run_id, q=q),
        "retention_days": int(get_setting("logs.retention_days")),
        "min_level": str(get_setting("logs.min_level")),
    }


@router.post("/logs/purge")
def purge_logs():
    from ..system_log import purge_old
    n = purge_old()
    return {"ok": True, "removed": n}


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
        raise HTTPException(400, "必须明确指定 keys 或 group，拒绝隐式全量重置")
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


# ---------- 历史规则账户（只读，资本化执行已退役） ----------

@router.post("/rules/rebalance")
def rules_rebalance(db: Session = Depends(get_db)):
    """永久拒绝旧版资本化规则调仓，不因配置或数据状态改变。"""
    raise HTTPException(
        status_code=410,
        detail={
            "code": "capitalized_rule_racing_retired",
            "reason": "资本化规则赛马已退役；请使用零资金 shadow 证据，不会创建订单或账户",
        },
    )


@router.post("/rules/rebalance/{model_id}")
def rules_rebalance_one(model_id: str, db: Session = Depends(get_db)):
    raise HTTPException(
        status_code=410,
        detail={
            "code": "capitalized_rule_racing_retired",
            "reason": "资本化规则赛马已退役；历史规则账户仅供审计查看",
            "model_id": model_id,
        },
    )


@router.get("/rules/status")
def rules_status(db: Session = Depends(get_db)):
    from ..strategies.rule_runner import RULE_MODEL_IDS

    rows = []
    for mid in RULE_MODEL_IDS:
        model = db.query(Model).filter(Model.type == "rule", Model.model_id == mid).first()
        if model is None:
            rows.append({"model_id": mid, "exists": False})
            continue
        account = db.query(Account).filter(Account.model_pk == model.id).first()
        eq = portfolio.total_equity(db, model.id) if account is not None else None
        pos_n = db.query(Position).filter(Position.model_pk == model.id).count()
        rows.append({
            "model_id": mid,
            "exists": True,
            "id": model.id,
            "name": model.name,
            "enabled": model.enabled,
            "historical_only": True,
            "total_equity": eq["total_equity"] if eq else None,
            "cash": eq["cash"] if eq else None,
            "position_count": pos_n,
            "pnl_pct": round((eq["total_equity"] / eq["initial_cash"] - 1) * 100, 2)
            if eq and eq["initial_cash"] else None,
        })
    return {
        "capital_execution_enabled": False,
        "historical_only": True,
        "is_rebalance_day": False,
        "schedule": "已退役：不再自动或手动调仓",
        "top_n": _rt_get("factor.top_n"),
        "strategies": rows,
    }


@router.get("/strategies/board")
def strategies_board(db: Session = Depends(get_db)):
    """历史策略对照只读视图；GET 不创建模型或账户。"""
    from ..strategies.board import build_strategy_board

    board = build_strategy_board(db)
    board["capital_execution_enabled"] = False
    board["historical_only"] = True
    return board


router.include_router(research_router)
router.include_router(analytics_router)
router.include_router(trade_plan_router)
router.include_router(execution_intent_router)





def _rt_get(key: str):
    from ..runtime_settings import get_setting
    return get_setting(key)

