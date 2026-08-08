"""每日自动选股:规则初筛 + 一次 LLM 精选,维护共享股池生命周期。"""
import json
import logging
import re
from datetime import datetime

from sqlalchemy.orm import Session

from ..data import market
from ..models import AgentOutput, Model, Position, Run, Watchlist
from ..runtime_settings import get_setting
from . import llm

logger = logging.getLogger(__name__)

_select_lock = False


def is_selecting() -> bool:
    return bool(_select_lock)


def parse_selector_json(text: str) -> dict | None:
    """从 LLM 输出中提取 {picks: [...], keep: [...]},失败返回 None。"""
    decoder = json.JSONDecoder()
    # 从每个含 "picks" 键的 { 位置尝试 raw_decode(取最后一个成功的)
    result = None
    for match in re.finditer(r"\{", text):
        start = match.start()
        if '"picks"' not in text[start:start + 200]:
            continue
        try:
            data, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "picks" in data:
            result = data
    return _normalize(result) if result is not None else None


def _normalize(data: dict) -> dict | None:
    picks = []
    for item in data.get("picks") or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        if len(code) == 6 and code.isdigit():
            picks.append({"code": code, "reason": str(item.get("reason", ""))[:200]})
    keep = [str(c).strip() for c in (data.get("keep") or [])
            if len(str(c).strip()) == 6 and str(c).strip().isdigit()]
    return {"picks": picks, "keep": keep}


def _candidates_text(candidates: list[dict]) -> str:
    lines = ["代码, 名称, 现价, 今日涨跌幅%, 成交额(亿)"]
    for c in candidates:
        lines.append(f"{c['code']}, {c['name']}, {c['price']:.2f}, "
                     f"{c['pct_change']:+.2f}, {c['turnover'] / 1e8:.1f}")
    return "\n".join(lines)


def _auto_pool_text(auto_items: list[Watchlist]) -> str:
    if not auto_items:
        return "(暂无 AI 自动跟踪股)"
    lines = []
    for item in auto_items:
        lines.append(f"- {item.code} {item.name}(此前选入理由: {item.select_reason or '无'},"
                     f"已连续 {item.miss_count} 次未被看好)")
    return "\n".join(lines)


def _apply_lifecycle(db: Session, auto_items: list[Watchlist],
                     favored: set[str]) -> list[str]:
    """更新 miss_count 并淘汰;返回被移除的代码列表。favored=本日被看好的代码集合。"""
    removed = []
    for item in auto_items:
        if item.code in favored:
            item.miss_count = 0
            continue
        item.miss_count += 1
        held = db.query(Position).filter(Position.code == item.code,
                                         Position.total_qty > 0).first()
        if item.miss_count >= get_setting("selector.miss_limit") and held is None:
            removed.append(item.code)
            db.delete(item)
    db.commit()
    return removed


def run_selector(trigger: str = "schedule") -> int | None:
    """执行一次自动选股。返回 run_id;并发冲突或无可用模型时返回 None。"""
    global _select_lock
    from . import engine as engine_mod

    if _select_lock or engine_mod.is_running():
        logger.warning("决策或选股流程运行中,跳过本次选股")
        return None
    _select_lock = True

    from ..database import SessionLocal

    db = SessionLocal()
    run = Run(trigger="selector")
    db.add(run)
    db.commit()
    run_id = run.id
    try:
        model = (db.query(Model)
                 .filter(Model.enabled.is_(True), Model.type == "llm")
                 .order_by(Model.id).first())
        if model is None:
            run.status = "failed"
            run.error = "无启用的 LLM 模型"
            return None

        pool = db.query(Watchlist).all()
        auto_items = [w for w in pool if w.source == "auto"]
        pool_codes = {w.code for w in pool}
        slots = max(int(get_setting("selector.pool_max")) - len(pool), 0)

        candidates = market.screen_candidates(exclude_codes=pool_codes)
        market_ctx = market.market_overview_text()
        selector_prompt = str(get_setting("prompt.selector"))

        user_input = (
            f"【大盘环境】\n{market_ctx}\n\n"
            f"【今日活跃候选股(已初筛)】\n{_candidates_text(candidates)}\n\n"
            f"【股池现有 AI 自动跟踪股】\n{_auto_pool_text(auto_items)}\n\n"
            f"【股池剩余空位】{slots} 个"
            + ("(股池已满,本日只需评估 keep,picks 请输出空数组)" if slots == 0 else "")
        )

        output = llm.chat(selector_prompt, user_input, model.model_id, retries=1)
        parsed = parse_selector_json(output)
        if parsed is None:
            # 重试一次
            output2 = llm.chat(selector_prompt, user_input, model.model_id, retries=0)
            parsed = parse_selector_json(output2)
            output = output + "\n\n[重试输出]\n" + output2
        db.add(AgentOutput(run_id=run_id, model_pk=model.id, code="SELECT",
                           agent="selector", input_summary=user_input[:2000],
                           output=output))
        db.commit()
        if parsed is None:
            run.status = "failed"
            run.error = "选股 JSON 解析失败,本日跳过"
            logger.warning("选股 JSON 解析失败,跳过本日选股")
            return run_id

        # 入池(截断到剩余空位;过滤不在候选中的臆造代码)
        candidate_map = {c["code"]: c for c in candidates}
        added = []
        for pick in parsed["picks"][:slots]:
            code = pick["code"]
            if code in pool_codes or code not in candidate_map:
                continue
            db.add(Watchlist(code=code, name=candidate_map[code]["name"],
                             source="auto", miss_count=0,
                             select_reason=pick["reason"]))
            added.append(code)
        db.commit()

        favored = set(parsed["keep"]) | {p["code"] for p in parsed["picks"]}
        removed = _apply_lifecycle(db, auto_items, favored)
        logger.info("选股完成: 新增 %s, 移除 %s", added or "无", removed or "无")
        run.status = "done"
        run.result_json = json.dumps({
            "kind": "selector",
            "model": model.name,
            "added": added,
            "removed": removed,
            "kept": list(parsed.get("keep") or [])[:30],
            "slots_before": slots,
            "candidate_n": len(candidates),
            "pool_size": db.query(Watchlist).count(),
        }, ensure_ascii=False)
    except Exception as err:  # noqa: BLE001
        logger.exception("自动选股失败")
        run.status = "failed"
        # Broken pipe / 超时等网络错误 → 可读文案
        raw = str(err)
        if "Broken pipe" in raw or "Errno 32" in raw:
            run.error = (
                "选股行情连接中断 (Broken pipe)。"
                "已优先改用扶摇指数成分快照；请重试 AI 选股。"
                f" 原始错误: {raw}"
            )
        else:
            run.error = raw
    finally:
        run.finished_at = datetime.now()
        db.commit()
        db.close()
        _select_lock = False
    return run_id
