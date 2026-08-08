"""结构化策略规格：可回测真源（NL 仅作进料）。"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from ..factors.definitions import FACTOR_NAMES

ALLOWED_FACTORS = set(FACTOR_NAMES)
ALLOWED_REBALANCE = {"W-MON", "W-FRI", "ME", "MS"}  # 周/月末
ALLOWED_EVENT_TYPES = {
    "stop_loss_pct",
    "take_profit_pct",
    "ma_exit",  # 收盘跌破均线则出清
    "hold_max_days",  # 持有满 N 交易日出清
}


def default_spec(title: str = "") -> dict[str, Any]:
    return {
        "name": title or "未命名策略",
        "universe": "pool",
        "mode": "factor_cross_section",  # factor_cross_section | equal_weight
        "factors": list(FACTOR_NAMES[:6]),  # 默认 S2 六因子
        "top_n": 10,
        "rebalance": "W-MON",
        "weighting": "equal",
        "events": [],
        "unsupported": [],
        "notes": "",
    }


def validate_spec(raw: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """校验并规范化规格；返回 (spec, errors)。errors 非空则不可回测。"""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return default_spec(), ["规格必须是 JSON 对象"]

    spec = default_spec(str(raw.get("name") or ""))
    spec["name"] = str(raw.get("name") or spec["name"])[:80]
    mode = str(raw.get("mode") or "factor_cross_section").strip()
    if mode not in ("factor_cross_section", "equal_weight"):
        errors.append(f"不支持的 mode: {mode}")
        mode = "factor_cross_section"
    spec["mode"] = mode

    universe = str(raw.get("universe") or "pool").strip()
    if universe != "pool":
        # v1 仅股池
        spec["unsupported"] = list(raw.get("unsupported") or []) + [f"universe={universe}"]
        universe = "pool"
    spec["universe"] = universe

    factors = raw.get("factors")
    if factors is None:
        factors = list(spec["factors"])
    if not isinstance(factors, list):
        errors.append("factors 须为数组")
        factors = []
    clean_f = []
    for f in factors:
        fs = str(f).strip()
        if fs in ALLOWED_FACTORS:
            clean_f.append(fs)
        elif fs:
            spec.setdefault("unsupported", []).append(f"factor:{fs}")
    if mode == "factor_cross_section" and not clean_f:
        clean_f = list(FACTOR_NAMES[:6])
    spec["factors"] = clean_f

    try:
        top_n = int(raw.get("top_n") or 10)
    except (TypeError, ValueError):
        top_n = 10
        errors.append("top_n 非法")
    spec["top_n"] = max(1, min(top_n, 50))

    reb = str(raw.get("rebalance") or "W-MON").strip()
    if reb not in ALLOWED_REBALANCE:
        # 尝试规范化
        low = reb.lower()
        if "month" in low or reb in ("M", "month"):
            reb = "ME"
        elif "week" in low or "周" in reb:
            reb = "W-MON"
        else:
            spec.setdefault("unsupported", []).append(f"rebalance:{reb}")
            reb = "W-MON"
    spec["rebalance"] = reb
    spec["weighting"] = "equal"

    events_in = raw.get("events") or []
    events: list[dict[str, Any]] = []
    if isinstance(events_in, list):
        for ev in events_in:
            if not isinstance(ev, dict):
                continue
            et = str(ev.get("type") or "").strip()
            if et not in ALLOWED_EVENT_TYPES:
                if et:
                    spec.setdefault("unsupported", []).append(f"event:{et}")
                continue
            item: dict[str, Any] = {"type": et}
            if et in ("stop_loss_pct", "take_profit_pct"):
                try:
                    item["value"] = float(ev.get("value"))
                except (TypeError, ValueError):
                    continue
            if et == "ma_exit":
                try:
                    item["window"] = int(ev.get("window") or 20)
                except (TypeError, ValueError):
                    item["window"] = 20
                item["window"] = max(2, min(item["window"], 120))
            if et == "hold_max_days":
                try:
                    item["days"] = int(ev.get("days") or ev.get("value") or 20)
                except (TypeError, ValueError):
                    item["days"] = 20
                item["days"] = max(1, min(item["days"], 250))
            events.append(item)
    spec["events"] = events
    if raw.get("unsupported"):
        extra = raw["unsupported"] if isinstance(raw["unsupported"], list) else []
        spec["unsupported"] = list(dict.fromkeys(
            list(spec.get("unsupported") or []) + [str(x) for x in extra]
        ))
    spec["notes"] = str(raw.get("notes") or "")[:500]
    return spec, errors


def heuristic_from_theory(theory: str, title: str = "") -> dict[str, Any]:
    """无 LLM 时的规则翻译（可测、可离线）。"""
    text = theory or ""
    spec = default_spec(title or _guess_title(text))
    factors: list[str] = []
    if re.search(r"动量|momentum|涨得好|强势", text, re.I):
        factors += ["mom_short", "mom_mid"]
    if re.search(r"低波|低波动|稳", text):
        factors.append("low_vol")
    if re.search(r"估值|PE|EP|便宜|低市盈", text, re.I):
        factors.append("ep")
    if re.search(r"BP|账面|破净", text, re.I):
        factors.append("bp")
    if re.search(r"ROE|质量|盈利能力", text, re.I):
        factors.append("quality_roe")
    if re.search(r"反转|超跌", text):
        factors.append("rev_1m")
    if re.search(r"低换手|冷门", text):
        factors.append("low_turn")
    if re.search(r"成长|ROE\s*改善", text, re.I):
        factors.append("growth_roe")
    if re.search(r"等权|躺平|不选股|全持有", text):
        spec["mode"] = "equal_weight"
        factors = []
    if factors:
        # 去重保序
        seen = set()
        clean = []
        for f in factors:
            if f not in seen:
                seen.add(f)
                clean.append(f)
        spec["factors"] = clean
        spec["mode"] = "factor_cross_section"

    m = re.search(r"(?:前|top\s*)(\d{1,2})", text, re.I)
    if m:
        spec["top_n"] = max(1, min(int(m.group(1)), 50))
    m2 = re.search(r"(\d{1,2})\s*只", text)
    if m2:
        spec["top_n"] = max(1, min(int(m2.group(1)), 50))

    if re.search(r"月|每月", text):
        spec["rebalance"] = "ME"
    elif re.search(r"周|每周", text):
        spec["rebalance"] = "W-MON"

    events = []
    m = re.search(r"止损\s*(\d+(?:\.\d+)?)\s*%", text)
    if m:
        events.append({"type": "stop_loss_pct", "value": -abs(float(m.group(1)) / 100)})
    m = re.search(r"止盈\s*(\d+(?:\.\d+)?)\s*%", text)
    if m:
        events.append({"type": "take_profit_pct", "value": abs(float(m.group(1)) / 100)})
    m = re.search(r"(\d{1,3})\s*日均线", text)
    if m:
        events.append({"type": "ma_exit", "window": int(m.group(1))})
    spec["events"] = events
    spec["notes"] = "heuristic"
    out, _ = validate_spec(spec)
    return out


def _guess_title(text: str) -> str:
    line = (text or "").strip().split("\n")[0].strip()
    return (line[:40] + "…") if len(line) > 40 else (line or "未命名假说")


def dumps(spec: dict[str, Any]) -> str:
    return json.dumps(spec, ensure_ascii=False)


def loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return default_spec()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return default_spec()
    spec, _ = validate_spec(data if isinstance(data, dict) else {})
    return spec
