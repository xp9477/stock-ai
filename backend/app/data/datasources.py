"""数据源控制台：固定角色、启停/超时/失败策略、健康探测。

角色写死（不可拖拽重排）：
  fuyao   — 主行情/财务、选股截面主源
  sina    — 选股全市场兜底 + 指数/交易日历（AKShare，无 Key）
  tencent — 实时行情与日 K
  tushare — 可选备份
  rss     — 公开新闻 RSS
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from ..runtime_settings import get_setting

logger = logging.getLogger(__name__)

# fail_policy 合法值
FAIL_POLICIES = frozenset({"fallback", "hard", "skip"})

# 固定角色元数据（UI / API 展示）
SOURCE_META: list[dict[str, str]] = [
    {
        "id": "fuyao",
        "label": "扶摇",
        "role": "主行情/财务 · 选股截面主源",
        "needs_key": "true",
        "key_setting": "secrets.fuyao_api_key",
    },
    {
        "id": "sina",
        "label": "新浪",
        "role": "选股全市场兜底 + 指数/交易日历（AKShare，无 Key）",
        "needs_key": "false",
        "key_setting": "",
    },
    {
        "id": "tencent",
        "label": "腾讯",
        "role": "实时行情与日 K",
        "needs_key": "false",
        "key_setting": "",
    },
    {
        "id": "tushare",
        "label": "Tushare",
        "role": "可选备份数据源",
        "needs_key": "true",
        "key_setting": "secrets.tushare_token",
    },
    {
        "id": "rss",
        "label": "新闻 RSS",
        "role": "公开 RSS 资讯",
        "needs_key": "false",
        "key_setting": "",
    },
]


def is_enabled(source_id: str) -> bool:
    try:
        return bool(get_setting(f"datasources.{source_id}.enabled"))
    except Exception:  # noqa: BLE001
        return True


def timeout_sec(source_id: str, default: int = 15) -> int:
    try:
        return int(get_setting(f"datasources.{source_id}.timeout_sec"))
    except Exception:  # noqa: BLE001
        return default


def fail_policy(source_id: str, default: str = "fallback") -> str:
    try:
        p = str(get_setting(f"datasources.{source_id}.fail_policy") or default).strip().lower()
    except Exception:  # noqa: BLE001
        return default
    return p if p in FAIL_POLICIES else default


def _probe_fuyao() -> dict[str, Any]:
    from . import fuyao_client as fuyao

    if not fuyao.available():
        return {"ok": False, "detail": "未配置 API Key"}
    if not is_enabled("fuyao"):
        return {"ok": False, "detail": "已禁用"}
    # 轻量：仅验证鉴权头能打到网关（用非法 thscode 也可能 0 数据但 code=0）
    t0 = time.perf_counter()
    try:
        fuyao.prices_snapshot(["600519"])
        ms = int((time.perf_counter() - t0) * 1000)
        return {"ok": True, "detail": f"snapshot 探测成功 ({ms}ms)", "latency_ms": ms}
    except Exception as err:  # noqa: BLE001
        return {"ok": False, "detail": str(err)[:200]}


def _probe_sina() -> dict[str, Any]:
    if not is_enabled("sina"):
        return {"ok": False, "detail": "已禁用"}
    import akshare as ak

    t0 = time.perf_counter()
    try:
        # 交易日历比全市场快照轻
        df = ak.tool_trade_date_hist_sina()
        ms = int((time.perf_counter() - t0) * 1000)
        n = len(df) if df is not None else 0
        return {"ok": n > 0, "detail": f"交易日历 {n} 行 ({ms}ms)", "latency_ms": ms}
    except Exception as err:  # noqa: BLE001
        return {"ok": False, "detail": str(err)[:200]}


def _probe_tencent() -> dict[str, Any]:
    if not is_enabled("tencent"):
        return {"ok": False, "detail": "已禁用"}
    import requests

    t0 = time.perf_counter()
    try:
        to = timeout_sec("tencent", 10)
        resp = requests.get("https://qt.gtimg.cn/q=sh600519", timeout=to)
        resp.encoding = "gbk"
        ok = '="' in (resp.text or "") and "贵州茅台" in resp.text
        ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": ok,
            "detail": f"行情探测{'成功' if ok else '异常'} ({ms}ms)",
            "latency_ms": ms,
        }
    except Exception as err:  # noqa: BLE001
        return {"ok": False, "detail": str(err)[:200]}


def _probe_tushare() -> dict[str, Any]:
    token = str(get_setting("secrets.tushare_token") or "").strip()
    if not is_enabled("tushare"):
        return {"ok": False, "detail": "已禁用"}
    if not token:
        return {"ok": False, "detail": "未配置 Token"}
    return {"ok": True, "detail": "Token 已配置（未发起远程调用）"}


def _probe_rss() -> dict[str, Any]:
    if not is_enabled("rss"):
        return {"ok": False, "detail": "已禁用"}
    from urllib.request import Request, urlopen

    from .news_rss import RSS_FEEDS

    if not RSS_FEEDS:
        return {"ok": False, "detail": "无 RSS 源"}
    feed = RSS_FEEDS[0]
    t0 = time.perf_counter()
    try:
        to = timeout_sec("rss", 12)
        req = Request(feed["url"], headers={"User-Agent": "stock-ai/1.0"})
        with urlopen(req, timeout=to) as resp:  # noqa: S310
            data = resp.read(2048)
        ms = int((time.perf_counter() - t0) * 1000)
        ok = bool(data)
        return {
            "ok": ok,
            "detail": f"{feed.get('name', feed['id'])} {'可达' if ok else '空'} ({ms}ms)",
            "latency_ms": ms,
        }
    except Exception as err:  # noqa: BLE001
        return {"ok": False, "detail": str(err)[:200]}


_PROBES: dict[str, Callable[[], dict[str, Any]]] = {
    "fuyao": _probe_fuyao,
    "sina": _probe_sina,
    "tencent": _probe_tencent,
    "tushare": _probe_tushare,
    "rss": _probe_rss,
}


def list_sources_status() -> list[dict[str, Any]]:
    """配置态快照（不含网络探测）。"""
    out = []
    for meta in SOURCE_META:
        sid = meta["id"]
        key_ok = True
        if meta.get("key_setting"):
            key_ok = bool(str(get_setting(meta["key_setting"]) or "").strip())
        out.append({
            "id": sid,
            "label": meta["label"],
            "role": meta["role"],
            "needs_key": meta["needs_key"] == "true",
            "enabled": is_enabled(sid),
            "timeout_sec": timeout_sec(sid),
            "fail_policy": fail_policy(sid),
            "key_configured": key_ok if meta.get("key_setting") else None,
        })
    return out


def probe_all(source_id: str | None = None) -> list[dict[str, Any]]:
    """健康探测。source_id 为空则探测全部。"""
    ids = [source_id] if source_id else [m["id"] for m in SOURCE_META]
    results = []
    for sid in ids:
        meta = next((m for m in SOURCE_META if m["id"] == sid), None)
        if meta is None:
            results.append({"id": sid, "ok": False, "detail": "未知数据源"})
            continue
        probe = _PROBES.get(sid)
        base = {
            "id": sid,
            "label": meta["label"],
            "role": meta["role"],
            "enabled": is_enabled(sid),
            "timeout_sec": timeout_sec(sid),
            "fail_policy": fail_policy(sid),
        }
        if probe is None:
            results.append({**base, "ok": False, "detail": "无探测实现"})
            continue
        try:
            result = probe()
        except Exception as err:  # noqa: BLE001
            result = {"ok": False, "detail": str(err)[:200]}
        results.append({**base, **result})
    return results
