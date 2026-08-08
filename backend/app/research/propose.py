"""AI 提议假说（产品 B）：从规则库 / 失效臂生成候选；须人确认后回测。

不做主动爬网（C）；LLM 可用时润色理论文案，否则用模板。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..models import Model, ResearchHypothesis
from .library import PRESET_FACTOR_SETS
from .spec import dumps, loads, validate_spec
from . import service as research

logger = logging.getLogger(__name__)


def propose_candidates(
    db: Session,
    *,
    count: int = 5,
    mode: str = "library",  # library | improve
) -> list[dict[str, Any]]:
    count = max(1, min(int(count or 5), 12))
    created: list[dict[str, Any]] = []

    if mode == "improve":
        created.extend(_propose_improve(db, count=count))
    # 补足 library 预设
    need = count - len(created)
    if need > 0:
        created.extend(_propose_library(db, count=need))

    return created[:count]


def _propose_library(db: Session, count: int) -> list[dict[str, Any]]:
    out = []
    tops = [8, 10, 15]
    for i, preset in enumerate(PRESET_FACTOR_SETS):
        if len(out) >= count:
            break
        n = tops[i % len(tops)]
        title = f"提议·{preset['label']}·N{n}"
        # 避免重复标题刷屏
        if db.query(ResearchHypothesis).filter(ResearchHypothesis.title == title).first():
            title = f"{title}·{db.query(ResearchHypothesis).count() + 1}"
        theory = (
            f"{preset['label']}策略：因子 {', '.join(preset['factors'])}，"
            f"每周调仓持有前 {n} 只（规则库自动提议，请确认规格后回测）。"
        )
        h = research.create_hypothesis(db, theory, title=title)
        spec, _ = validate_spec({
            "name": title,
            "mode": "factor_cross_section",
            "factors": list(preset["factors"]),
            "top_n": n,
            "rebalance": "W-MON",
            "events": [{"type": "stop_loss_pct", "value": -0.08}],
            "notes": "propose:library",
        })
        row = research.get_hypothesis(db, h["id"])
        row.spec_json = dumps(spec)
        row.status = "draft"
        db.commit()
        out.append(research._to_dict(row))
    return out


def _propose_improve(db: Session, count: int) -> list[dict[str, Any]]:
    """针对已废弃/弱建议假说或停用研究臂，生成改进变体。"""
    out = []
    weak = (
        db.query(ResearchHypothesis)
        .filter(ResearchHypothesis.suggestion == "discard")
        .order_by(ResearchHypothesis.id.desc())
        .limit(count)
        .all()
    )
    for h0 in weak:
        if len(out) >= count:
            break
        base = loads(h0.spec_json)
        factors = list(base.get("factors") or ["mom_short", "quality_roe"])
        # 变体：加低波、收紧 top_n、加止损
        if "low_vol" not in factors:
            factors.append("low_vol")
        top_n = max(5, int(base.get("top_n") or 10) - 2)
        title = f"改进·{(h0.title or '策略')[:20]}"
        theory = (
            f"基于假说#{h0.id} 的改进：原建议废弃。"
            f"加入低波过滤，TopN={top_n}，止损 8%。"
        )
        h = research.create_hypothesis(db, theory, title=title)
        spec, _ = validate_spec({
            "name": title,
            "mode": "factor_cross_section",
            "factors": factors,
            "top_n": top_n,
            "rebalance": base.get("rebalance") or "W-MON",
            "events": [{"type": "stop_loss_pct", "value": -0.08}],
            "notes": f"propose:improve:{h0.id}",
        })
        row = research.get_hypothesis(db, h["id"])
        row.spec_json = dumps(spec)
        row.status = "draft"
        db.commit()
        out.append(research._to_dict(row))

    # 停用研究臂
    if len(out) < count:
        for m in (
            db.query(Model)
            .filter(Model.type == "rule", Model.model_id.like("res_%"), Model.enabled.is_(False))
            .limit(count)
            .all()
        ):
            if len(out) >= count:
                break
            title = f"重启变体·{m.name[:16]}"
            theory = f"针对已退役账户 {m.model_id} 的参数微扰提议，请重新回测。"
            h = research.create_hypothesis(db, theory, title=title)
            out.append(h)
    return out


