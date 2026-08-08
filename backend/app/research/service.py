"""研究假说服务：CRUD / 翻译 / 回测 / 晋升 / 废弃 / 退役。"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..models import Account, Model, ResearchHypothesis
from ..runtime_settings import get_setting
from ..seed import ensure_account
from ..trading import broker
from .spec import dumps, loads, validate_spec
from .translate import translate_theory

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now()


def list_hypotheses(db: Session, status: str | None = None) -> list[dict[str, Any]]:
    q = db.query(ResearchHypothesis).order_by(ResearchHypothesis.id.desc())
    if status:
        q = q.filter(ResearchHypothesis.status == status)
    return [_to_dict(h) for h in q.limit(100).all()]


def get_hypothesis(db: Session, hid: int) -> ResearchHypothesis:
    h = db.get(ResearchHypothesis, hid)
    if h is None:
        raise ValueError("假说不存在")
    return h


def create_hypothesis(db: Session, theory_text: str, title: str = "") -> dict[str, Any]:
    theory_text = (theory_text or "").strip()
    if not theory_text:
        raise ValueError("请填写投资理论 / 假说文本")
    if not title:
        title = theory_text.split("\n")[0].strip()[:40] or "未命名假说"
    h = ResearchHypothesis(
        title=title[:120],
        theory_text=theory_text,
        status="draft",
        spec_json="{}",
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return _to_dict(h)


def translate(db: Session, hid: int) -> dict[str, Any]:
    h = get_hypothesis(db, hid)
    result = translate_theory(db, h.theory_text, title=h.title)
    spec = result["spec"]
    h.spec_json = dumps(spec)
    h.title = str(spec.get("name") or h.title)[:120]
    h.status = "draft"
    h.updated_at = _now()
    db.commit()
    out = _to_dict(h)
    out["translate_source"] = result.get("source")
    out["translate_errors"] = result.get("errors") or []
    return out


def update_spec(db: Session, hid: int, spec_raw: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
    h = get_hypothesis(db, hid)
    if h.status in ("promoted", "retired"):
        raise ValueError("已晋升/退役的假说不可再改规格（请新建假说）")
    spec, errors = validate_spec(spec_raw)
    if errors:
        raise ValueError("规格无效: " + "; ".join(errors))
    h.spec_json = dumps(spec)
    h.title = str(spec.get("name") or h.title)[:120]
    if confirm:
        h.status = "confirmed"
    elif h.status in ("backtested", "suggested"):
        # 改规格后需重跑回测
        h.status = "confirmed"
        h.backtest_json = ""
        h.suggestion = ""
    h.updated_at = _now()
    db.commit()
    return _to_dict(h)


def run_backtest(db: Session, hid: int, years: int = 3) -> dict[str, Any]:
    from ..backtest.spec_runner import run_spec_backtest
    from ..factors.panel import build_factor_panel
    from ..models import Watchlist

    h = get_hypothesis(db, hid)
    if h.status == "draft" and (not h.spec_json or h.spec_json == "{}"):
        raise ValueError("请先翻译或确认规格")
    spec = loads(h.spec_json)
    codes = [w.code for w in db.query(Watchlist).all()]
    if len(codes) < 2:
        raise ValueError("股池至少需要 2 只股票")
    years = max(1, min(int(years or 3), 8))
    start = date.today() - timedelta(days=365 * years + 60)
    panel = build_factor_panel(codes, start=start, end=date.today())
    if panel is None or panel.empty:
        raise RuntimeError("无法构建因子面板（检查扶摇 Key 与股池）")
    if "close" not in panel.columns and "收盘" in panel.columns:
        panel = panel.rename(columns={"收盘": "close"})

    result = run_spec_backtest(panel, spec)
    # 锚对照
    from ..backtest.engine import run_equal_weight_buyhold
    anchor = run_equal_weight_buyhold(panel)

    suggestion = _suggest(result.metrics, anchor.metrics if anchor else None)
    payload = {
        "codes": codes,
        "start": start.isoformat(),
        "spec": spec,
        "result": result.to_dict(),
        "anchor": anchor.to_dict() if anchor else None,
        "suggestion": suggestion,
        "suggestion_reason": _suggest_reason(result.metrics, anchor.metrics if anchor else None, suggestion),
    }
    h.backtest_json = json.dumps(payload, ensure_ascii=False, default=str)
    h.suggestion = suggestion
    h.status = "suggested" if suggestion in ("promote", "discard") else "backtested"
    if suggestion == "review":
        h.status = "backtested"
    h.updated_at = _now()
    db.commit()
    return _to_dict(h)


def _suggest(metrics: dict | None, anchor: dict | None) -> str:
    metrics = metrics or {}
    if not metrics.get("sample_ok"):
        return "review"  # 样本不足 → 人工看
    sharpe = float(metrics.get("sharpe") or 0)
    a_sharpe = float((anchor or {}).get("sharpe") or 0)
    mdd = float(metrics.get("max_drawdown") or 0)
    if sharpe > a_sharpe and sharpe > 0 and mdd > -0.5:
        return "promote"
    if sharpe < a_sharpe - 0.2 or sharpe < 0:
        return "discard"
    return "review"


def _suggest_reason(metrics, anchor, suggestion: str) -> str:
    metrics = metrics or {}
    a = anchor or {}
    if suggestion == "review" and not metrics.get("sample_ok"):
        return (
            f"样本未达标（交易日 {metrics.get('n_days')} / "
            f"平仓 {metrics.get('closed_trades')}），请人工判断"
        )
    if suggestion == "promote":
        return (
            f"样本达标且夏普 {metrics.get('sharpe')} > 锚 {a.get('sharpe')}，建议晋升"
        )
    if suggestion == "discard":
        return (
            f"夏普 {metrics.get('sharpe')} 弱于锚 {a.get('sharpe')} 或为负，建议废弃"
        )
    return "灰区：建议人工复核后再晋升或废弃"


def discard(db: Session, hid: int, reason: str = "") -> dict[str, Any]:
    h = get_hypothesis(db, hid)
    if h.status == "promoted":
        raise ValueError("已晋升假说请先退役再废弃，或直接退役账户")
    h.status = "discarded"
    h.discard_reason = (reason or "")[:500]
    h.updated_at = _now()
    db.commit()
    return _to_dict(h)


def count_competitive_live(db: Session) -> int:
    """可竞赛在跑 rule 臂数（不含锚 pool_equal）。"""
    n = 0
    for m in db.query(Model).filter(Model.type == "rule", Model.enabled.is_(True)).all():
        if m.model_id == "pool_equal":
            continue
        n += 1
    return n


def max_live_arms() -> int:
    try:
        return int(get_setting("race.max_live_rule_arms"))
    except Exception:  # noqa: BLE001
        return 10


def promote(db: Session, hid: int) -> dict[str, Any]:
    h = get_hypothesis(db, hid)
    if h.status == "promoted" and h.promoted_model_id:
        return _to_dict(h)
    if h.status == "discarded":
        raise ValueError("已废弃假说不可晋升")
    if not h.backtest_json:
        raise ValueError("请先完成回测")
    spec = loads(h.spec_json)
    live = count_competitive_live(db)
    cap = max_live_arms()
    if live >= cap:
        raise ValueError(
            f"可竞赛规则臂已满（{live}/{cap}）。请先在策略页停用/退役一支后再晋升。"
        )

    model_id = f"res_{h.id}"
    existing = db.query(Model).filter(Model.model_id == model_id).first()
    if existing:
        existing.enabled = True
        existing.name = (h.title or model_id)[:50]
        existing.members = json.dumps(
            {"kind": "research", "hypothesis_id": h.id, "spec": spec},
            ensure_ascii=False,
        )
        model = existing
    else:
        # 名称唯一
        base_name = (h.title or f"研究#{h.id}")[:40]
        name = base_name
        i = 1
        while db.query(Model).filter(Model.name == name).first():
            name = f"{base_name}-{i}"
            i += 1
        model = Model(
            name=name,
            model_id=model_id,
            type="rule",
            enabled=True,
            members=json.dumps(
                {"kind": "research", "hypothesis_id": h.id, "spec": spec},
                ensure_ascii=False,
            ),
        )
        db.add(model)
        db.flush()
        ensure_account(db, model.id)

    h.status = "promoted"
    h.promoted_model_id = model_id
    h.updated_at = _now()
    db.commit()
    out = _to_dict(h)
    out["model_pk"] = model.id
    return out


def retire(db: Session, hid: int, reason: str = "") -> dict[str, Any]:
    h = get_hypothesis(db, hid)
    if not h.promoted_model_id:
        raise ValueError("未晋升，无需退役")
    model = db.query(Model).filter(Model.model_id == h.promoted_model_id).first()
    if model:
        model.enabled = False
    h.status = "retired"
    if reason:
        h.discard_reason = reason[:500]
    h.updated_at = _now()
    db.commit()
    return _to_dict(h)


def _to_dict(h: ResearchHypothesis) -> dict[str, Any]:
    bt = None
    if h.backtest_json:
        try:
            bt = json.loads(h.backtest_json)
        except json.JSONDecodeError:
            bt = None
    return {
        "id": h.id,
        "title": h.title,
        "theory_text": h.theory_text,
        "spec": loads(h.spec_json),
        "status": h.status,
        "suggestion": h.suggestion,
        "discard_reason": h.discard_reason,
        "promoted_model_id": h.promoted_model_id,
        "backtest": bt,
        "created_at": h.created_at.strftime("%Y-%m-%d %H:%M:%S") if h.created_at else "",
        "updated_at": h.updated_at.strftime("%Y-%m-%d %H:%M:%S") if h.updated_at else "",
    }
