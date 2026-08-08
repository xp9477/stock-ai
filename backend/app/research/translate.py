"""NL → 结构化规格：优先 LLM，失败回退启发式。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..agents import llm
from ..models import Model
from .spec import default_spec, heuristic_from_theory, validate_spec

logger = logging.getLogger(__name__)

_SYSTEM = """你是量化策略规格翻译器。把用户的投资理论译成 JSON 策略规格（不要 markdown）。
只输出一个 JSON 对象，字段：
{
  "name": "短标题",
  "mode": "factor_cross_section 或 equal_weight",
  "universe": "pool",
  "factors": ["mom_short","mom_mid","low_vol","ep","bp","quality_roe","rev_1m","low_turn","growth_roe" 的子集],
  "top_n": 10,
  "rebalance": "W-MON 或 ME",
  "events": [{"type":"stop_loss_pct","value":-0.08},{"type":"take_profit_pct","value":0.15},{"type":"ma_exit","window":20}],
  "unsupported": ["无法映射的能力说明"],
  "notes": "一句说明"
}
约束：universe 固定 pool；不可编造未列出的因子；做不到的放进 unsupported。
"""


def _first_llm_model_id(db) -> str | None:
    m = (
        db.query(Model)
        .filter(Model.enabled.is_(True), Model.type == "llm")
        .order_by(Model.id)
        .first()
    )
    return m.model_id if m else None


def _extract_json_obj(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    # 整段 JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    # 最大花括号块
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def translate_theory(db, theory: str, title: str = "") -> dict[str, Any]:
    """返回 {spec, source: llm|heuristic, raw?, errors}。"""
    theory = (theory or "").strip()
    if not theory:
        spec = default_spec(title)
        return {"spec": spec, "source": "heuristic", "errors": ["理论文本为空"]}

    model_id = _first_llm_model_id(db)
    if model_id:
        try:
            raw = llm.chat(_SYSTEM, theory[:4000], model_id, retries=1)
            data = _extract_json_obj(raw)
            if data:
                if title and not data.get("name"):
                    data["name"] = title
                spec, errors = validate_spec(data)
                return {
                    "spec": spec,
                    "source": "llm",
                    "raw": raw[:2000],
                    "errors": errors,
                }
        except Exception as err:  # noqa: BLE001
            logger.warning("LLM 翻译失败，回退启发式: %s", err)

    spec = heuristic_from_theory(theory, title=title)
    return {"spec": spec, "source": "heuristic", "errors": []}
