"""运行时配置：注册表默认值 + .env 回退 + DB 覆盖层。"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .settings_registry import REGISTRY, SettingDef, get_def, list_defs

logger = logging.getLogger(__name__)

# 进程内缓存：key -> 已 coerce 的有效值；写操作后 invalidate
_cache: dict[str, Any] | None = None
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

# secret 写入时的「清空」哨兵（前端可选）
SECRET_CLEAR = "__CLEAR__"


def invalidate_cache() -> None:
    global _cache
    _cache = None


def _env_fallback(key: str) -> Any | None:
    """无 DB 覆盖时，从 pydantic Settings / .env 取值。"""
    from .config import settings

    mapping = {
        "secrets.llm_base_url": settings.llm_base_url,
        "secrets.llm_api_key": settings.llm_api_key,
        "secrets.llm_temperature": settings.llm_temperature,
        "secrets.fuyao_api_key": settings.fuyao_api_key,
        "secrets.tushare_token": settings.tushare_token,
        "account.initial_cash": settings.initial_cash,
        "trading.commission_rate": settings.commission_rate,
        "trading.commission_min": settings.commission_min,
        "trading.stamp_tax_rate": settings.stamp_tax_rate,
        "trading.transfer_fee_rate": settings.transfer_fee_rate,
        "schedule.enabled": settings.schedule_enabled,
        "schedule.morning_decision_time": settings.morning_decision_time,
        "schedule.daily_decision_time": settings.daily_decision_time,
        "schedule.stock_select_enabled": settings.stock_select_enabled,
        "schedule.stock_select_time": settings.stock_select_time,
        "schedule.monitor_interval_minutes": settings.monitor_interval_minutes,
    }
    if key not in mapping:
        return None
    return mapping[key]


def _is_secret(defn: SettingDef) -> bool:
    return defn.secret or defn.type == "secret"


def mask_secret(value: str) -> str:
    """脱敏展示：保留末 4 位。"""
    s = (value or "").strip()
    if not s:
        return ""
    if len(s) <= 4:
        return "••••"
    return "••••••••" + s[-4:]


def _coerce(defn: SettingDef, raw: Any) -> Any:
    """把原始值转为类型安全的 Python 值。"""
    if defn.type == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        s = str(raw).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"{defn.key}: 无法解析布尔值 {raw!r}")

    if defn.type == "int":
        v = int(float(raw))
        if defn.min_value is not None and v < defn.min_value:
            raise ValueError(f"{defn.key}: 不能小于 {defn.min_value}")
        if defn.max_value is not None and v > defn.max_value:
            raise ValueError(f"{defn.key}: 不能大于 {defn.max_value}")
        return v

    if defn.type in ("float", "percent"):
        v = float(raw)
        if defn.min_value is not None and v < defn.min_value:
            raise ValueError(f"{defn.key}: 不能小于 {defn.min_value}")
        if defn.max_value is not None and v > defn.max_value:
            raise ValueError(f"{defn.key}: 不能大于 {defn.max_value}")
        return v

    if defn.type == "time":
        s = str(raw).strip()
        if not _TIME_RE.match(s):
            raise ValueError(f"{defn.key}: 时间格式须为 HH:MM，收到 {raw!r}")
        h, m = s.split(":")
        return f"{int(h):02d}:{int(m):02d}"

    # str / text / secret
    s = str(raw)
    if defn.type == "text" and not s.strip():
        raise ValueError(f"{defn.key}: 文本不能为空")
    if _is_secret(defn):
        return s.strip()
    s = s.strip() if defn.type == "str" else s
    # 数据源失败策略白名单
    if defn.key.endswith(".fail_policy"):
        allowed = {"fallback", "hard", "skip"}
        low = s.lower()
        if low not in allowed:
            raise ValueError(f"{defn.key}: 须为 fallback / hard / skip，收到 {raw!r}")
        return low
    if defn.key == "logs.min_level":
        low = s.upper()
        if low not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError(f"{defn.key}: 须为 DEBUG/INFO/WARNING/ERROR")
        return low
    return s


def _serialize(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _load_override_map(db: Session | None = None) -> dict[str, str]:
    """从 DB 读取全部覆盖（原始字符串）。表不存在时回退空（用默认值）。"""
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from .database import SessionLocal
    from .models import SettingOverride

    own = db is None
    if own:
        db = SessionLocal()
    try:
        rows = db.query(SettingOverride).all()
        return {r.key: r.value for r in rows}
    except (OperationalError, ProgrammingError) as err:
        logger.debug("setting_overrides 不可用,使用默认配置: %s", err)
        return {}
    finally:
        if own:
            db.close()


def _resolve_one(key: str, defn: SettingDef, overrides: dict[str, str]) -> Any:
    # Non-editable entries are code-owned invariants/contracts.  Old database
    # overrides (or a stale .env value) must not silently weaken them after an
    # upgrade merely because the row predates the read-only flag.
    if not defn.editable:
        return _coerce(defn, defn.default)
    if key in overrides:
        try:
            return _coerce(defn, overrides[key])
        except ValueError:
            logger.warning("配置 %s 覆盖值非法，回退默认", key)
    fb = _env_fallback(key)
    if fb is not None:
        # 空字符串密钥不算有效 env 值，继续走 default
        if _is_secret(defn) and not str(fb).strip():
            return defn.default
        try:
            return _coerce(defn, fb)
        except ValueError:
            pass
    return defn.default


def _effective_map(db: Session | None = None) -> dict[str, Any]:
    global _cache
    if _cache is not None and db is None:
        return _cache
    overrides = _load_override_map(db)
    result: dict[str, Any] = {}
    for key, defn in REGISTRY.items():
        result[key] = _resolve_one(key, defn, overrides)
    if db is None:
        _cache = result
    return result


def get_setting(key: str, db: Session | None = None) -> Any:
    """读取有效配置（DB 覆盖 > .env > 注册表默认）。"""
    get_def(key)
    return _effective_map(db)[key]


def is_overridden(key: str, db: Session | None = None) -> bool:
    defn = get_def(key)
    return defn.editable and key in _load_override_map(db)


def secret_source(key: str, db: Session | None = None) -> str:
    """none | env | override — 供 UI 展示密钥来源。"""
    defn = get_def(key)
    overrides = _load_override_map(db)
    if key in overrides and str(overrides[key]).strip():
        return "override"
    fb = _env_fallback(key)
    if fb is not None and str(fb).strip():
        return "env"
    if not _is_secret(defn) and fb is not None:
        return "env"
    return "none"


def list_settings(group: str | None = None, db: Session | None = None) -> list[dict]:
    """供 API：按分组返回完整元数据 + 当前值（secret 脱敏）。"""
    overrides = _load_override_map(db)
    effective = _effective_map(db)
    out = []
    for defn in list_defs(group):
        raw = effective[defn.key]
        item = {
            "key": defn.key,
            "group": defn.group,
            "type": defn.type,
            "label": defn.label,
            "description": defn.description,
            "unit": defn.unit,
            "editable": defn.editable,
            "requires_scheduler_reload": defn.requires_scheduler_reload,
            "min_value": defn.min_value,
            "max_value": defn.max_value,
            "step": defn.step,
            "precision": defn.precision,
            "secret": _is_secret(defn),
            "overridden": defn.editable and defn.key in overrides,
            "danger": getattr(defn, "danger", "normal") or "normal",
            "evidence_role": getattr(defn, "evidence_role", "operational") or "operational",
        }
        if _is_secret(defn):
            configured = bool(str(raw or "").strip())
            item["value"] = ""  # 永不回传明文
            item["default"] = ""
            item["configured"] = configured
            item["masked"] = mask_secret(str(raw)) if configured else ""
            item["source"] = secret_source(defn.key, db) if configured else "none"
        else:
            item["value"] = raw
            item["default"] = defn.default
            item["configured"] = True
            item["masked"] = ""
            item["source"] = "fixed" if not defn.editable else (
                "override" if defn.key in overrides else (
                "env" if _env_fallback(defn.key) is not None else "default"
                )
            )
        out.append(item)
    return out


def _on_secrets_changed(keys: list[str]) -> None:
    """密钥变更后重置 LLM 客户端缓存。"""
    if any(k.startswith("secrets.llm_") for k in keys):
        try:
            from .agents import llm
            llm.reset_client()
        except Exception:  # noqa: BLE001
            logger.debug("reset llm client failed", exc_info=True)


def set_settings(updates: dict[str, Any], db: Session) -> dict:
    """批量写入覆盖。返回 {updated, reload_scheduler, skipped}。

    secret 字段：空字符串 = 跳过不修改；值为 __CLEAR__ = 删除覆盖回退 .env。
    """
    from .models import SettingOverride

    if not updates:
        return {"updated": [], "skipped": [], "reload_scheduler": False}

    updated: list[str] = []
    skipped: list[str] = []
    reload = False
    for key, raw in updates.items():
        defn = get_def(key)
        if not defn.editable:
            raise ValueError(f"{key} 不可编辑")

        if _is_secret(defn):
            s = "" if raw is None else str(raw).strip()
            if not s:
                skipped.append(key)
                continue
            if s == SECRET_CLEAR:
                row = db.query(SettingOverride).filter(SettingOverride.key == key).first()
                if row:
                    db.delete(row)
                    updated.append(key)
                else:
                    skipped.append(key)
                continue
            # 忽略前端误把脱敏串写回
            if s.startswith("••••"):
                skipped.append(key)
                continue
            value = s
        else:
            value = _coerce(defn, raw)

        row = db.query(SettingOverride).filter(SettingOverride.key == key).first()
        serialized = _serialize(value)
        if row is None:
            db.add(SettingOverride(key=key, value=serialized))
        else:
            row.value = serialized
            row.updated_at = datetime.now()
        updated.append(key)
        if defn.requires_scheduler_reload:
            reload = True
    db.commit()
    invalidate_cache()
    _on_secrets_changed(updated)
    if any(k.startswith("datasources.") for k in updated):
        _clear_data_caches()
    if any(k.startswith("secrets.tushare") for k in updated):
        try:
            from .data import tushare_client
            tushare_client.reset_client()
        except Exception:  # noqa: BLE001
            pass
    return {"updated": updated, "skipped": skipped, "reload_scheduler": reload}


def _clear_data_caches() -> None:
    """数据源设置变更后清 TTL，避免旧开关结果残留。"""
    try:
        from .data import market, news_rss
        for fn in (
            market.get_quote, market.get_daily_kline, market.get_news,
            market.get_index_daily, market.get_hs300_history, market._trade_dates,
            market.get_market_snapshot, market.get_screen_universe,
            news_rss.fetch_all_headlines,
        ):
            if hasattr(fn, "cache_clear"):
                fn.cache_clear()
    except Exception:  # noqa: BLE001
        logger.debug("clear data caches failed", exc_info=True)


def reset_settings(keys: list[str] | None = None, group: str | None = None,
                   db: Session | None = None) -> dict:
    """删除覆盖，恢复 .env / 注册表默认。"""
    from .database import SessionLocal
    from .models import SettingOverride

    own = db is None
    if own:
        db = SessionLocal()
    try:
        q = db.query(SettingOverride)
        if keys:
            for k in keys:
                get_def(k)
            q = q.filter(SettingOverride.key.in_(keys))
        elif group:
            group_keys = [d.key for d in list_defs(group)]
            q = q.filter(SettingOverride.key.in_(group_keys))
        rows = q.all()
        removed = [r.key for r in rows]
        reload = any(REGISTRY[k].requires_scheduler_reload for k in removed if k in REGISTRY)
        for r in rows:
            db.delete(r)
        db.commit()
        invalidate_cache()
        _on_secrets_changed(removed)
        if any(k.startswith("datasources.") for k in removed):
            _clear_data_caches()
        return {"removed": removed, "reload_scheduler": reload}
    finally:
        if own:
            db.close()


def export_snapshot() -> str:
    """调试用：当前有效配置 JSON（secret 脱敏）。"""
    data = {}
    for k, v in _effective_map().items():
        defn = REGISTRY[k]
        data[k] = mask_secret(str(v)) if _is_secret(defn) else v
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)
