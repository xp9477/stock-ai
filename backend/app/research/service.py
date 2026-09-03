"""研究假说服务：CRUD / 翻译 / 回测 / 晋升 / 废弃 / 退役。"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import (
    BacktestExperiment,
    HoldoutAccessEvent,
    HoldoutSelection,
    Model,
    ResearchHypothesis,
)
from ..runtime_settings import get_setting
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
    if h.status in ("promoted", "retired"):
        raise ValueError("已晋升/退役的假说不可重新翻译（请新建假说）")
    _ensure_holdout_not_opened(db, h)
    result = translate_theory(db, h.theory_text, title=h.title)
    spec = result["spec"]
    h.spec_json = dumps(spec)
    h.title = str(spec.get("name") or h.title)[:120]
    h.status = "draft"
    # A translated spec is a new claim.  Never carry a result or promotion
    # recommendation produced by the previous claim across this boundary.
    h.backtest_json = ""
    h.suggestion = ""
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
    _ensure_holdout_not_opened(db, h)
    spec, errors = validate_spec(spec_raw)
    if errors:
        raise ValueError("规格无效: " + "; ".join(errors))
    changed = dumps(spec) != dumps(loads(h.spec_json))
    h.spec_json = dumps(spec)
    h.title = str(spec.get("name") or h.title)[:120]
    if confirm:
        h.status = "confirmed"
    elif changed and h.status in ("backtested", "suggested"):
        # 改规格后需重跑回测
        h.status = "confirmed"
    if changed:
        h.backtest_json = ""
        h.suggestion = ""
    h.updated_at = _now()
    db.commit()
    return _to_dict(h)


def run_backtest(
    db: Session,
    hid: int,
    years: int = 3,
    *,
    reveal_holdout: bool = True,
    actor: str = "local_user",
) -> dict[str, Any]:
    from ..backtest import evidence
    from ..factors.panel import build_factor_panel
    from ..models import Watchlist

    h = get_hypothesis(db, hid)
    if h.status == "draft" and (not h.spec_json or h.spec_json == "{}"):
        raise ValueError("请先翻译或确认规格")
    spec = loads(h.spec_json)
    current_spec_fingerprint = evidence.spec_fingerprint(spec)
    existing = (
        db.query(BacktestExperiment)
        .filter(
            BacktestExperiment.hypothesis_id == h.id,
            BacktestExperiment.spec_fingerprint == current_spec_fingerprint,
            BacktestExperiment.status.in_((
                "development_completed", "completed", "holdout_failed",
            )),
        )
        .order_by(BacktestExperiment.id.desc())
        .first()
    )
    if existing is not None:
        return _publish_experiment(
            db,
            h,
            existing,
            reveal_holdout=reveal_holdout,
            actor=actor,
        )

    codes = [w.code for w in db.query(Watchlist).all()]
    if len(codes) < 2:
        raise ValueError("股池至少需要 2 只股票")
    years = max(1, min(int(years or 3), 8))
    # Only request days strictly before the frozen cutoff.  We cannot prove an
    # intraday bar on the current calendar date is final.
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=365 * years + 60)
    cutoff = datetime.combine(date.today(), time.min, tzinfo=timezone.utc)
    panel = build_factor_panel(codes, start=start, end=end)
    if panel is None or panel.empty:
        raise RuntimeError("无法构建因子面板（检查扶摇 Key 与股池）")
    experiment = evidence.run_reproducible_experiment(
        db,
        panel=panel,
        spec=spec,
        universe=codes,
        data_cutoff_at=cutoff,
        hypothesis_id=h.id,
    )
    return _publish_experiment(
        db,
        h,
        experiment,
        reveal_holdout=reveal_holdout,
        actor=actor,
    )


def _ensure_holdout_not_opened(db: Session, h: ResearchHypothesis) -> None:
    opened = (
        db.query(BacktestExperiment.id)
        .filter(
            BacktestExperiment.hypothesis_id == h.id,
            BacktestExperiment.holdout_opened_at.is_not(None),
        )
        .first()
    )
    if opened is not None:
        raise ValueError("保留样本已经查看；该假说规格已封存，请新建假说")


def _publish_experiment(
    db: Session,
    h: ResearchHypothesis,
    experiment: BacktestExperiment,
    *,
    reveal_holdout: bool,
    actor: str,
) -> dict[str, Any]:
    from ..backtest import evidence

    if experiment.hypothesis_id != h.id:
        raise ValueError("experiment does not belong to the hypothesis")
    current_spec_fingerprint = evidence.spec_fingerprint(loads(h.spec_json))
    if experiment.spec_fingerprint != current_spec_fingerprint:
        raise ValueError("experiment result does not match the current spec")

    development_row = evidence.get_result(db, experiment.id, "development")
    development = evidence.result_dict(development_row)
    holdout = None
    holdout_row = None
    suggestion = "review"
    suggestion_reason = "保留样本仍封存；开发结果不能用于晋升"
    if reveal_holdout:
        holdout = evidence.open_holdout(
            db,
            experiment.id,
            actor=actor,
            purpose="hypothesis_final_validation",
        )
        holdout_row = evidence.get_result(db, experiment.id, "holdout")
        suggestion = _suggest(
            development["strategy"].get("metrics"),
            development["anchor"].get("metrics"),
            holdout["strategy"].get("metrics"),
            holdout["anchor"].get("metrics"),
        )
        suggestion_reason = _suggest_reason(
            development["strategy"].get("metrics"),
            development["anchor"].get("metrics"),
            holdout["strategy"].get("metrics"),
            holdout["anchor"].get("metrics"),
            suggestion,
        )

    payload = {
        "experiment_id": experiment.id,
        "experiment_key": experiment.experiment_key,
        "fingerprints": {
            "spec": experiment.spec_fingerprint,
            "code": experiment.code_fingerprint,
            "config": experiment.config_fingerprint,
            "data": experiment.data_fingerprint,
            "universe": experiment.universe_fingerprint,
        },
        "result_fingerprints": {
            "development": development_row.result_fingerprint,
            "holdout": holdout_row.result_fingerprint if holdout_row else None,
        },
        "data_cutoff_at": experiment.data_cutoff_at.isoformat(),
        "codes": json.loads(experiment.universe_json),
        "start": experiment.data_start,
        "spec": json.loads(experiment.spec_json),
        "result": development["strategy"],
        "anchor": development["anchor"],
        "holdout": holdout["strategy"] if holdout else None,
        "holdout_anchor": holdout["anchor"] if holdout else None,
        "holdout_sealed": holdout is None,
        "split": {
            "development": experiment.development_ratio,
            "holdout": 1 - experiment.development_ratio,
        },
        "suggestion": suggestion,
        "suggestion_reason": suggestion_reason,
    }
    h.backtest_json = json.dumps(payload, ensure_ascii=False, default=str)
    h.suggestion = suggestion if holdout is not None else ""
    if holdout is not None and suggestion in ("promote", "discard"):
        h.status = "suggested"
    else:
        h.status = "backtested"
    h.updated_at = _now()
    db.commit()
    return _to_dict(h)


def reveal_holdout(
    db: Session,
    hid: int,
    experiment_id: int,
    *,
    actor: str = "local_user",
) -> dict[str, Any]:
    h = get_hypothesis(db, hid)
    experiment = db.get(BacktestExperiment, experiment_id)
    if experiment is None:
        raise ValueError("experiment does not exist")
    return _publish_experiment(
        db,
        h,
        experiment,
        reveal_holdout=True,
        actor=actor,
    )


def _suggest(
    metrics: dict | None,
    anchor: dict | None,
    holdout: dict | None,
    holdout_anchor: dict | None,
    *,
    min_trade_days: int | None = None,
    min_closed_trades: int | None = None,
) -> str:
    metrics = metrics or {}
    if not metrics.get("sample_ok"):
        return "review"  # 样本不足 → 人工看
    holdout = holdout or {}
    frozen_days = (
        int(min_trade_days)
        if min_trade_days is not None
        else int(get_setting("race.min_trade_days"))
    )
    frozen_trades = (
        int(min_closed_trades)
        if min_closed_trades is not None
        else int(get_setting("race.min_closed_trades"))
    )
    min_days = max(40, frozen_days // 2)
    min_trades = max(5, frozen_trades // 10)
    if int(holdout.get("n_days") or 0) < min_days:
        return "review"
    if int(holdout.get("closed_trades") or 0) < min_trades:
        return "review"
    sharpe = float(metrics.get("sharpe") or 0)
    a_sharpe = float((anchor or {}).get("sharpe") or 0)
    h_sharpe = float(holdout.get("sharpe") or 0)
    h_anchor = float((holdout_anchor or {}).get("sharpe") or 0)
    mdd = float(metrics.get("max_drawdown") or 0)
    if (sharpe > a_sharpe and sharpe > 0 and mdd > -0.5
            and h_sharpe > h_anchor and h_sharpe > 0):
        return "promote"
    if sharpe < a_sharpe - 0.2 or sharpe < 0 or h_sharpe < 0:
        return "discard"
    return "review"


def _suggest_reason(metrics, anchor, holdout, holdout_anchor, suggestion: str) -> str:
    metrics = metrics or {}
    a = anchor or {}
    holdout = holdout or {}
    holdout_anchor = holdout_anchor or {}
    if suggestion == "review" and not metrics.get("sample_ok"):
        return (
            f"样本未达标（交易日 {metrics.get('n_days')} / "
            f"平仓 {metrics.get('closed_trades')}），请人工判断"
        )
    if suggestion == "promote":
        return (
            f"开发期夏普 {metrics.get('sharpe')} > 锚 {a.get('sharpe')}；"
            f"保留样本夏普 {holdout.get('sharpe')} > 锚 {holdout_anchor.get('sharpe')}，建议晋升"
        )
    if suggestion == "discard":
        return (
            f"夏普 {metrics.get('sharpe')} 弱于锚 {a.get('sharpe')} 或为负，建议废弃"
        )
    return (
        "未同时通过开发期与保留样本门槛："
        f"保留样本 {holdout.get('n_days', 0)} 日 / {holdout.get('closed_trades', 0)} 笔，"
        f"夏普 {holdout.get('sharpe', 0)}"
    )


def discard(db: Session, hid: int, reason: str = "") -> dict[str, Any]:
    h = get_hypothesis(db, hid)
    if h.status == "promoted":
        raise ValueError("已晋升假说请先退役证据，再决定是否废弃")
    h.status = "discarded"
    h.discard_reason = (reason or "")[:500]
    h.updated_at = _now()
    db.commit()
    return _to_dict(h)


def promote(db: Session, hid: int) -> dict[str, Any]:
    h = get_hypothesis(db, hid)
    if h.status == "promoted":
        return _to_dict(h)
    if h.status == "discarded":
        raise ValueError("已废弃假说不可晋升")
    if not h.backtest_json:
        raise ValueError("请先完成回测")
    spec = loads(h.spec_json)
    experiment = _validated_promotion_experiment(db, h, spec)
    # Promotion is an evidence decision, not capital authorization.  It must
    # never create/re-enable a Model, Account, Position, or Order.  Historical
    # promoted_model_id values remain untouched as read-only legacy evidence;
    # newly promoted hypotheses deliberately keep this compatibility field empty.
    h.status = "promoted"
    h.promoted_model_id = ""
    h.updated_at = _now()
    db.commit()
    out = _to_dict(h)
    out["promoted_experiment_id"] = experiment.id
    out["spec_fingerprint"] = experiment.spec_fingerprint
    out["capital_account_created"] = False
    return out


def _validated_promotion_experiment(
    db: Session,
    h: ResearchHypothesis,
    spec: dict[str, Any],
) -> BacktestExperiment:
    """Bind promotion to immutable results when the hypothesis uses the new ledger."""
    from ..backtest import evidence

    experiments = (
        db.query(BacktestExperiment)
        .filter(BacktestExperiment.hypothesis_id == h.id)
        .all()
    )
    if not experiments:
        raise ValueError("缺少不可变 experiment 证据，旧回测投影不能晋升")
    try:
        projection = json.loads(h.backtest_json)
        experiment_id = int(projection.get("experiment_id"))
    except (TypeError, ValueError, json.JSONDecodeError, AttributeError) as error:
        raise ValueError("回测投影缺少不可变 experiment 证据") from error
    experiment = db.get(BacktestExperiment, experiment_id)
    if experiment is None or experiment.hypothesis_id != h.id:
        raise ValueError("回测 experiment 与当前假说不匹配")
    if experiment.status != "completed" or experiment.holdout_opened_at is None:
        raise ValueError("experiment 尚未完成封存验证")
    if experiment.spec_fingerprint != evidence.spec_fingerprint(spec):
        raise ValueError("当前规格与回测 experiment 指纹不匹配")
    frozen_fingerprints = projection.get("fingerprints") or {}
    expected_fingerprints = {
        "spec": experiment.spec_fingerprint,
        "code": experiment.code_fingerprint,
        "config": experiment.config_fingerprint,
        "data": experiment.data_fingerprint,
        "universe": experiment.universe_fingerprint,
    }
    if frozen_fingerprints != expected_fingerprints:
        raise ValueError("回测投影 provenance 指纹与 experiment 不匹配")
    development_row = evidence.get_result(db, experiment.id, "development")
    holdout_row = evidence.get_result(db, experiment.id, "holdout")
    result_fingerprints = projection.get("result_fingerprints") or {}
    if result_fingerprints != {
        "development": development_row.result_fingerprint,
        "holdout": holdout_row.result_fingerprint,
    }:
        raise ValueError("回测投影 result 指纹与不可变结果不匹配")
    selection = (
        db.query(HoldoutSelection)
        .filter(HoldoutSelection.campaign_key == experiment.campaign_key)
        .first()
    )
    if (
        selection is None
        or selection.experiment_id != experiment.id
        or selection.development_result_id != development_row.id
        or selection.development_result_fingerprint != development_row.result_fingerprint
    ):
        raise ValueError("experiment 缺少匹配的 campaign holdout reservation")
    access = (
        db.query(HoldoutAccessEvent.id)
        .filter(
            HoldoutAccessEvent.selection_id == selection.id,
            HoldoutAccessEvent.experiment_id == experiment.id,
            HoldoutAccessEvent.result_id == holdout_row.id,
            HoldoutAccessEvent.result_fingerprint == holdout_row.result_fingerprint,
        )
        .first()
    )
    if access is None:
        raise ValueError("experiment holdout 尚无匹配的访问证据")
    development = evidence.result_dict(development_row)
    holdout = evidence.result_dict(holdout_row)
    try:
        frozen_config = json.loads(experiment.config_json)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("experiment 冻结配置无法解析") from error
    if not isinstance(frozen_config, dict):
        raise ValueError("experiment 冻结配置不是对象")
    frozen_settings = frozen_config.get("settings")
    if not isinstance(frozen_settings, dict):
        raise ValueError("experiment 冻结配置缺少 settings 对象")
    try:
        frozen_min_days = int(frozen_settings["race.min_trade_days"])
        frozen_min_trades = int(frozen_settings["race.min_closed_trades"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("experiment 冻结配置缺少验证样本门槛") from error
    verified_suggestion = _suggest(
        development["strategy"].get("metrics"),
        development["anchor"].get("metrics"),
        holdout["strategy"].get("metrics"),
        holdout["anchor"].get("metrics"),
        min_trade_days=frozen_min_days,
        min_closed_trades=frozen_min_trades,
    )
    if verified_suggestion != "promote":
        raise ValueError("不可变开发期/保留样本结果不支持晋升")
    return experiment


def retire(db: Session, hid: int, reason: str = "") -> dict[str, Any]:
    h = get_hypothesis(db, hid)
    if h.status != "promoted":
        raise ValueError("未晋升，无需退役")
    # Legacy versions may have attached a rule model.  Preserve its records but
    # disable it; evidence-only promotions have no capital object to mutate.
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
    promoted_experiment_id = None
    if isinstance(bt, dict) and h.status in ("promoted", "retired"):
        promoted_experiment_id = bt.get("experiment_id")
    return {
        "id": h.id,
        "title": h.title,
        "theory_text": h.theory_text,
        "spec": loads(h.spec_json),
        "status": h.status,
        "suggestion": h.suggestion,
        "discard_reason": h.discard_reason,
        "promoted_model_id": h.promoted_model_id,
        "promoted_experiment_id": promoted_experiment_id,
        "capital_account_attached": bool(h.promoted_model_id),
        "backtest": bt,
        "created_at": h.created_at.strftime("%Y-%m-%d %H:%M:%S") if h.created_at else "",
        "updated_at": h.updated_at.strftime("%Y-%m-%d %H:%M:%S") if h.updated_at else "",
    }
