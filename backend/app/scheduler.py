"""APScheduler:每交易日一次全量决策 + 盘中定时持仓监控。"""
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


def start():
    global _scheduler
    if not settings.schedule_enabled:
        logger.info("定时调度已关闭 (SCHEDULE_ENABLED=false)")
        return
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    hour, minute = settings.daily_decision_time.strip().split(":")
    _scheduler.add_job(_decision_job,
                       CronTrigger(day_of_week="mon-fri", hour=int(hour), minute=int(minute)),
                       id="daily_decision", max_instances=1, coalesce=True)

    if settings.stock_select_enabled:
        sel_hour, sel_minute = settings.stock_select_time.strip().split(":")
        _scheduler.add_job(_selector_job,
                           CronTrigger(day_of_week="mon-fri",
                                       hour=int(sel_hour), minute=int(sel_minute)),
                           id="stock_select", max_instances=1, coalesce=True)

    _scheduler.add_job(_monitor_job,
                       CronTrigger(day_of_week="mon-fri", hour="9-14",
                                   minute=f"*/{settings.monitor_interval_minutes}"),
                       id="monitor", max_instances=1, coalesce=True)

    _scheduler.start()
    logger.info("调度器已启动: 每日决策 %s, 自动选股 %s, 盘中监控每 %d 分钟",
                settings.daily_decision_time,
                settings.stock_select_time if settings.stock_select_enabled else "关闭",
                settings.monitor_interval_minutes)


def shutdown():
    if _scheduler:
        _scheduler.shutdown(wait=False)


def is_enabled() -> bool:
    return _scheduler is not None and _scheduler.running


def schedule_times() -> str:
    return f"每日 {settings.daily_decision_time} 决策 / 每 {settings.monitor_interval_minutes} 分钟监控"


def next_run_time() -> str | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job("daily_decision")
    if job is None or job.next_run_time is None:
        return None
    return job.next_run_time.strftime("%Y-%m-%d %H:%M")
