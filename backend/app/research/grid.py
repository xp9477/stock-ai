"""规则库网格：笛卡尔积有上限，单次拉面板批量回测。"""
from __future__ import annotations

import itertools
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ..backtest.engine import run_equal_weight_buyhold
from ..backtest import evidence
from ..backtest.validation import split_development_holdout
from ..factors.panel import build_factor_panel
from ..models import Watchlist
from ..runtime_settings import get_setting
from .library import PRESET_FACTOR_SETS
from .spec import validate_spec

logger = logging.getLogger(__name__)

DEFAULT_MAX_COMBOS = 48


def expand_grid(
    *,
    factor_set_ids: list[str] | None = None,
    top_n_list: list[int] | None = None,
    rebalances: list[str] | None = None,
    stop_losses: list[float | None] | None = None,
    include_equal_weight: bool = True,
    max_combos: int | None = None,
) -> list[dict[str, Any]]:
    """生成规格列表（未回测）。"""
    max_combos = max(1, min(int(max_combos or DEFAULT_MAX_COMBOS), 120))
    presets = {p["id"]: p for p in PRESET_FACTOR_SETS}
    ids = factor_set_ids or [p["id"] for p in PRESET_FACTOR_SETS[:5]]
    sets = [presets[i] for i in ids if i in presets]
    if not sets:
        sets = PRESET_FACTOR_SETS[:3]

    top_n_list = top_n_list or [5, 10, 15]
    rebalances = rebalances or ["W-MON", "ME"]
    stop_losses = stop_losses if stop_losses is not None else [None, -0.08]

    specs: list[dict[str, Any]] = []
    if include_equal_weight:
        for stop in stop_losses:
            events = []
            if stop is not None:
                events.append({"type": "stop_loss_pct", "value": float(stop)})
            raw = {
                "name": f"等权" + (f"+止损{abs(stop)*100:.0f}%" if stop else ""),
                "mode": "equal_weight",
                "factors": [],
                "top_n": 30,
                "rebalance": "W-MON",
                "events": events,
                "notes": "grid",
            }
            spec, err = validate_spec(raw)
            if not err:
                specs.append(spec)

    for preset, top_n, reb, stop in itertools.product(sets, top_n_list, rebalances, stop_losses):
        events = []
        if stop is not None:
            events.append({"type": "stop_loss_pct", "value": float(stop)})
        name = f"{preset['label']}·N{top_n}·{reb}"
        if stop is not None:
            name += f"·SL{abs(stop)*100:.0f}"
        raw = {
            "name": name[:80],
            "mode": "factor_cross_section",
            "factors": list(preset["factors"]),
            "top_n": int(top_n),
            "rebalance": reb,
            "events": events,
            "notes": f"grid:{preset['id']}",
        }
        spec, err = validate_spec(raw)
        if err:
            continue
        specs.append(spec)
        if len(specs) >= max_combos:
            break

    return specs[:max_combos]


def run_grid(
    db: Session,
    *,
    years: int = 3,
    factor_set_ids: list[str] | None = None,
    top_n_list: list[int] | None = None,
    rebalances: list[str] | None = None,
    stop_losses: list[float | None] | None = None,
    include_equal_weight: bool = True,
    max_combos: int | None = None,
) -> dict[str, Any]:
    codes = [w.code for w in db.query(Watchlist).all()]
    if len(codes) < 2:
        raise ValueError("股池至少需要 2 只股票")

    years = max(1, min(int(years or 3), 8))
    start = date.today() - timedelta(days=365 * years + 60)
    panel = build_factor_panel(codes, start=start, end=date.today())
    if panel is None or getattr(panel, "empty", True):
        raise RuntimeError("无法构建因子面板（检查扶摇与股池）")
    if "close" not in panel.columns and "收盘" in panel.columns:
        panel = panel.rename(columns={"收盘": "close"})

    # 网格搜索永远看不到最后 20% 保留样本，避免用测试集挑参数。
    development, holdout = split_development_holdout(panel)
    if development.empty or holdout.empty:
        raise RuntimeError("开发期/封存保留样本切分失败，数据不足")
    holdout_start = pd.to_datetime(holdout["date"], errors="coerce").min()
    if pd.isna(holdout_start):
        raise RuntimeError("封存保留样本缺少有效日期")
    development_cutoff = datetime.combine(
        holdout_start.date(), time.min, tzinfo=timezone.utc)
    specs = expand_grid(
        factor_set_ids=factor_set_ids,
        top_n_list=top_n_list,
        rebalances=rebalances,
        stop_losses=stop_losses,
        include_equal_weight=include_equal_weight,
        max_combos=max_combos,
    )
    anchor = run_equal_weight_buyhold(development)
    a_metrics = anchor.metrics or {}

    rows: list[dict[str, Any]] = []
    for i, spec in enumerate(specs):
        try:
            experiment = evidence.run_reproducible_development(
                db,
                panel=development,
                spec=spec,
                universe=codes,
                data_cutoff_at=development_cutoff,
            )
            result_row = evidence.get_result(db, experiment.id, "development")
            persisted = evidence.result_dict(result_row)["strategy"]
            m = persisted.get("metrics") or {}
            sharpe = float(m.get("sharpe") or 0)
            a_sh = float(a_metrics.get("sharpe") or 0)
            suggestion = "review"
            if m.get("sample_ok") and sharpe > a_sh and sharpe > 0:
                suggestion = "candidate"
            elif m.get("sample_ok") and (sharpe < a_sh - 0.2 or sharpe < 0):
                suggestion = "discard"
            rows.append({
                "rank": 0,
                "experiment_id": experiment.id,
                "result_fingerprint": result_row.result_fingerprint,
                "spec": spec,
                "metrics": m,
                "closed_trades": int(persisted.get("closed_trades") or 0),
                "suggestion": suggestion,
                "sharpe": sharpe,
                "excess_sharpe_vs_anchor": round(sharpe - a_sh, 4),
            })
        except Exception as err:  # noqa: BLE001
            logger.warning("grid combo failed %s: %s", spec.get("name"), err)
            rows.append({
                "rank": 0,
                "spec": spec,
                "error": str(err)[:200],
                "suggestion": "discard",
                "sharpe": -999,
            })

    rows.sort(key=lambda r: (-(r.get("sharpe") if r.get("sharpe") != -999 else -999),))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i

    min_days = int(get_setting("race.min_trade_days"))
    min_trades = int(get_setting("race.min_closed_trades"))
    return {
        "codes": codes,
        "start": start.isoformat(),
        "years": years,
        "n_combos": len(specs),
        "anchor": anchor.to_dict(),
        "race": {"min_trade_days": min_days, "min_closed_trades": min_trades},
        "rows": rows,
        "survivors": [r for r in rows if r.get("suggestion") == "candidate"],
        "holdout_reserved": True,
    }


def import_specs_as_hypotheses(
    db: Session,
    specs: list[dict[str, Any]],
    *,
    theory_prefix: str = "网格导入",
) -> list[dict[str, Any]]:
    """把选中的网格规格落成假说草稿（已带 spec，状态 confirmed）。"""
    from . import service as research
    from .spec import dumps

    created = []
    for raw in specs[:30]:
        spec, err = validate_spec(raw if isinstance(raw, dict) else {})
        if err:
            continue
        h = research.create_hypothesis(
            db,
            theory_text=f"{theory_prefix}：{spec.get('name')}\n{spec.get('notes') or ''}",
            title=str(spec.get("name") or "网格策略")[:80],
        )
        row = research.get_hypothesis(db, h["id"])
        row.spec_json = dumps(spec)
        row.status = "confirmed"
        db.commit()
        created.append(research._to_dict(row))
    return created
