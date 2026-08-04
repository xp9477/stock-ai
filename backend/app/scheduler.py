"""APScheduler:每日决策 + 盘中监控 + 规则组周频调仓。"""
import logging
from datetime import datetime, time as dtime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .agents import engine, monitor, selector
from .config import settings
from .data import market

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# 盘中监控时段
MONITOR_WINDOWS = ((dtime(9, 45), dtime(11, 30)), (dtime(13, 15), dtime(14, 45)))


def _decision_job():
    if not market.is_trade_date():
        logger.info("今日非交易日,跳过每日决策")
        return
    engine.run_pipeline(trigger="schedule")


def _selector_job():
    if not market.is_trade_date():
        logger.info("今日非交易日,跳过自动选股")
        return
    selector.run_selector(trigger="schedule")


def _monitor_job():
    if not market.is_trade_date():
        return
    now = datetime.now().time()
    if not any(start <= now <= end for start, end in MONITOR_WINDOWS):
        return
    try:
        count = monitor.run_monitor()
        if count:
            logger.info("盘中监控触发 %d 次复审", count)
    except Exception:  # noqa: BLE001
        logger.exception("盘中监控失败")


def _rule_rebalance_job():
    """周一（交易日）规则组调仓。"""
    if not market.is_trade_date():
        logger.info("今日非交易日,跳过规则调仓")
        return
    from .database import SessionLocal
    from .strategies.rule_runner import rebalance_all_rules

    db = SessionLocal()
    try:
        result = rebalance_all_rules(db)
        logger.info("规则组调仓完成: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("规则组调仓失败")
    finally:
        db.close()


def _sched_params() -> dict:
    """从运行时配置读取调度参数（设置页可改）。"""
    try:
        from .runtime_settings import get_setting
        return {
            "decision_time": str(get_setting("schedule.daily_decision_time")),
            "select_enabled": bool(get_setting("schedule.stock_select_enabled")),
            "select_time": str(get_setting("schedule.stock_select_time")),
            "monitor_minutes": int(get_setting("schedule.monitor_interval_minutes")),
        }
    except Exception:  # noqa: BLE001
        return {
            "decision_time": settings.daily_decision_time,
            "select_enabled": settings.stock_select_enabled,
            "select_time": settings.stock_select_time,
            "monitor_minutes": settings.monitor_interval_minutes,
        }


def _register_jobs(sched: BackgroundScheduler) -> None:
    p = _sched_params()
    hour, minute = p["decision_time"].strip().split(":")
    sched.add_job(_decision_job,
                  CronTrigger(day_of_week="mon-fri", hour=int(hour), minute=int(minute)),
                  id="daily_decision", max_instances=1, coalesce=True, replace_existing=True)

    if p["select_enabled"]:
        sel_hour, sel_minute = p["select_time"].strip().split(":")
        sched.add_job(_selector_job,
                      CronTrigger(day_of_week="mon-fri",
                                  hour=int(sel_hour), minute=int(sel_minute)),
                      id="stock_select", max_instances=1, coalesce=True, replace_existing=True)
    else:
        try:
            sched.remove_job("stock_select")
        except Exception:  # noqa: BLE001
            pass

    sched.add_job(_monitor_job,
                  CronTrigger(day_of_week="mon-fri", hour="9-14",
                              minute=f"*/{p['monitor_minutes']}"),
                  id="monitor", max_instances=1, coalesce=True, replace_existing=True)

    # 规则组：每周一 14:50（决策后）
    sched.add_job(_rule_rebalance_job,
                  CronTrigger(day_of_week="mon", hour=14, minute=50),
                  id="rule_rebalance", max_instances=1, coalesce=True, replace_existing=True)


def start():
    global _scheduler
    if not settings.schedule_enabled:
        logger.info("定时调度已关闭 (SCHEDULE_ENABLED=false)")
        return
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _register_jobs(_scheduler)
    _scheduler.start()
    p = _sched_params()
    logger.info(
        "调度器已启动: 每日决策 %s, 自动选股 %s, 盘中监控每 %d 分钟, 规则调仓 周一 14:50",
        p["decision_time"],
        p["select_time"] if p["select_enabled"] else "关闭",
        p["monitor_minutes"],
    )


def reload_jobs() -> None:
    """设置页改调度参数后热重载 job（不重启进程）。"""
    if _scheduler is None or not _scheduler.running:
        logger.info("调度器未运行,跳过 reload_jobs")
        return
    _register_jobs(_scheduler)
    p = _sched_params()
    logger.info(
        "调度器已重载: 每日决策 %s, 自动选股 %s, 监控每 %d 分钟",
        p["decision_time"],
        p["select_time"] if p["select_enabled"] else "关闭",
        p["monitor_minutes"],
    )


def shutdown():
    if _scheduler:
        _scheduler.shutdown(wait=False)


def is_enabled() -> bool:
    return _scheduler is not None and _scheduler.running


def schedule_times() -> str:
    p = _sched_params()
    return f"每日 {p['decision_time']} 决策 / 每 {p['monitor_minutes']} 分钟监控"


def next_run_time() -> str | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job("daily_decision")
    if job is None or job.next_run_time is None:
        return None
    return job.next_run_time.strftime("%Y-%m-%d %H:%M")
