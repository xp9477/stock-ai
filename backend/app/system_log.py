"""全局系统日志：logging Handler → SQLite + 内存环形缓冲；按保留天数清理。"""
from __future__ import annotations

import logging
import re
import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+"
)
_ring: deque[dict[str, Any]] = deque(maxlen=2000)
_ring_lock = threading.Lock()
_handler_installed = False
_write_count = 0


def _redact(msg: str) -> str:
    return _SECRET_RE.sub(r"\1=***", msg or "")


def _min_level_num() -> int:
    try:
        from .runtime_settings import get_setting
        name = str(get_setting("logs.min_level") or "INFO").upper()
    except Exception:  # noqa: BLE001
        name = "INFO"
    return _LEVELS.get(name, 20)


def _retention_days() -> int:
    try:
        from .runtime_settings import get_setting
        return int(get_setting("logs.retention_days") or 30)
    except Exception:  # noqa: BLE001
        return 30


def _extract_run_id(record: logging.LogRecord) -> int | None:
    rid = getattr(record, "run_id", None)
    if rid is not None:
        try:
            return int(rid)
        except (TypeError, ValueError):
            return None
    # 从消息里粗匹配 run_id=123
    m = re.search(r"run_id[=:\s]+(\d+)", record.getMessage() or "")
    return int(m.group(1)) if m else None


class DbLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _write_count
        try:
            if record.levelno < _min_level_num():
                return
            # 避免日志系统自身递归
            if record.name.startswith("app.system_log"):
                return
            msg = _redact(self.format(record) if self.formatter else record.getMessage())
            if len(msg) > 4000:
                msg = msg[:4000] + "…"
            entry = {
                "level": record.levelname,
                "logger_name": (record.name or "")[:120],
                "message": msg,
                "run_id": _extract_run_id(record),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with _ring_lock:
                _ring.appendleft(entry)
            _persist(entry)
            _write_count += 1
            if _write_count % 50 == 0:
                purge_old()
        except Exception:  # noqa: BLE001
            self.handleError(record)


def _persist(entry: dict[str, Any]) -> None:
    try:
        from .database import SessionLocal
        from .models import SystemLog

        db = SessionLocal()
        try:
            db.add(SystemLog(
                level=entry["level"],
                logger_name=entry["logger_name"],
                message=entry["message"],
                run_id=entry.get("run_id"),
            ))
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass


def purge_old(days: int | None = None) -> int:
    days = days if days is not None else _retention_days()
    cutoff = datetime.now() - timedelta(days=max(1, days))
    try:
        from .database import SessionLocal
        from .models import SystemLog

        db = SessionLocal()
        try:
            q = db.query(SystemLog).filter(SystemLog.created_at < cutoff)
            n = q.count()
            q.delete(synchronize_session=False)
            db.commit()
            return n
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return 0


def list_logs(
    limit: int = 200,
    level: str | None = None,
    run_id: int | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    """优先 DB；失败时回退内存环。"""
    limit = max(1, min(int(limit or 200), 1000))
    try:
        from .database import SessionLocal
        from .models import SystemLog

        db = SessionLocal()
        try:
            query = db.query(SystemLog).order_by(SystemLog.id.desc())
            if level:
                query = query.filter(SystemLog.level == level.upper())
            if run_id is not None:
                query = query.filter(SystemLog.run_id == run_id)
            if q:
                query = query.filter(SystemLog.message.contains(q))
            rows = query.limit(limit).all()
            return [{
                "id": r.id,
                "level": r.level,
                "logger_name": r.logger_name,
                "message": r.message,
                "run_id": r.run_id,
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
            } for r in rows]
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        with _ring_lock:
            items = list(_ring)
        if level:
            items = [i for i in items if i.get("level") == level.upper()]
        if run_id is not None:
            items = [i for i in items if i.get("run_id") == run_id]
        if q:
            items = [i for i in items if q in (i.get("message") or "")]
        return items[:limit]


def install_handler() -> None:
    global _handler_installed
    if _handler_installed:
        return
    root = logging.getLogger()
    h = DbLogHandler()
    h.setLevel(logging.DEBUG)
    h.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(h)
    _handler_installed = True
    logger.info("系统日志 Handler 已安装（保留 %s 天）", _retention_days())
