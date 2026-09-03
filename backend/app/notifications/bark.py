"""Best-effort Bark notifications for events requiring human attention.

Notification delivery is deliberately outside the trading contract: a Bark
failure is logged but can never change a Run, TradePlan, account or position.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests

from ..runtime_settings import get_setting

logger = logging.getLogger(__name__)

_dedupe_lock = threading.Lock()
_delivered: dict[str, float] = {}
_DEDUPE_TTL_SECONDS = 24 * 3600


class BarkError(RuntimeError):
    """A sanitized Bark configuration or delivery error."""


def _setting(key: str) -> Any:
    return get_setting(f"notifications.bark.{key}")


def _push_endpoint() -> str:
    raw = str(_setting("server_url") or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BarkError("Bark Server 地址必须是有效的 http(s) URL")
    if parsed.username or parsed.password:
        raise BarkError("Bark Server 地址不得包含用户名或密码")
    return raw if parsed.path.rstrip("/").endswith("/push") else f"{raw}/push"


def _click_url(path: str) -> str | None:
    base = str(_setting("open_url") or "").strip()
    if not base:
        return None
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        logger.warning("Bark 点击跳转地址无效，已忽略")
        return None
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def _already_delivered(key: str) -> bool:
    now = time.monotonic()
    with _dedupe_lock:
        expired = [item for item, at in _delivered.items()
                   if now - at >= _DEDUPE_TTL_SECONDS]
        for item in expired:
            _delivered.pop(item, None)
        return key in _delivered


def _mark_delivered(key: str) -> None:
    with _dedupe_lock:
        _delivered[key] = time.monotonic()


def send(
    title: str,
    body: str,
    *,
    level: str = "active",
    path: str = "",
    dedupe_key: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Send one Bark message and return a sanitized delivery summary."""
    if not force and not bool(_setting("enabled")):
        return {"ok": False, "skipped": True, "detail": "Bark 未启用"}
    device_key = str(_setting("device_key") or "").strip()
    if not device_key:
        raise BarkError("Bark Device Key 未配置")
    if dedupe_key and not force and _already_delivered(dedupe_key):
        return {"ok": True, "skipped": True, "detail": "重复通知已跳过"}

    payload: dict[str, Any] = {
        "device_key": device_key,
        "title": str(title).strip()[:120],
        "body": str(body).strip()[:1800],
        "group": str(_setting("group") or "stock-ai").strip()[:64],
        "level": level if level in {"critical", "active", "timeSensitive", "passive"}
        else "active",
        "isArchive": "1",
    }
    click_url = _click_url(path)
    if click_url:
        payload["url"] = click_url

    try:
        response = requests.post(
            _push_endpoint(), json=payload,
            timeout=int(_setting("timeout_sec")),
        )
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError:
            result = {}
        code = result.get("code") if isinstance(result, dict) else None
        if code not in (None, 0, 200):
            raise BarkError(f"Bark 服务拒绝请求 (code={code})")
    except BarkError:
        raise
    except requests.RequestException as error:
        # Never include request bodies: they contain the device key.
        raise BarkError(f"Bark 推送连接失败: {type(error).__name__}") from error

    if dedupe_key:
        _mark_delivered(dedupe_key)
    logger.info("Bark 推送成功 title=%s dedupe=%s", payload["title"], dedupe_key or "-")
    return {"ok": True, "skipped": False, "detail": "Bark 推送成功"}


def _safe_send(*args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return send(*args, **kwargs)
    except Exception as error:  # noqa: BLE001
        logger.warning("Bark 推送失败（不影响业务）: %s", error)
        return {"ok": False, "skipped": False, "detail": str(error)}


def send_test() -> dict[str, Any]:
    """Send even while disabled, allowing setup to be verified first."""
    return send(
        "Stock AI · Bark 测试成功",
        "通知通道已连通。以后只有候选计划、风险复审或运行异常才会提醒你。",
        level="active", path="/settings?tab=notifications", force=True,
    )


def notify_pipeline_result(
    *,
    run_id: int,
    status: str,
    result: dict[str, Any] | None,
    error: str = "",
    plans: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Notify once for actionable plans, or for a failed/degraded Run."""
    if not bool(_setting("enabled")):
        return {"ok": False, "skipped": True, "detail": "Bark 未启用"}
    result = result or {}
    plan_rows = list(plans)
    if status == "failed":
        if not bool(_setting("notify_failures")):
            return {"ok": False, "skipped": True, "detail": "失败通知已关闭"}
        return _safe_send(
            f"Stock AI · 决策失败 #{run_id}",
            f"需要检查运行日志。\n{(error or '未知错误')[:500]}",
            level="timeSensitive", path=f"/runs/{run_id}",
            dedupe_key=f"run:{run_id}:failed",
        )

    degraded = bool(result.get("degraded"))
    if plan_rows and bool(_setting("notify_candidates")):
        lines = []
        for plan in plan_rows[:8]:
            side = "买入" if plan.get("side") == "buy" else "卖出"
            line = f"{side} {plan.get('name') or plan.get('code')}({plan.get('code')})"
            if plan.get("side") == "buy" and plan.get("max_buy_price"):
                line += f" ≤ {float(plan['max_buy_price']):.2f}"
            lines.append(line)
        if len(plan_rows) > 8:
            lines.append(f"另有 {len(plan_rows) - 8} 项")
        suffix = "\n⚠️ 本轮同时存在降级，请先检查详情。" if degraded else ""
        auto_ticket = not bool(get_setting("execution.require_manual_confirmation"))
        auto_fill = bool(get_setting("execution.auto_fill_tickets"))
        if auto_fill and auto_ticket:
            title = f"Stock AI · {len(plan_rows)} 笔模拟成交"
            lead = "机器门禁已通过，已在本系统模拟盘自动成交。东财客户端不会出现委托。\n"
        elif auto_ticket:
            title = f"Stock AI · {len(plan_rows)} 张待执行票据"
            lead = "机器门禁已通过，已自动生成票据，尚未向券商下单。\n"
        else:
            title = f"Stock AI · {len(plan_rows)} 个候选计划待确认"
            lead = "需要你打开订单页：核对公告 → 刷新价格门禁 → 决定批准或拒绝。\n"
        body = lead + "\n".join(lines) + suffix
        return _safe_send(
            title,
            body, level="timeSensitive", path="/orders",
            dedupe_key=f"run:{run_id}:candidates",
        )

    if degraded and bool(_setting("notify_failures")):
        failed = int(result.get("independent_judgment_failed") or 0)
        errors = result.get("decision_error_counts") or {}
        return _safe_send(
            f"Stock AI · 决策降级 #{run_id}",
            f"没有待审批计划，但运行存在降级。独立判断失败 {failed}，决策错误 {errors}。",
            level="active", path=f"/runs/{run_id}",
            dedupe_key=f"run:{run_id}:degraded",
        )
    return {"ok": False, "skipped": True, "detail": "没有需要人工介入的事件"}


def notify_monitor_event(event: Any) -> dict[str, Any]:
    if not bool(_setting("notify_risk_reviews")):
        return {"ok": False, "skipped": True, "detail": "风险通知已关闭"}
    pnl = float(getattr(event, "pnl_pct", 0.0) or 0.0)
    action = str(getattr(event, "action", "") or "")
    urgent = action == "review_required" or str(getattr(event, "trigger", "")) == "deep_loss"
    return _safe_send(
        f"Stock AI · 持仓复审 {getattr(event, 'name', '') or getattr(event, 'code', '')}",
        (
            f"{getattr(event, 'code', '')} 浮动盈亏 {pnl:+.1%}\n"
            f"事件：{getattr(event, 'trigger', '')} / {action}\n"
            "需要打开系统查看复审依据；系统尚未自动成交。"
        ),
        level="timeSensitive" if urgent else "active",
        path="/orders",
        dedupe_key=f"monitor:{getattr(event, 'id', '')}:{action}",
    )


def notify_selector_failure(run_id: int, error: str) -> dict[str, Any]:
    if not bool(_setting("notify_failures")):
        return {"ok": False, "skipped": True, "detail": "失败通知已关闭"}
    return _safe_send(
        f"Stock AI · 自动选股失败 #{run_id}",
        f"需要检查数据源或模型。\n{(error or '未知错误')[:500]}",
        level="active", path=f"/runs/{run_id}",
        dedupe_key=f"selector:{run_id}:failed",
    )
