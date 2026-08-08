"""规则库：可组合积木（因子 / 调仓 / 事件 / 预设）。网格与 AI 提议的原材料。"""
from __future__ import annotations

from typing import Any

from ..factors.definitions import FACTOR_NAMES

FACTOR_META: list[dict[str, str]] = [
    {"id": "mom_short", "label": "短动量", "group": "动量"},
    {"id": "mom_mid", "label": "中动量", "group": "动量"},
    {"id": "low_vol", "label": "低波动", "group": "风险"},
    {"id": "ep", "label": "EP 盈利收益", "group": "估值"},
    {"id": "bp", "label": "BP 账面市值", "group": "估值"},
    {"id": "quality_roe", "label": "ROE 质量", "group": "质量"},
    {"id": "rev_1m", "label": "一月反转", "group": "反转"},
    {"id": "low_turn", "label": "低换手", "group": "流动性"},
    {"id": "growth_roe", "label": "ROE 改善", "group": "成长"},
]

EVENT_META: list[dict[str, Any]] = [
    {"type": "stop_loss_pct", "label": "固定止损 %", "params": ["value"]},
    {"type": "take_profit_pct", "label": "固定止盈 %", "params": ["value"]},
    {"type": "ma_exit", "label": "跌破 N 日均线出清", "params": ["window"]},
    {"type": "hold_max_days", "label": "持有满 N 日出清", "params": ["days"]},
]

REBALANCE_META = [
    {"id": "W-MON", "label": "每周一"},
    {"id": "W-FRI", "label": "每周五（对齐到周）"},
    {"id": "ME", "label": "每月末（月内首个交易日近似）"},
]

# 预设因子组合（网格默认种子）
PRESET_FACTOR_SETS: list[dict[str, Any]] = [
    {"id": "s2_full", "label": "S2 六因子", "factors": list(FACTOR_NAMES[:6])},
    {"id": "mom", "label": "纯动量", "factors": ["mom_short", "mom_mid"]},
    {"id": "value", "label": "估值", "factors": ["ep", "bp"]},
    {"id": "quality", "label": "质量+成长", "factors": ["quality_roe", "growth_roe"]},
    {"id": "mom_quality", "label": "动量+质量", "factors": ["mom_short", "mom_mid", "quality_roe"]},
    {"id": "low_risk", "label": "低波低换手", "factors": ["low_vol", "low_turn"]},
    {"id": "rev_value", "label": "反转+估值", "factors": ["rev_1m", "ep", "bp"]},
]


def get_library() -> dict[str, Any]:
    return {
        "factors": [f for f in FACTOR_META if f["id"] in FACTOR_NAMES],
        "events": EVENT_META,
        "rebalances": REBALANCE_META,
        "modes": [
            {"id": "factor_cross_section", "label": "因子截面 TopN"},
            {"id": "equal_weight", "label": "池内等权"},
        ],
        "presets": PRESET_FACTOR_SETS,
        "defaults": {
            "top_n_options": [5, 8, 10, 15, 20],
            "stop_loss_options": [None, -0.05, -0.08, -0.12],
            "max_grid_combos": 48,
        },
        "notes": "研究层批量试组合；仅存活者（建议+人确认）开前瞻账户。",
    }
