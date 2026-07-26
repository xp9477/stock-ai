"""APScheduler 盘中定时决策。仅 A 股交易日运行。"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .agents import engine
from .config import settings
from .data import market

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _job():
    if not market.is_trade_date():
        logger.info("今日非交易日,跳过定时决策")
        return
    engine.run_pipeline(trigger="schedule")


def start():
    global _scheduler
    if not settings.schedule_enabled:
        logger.info("定时调度已关闭 (SCHEDULE_ENABLED=false)")
        return
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    seen: set[str] = set()
    for time_str in settings.schedule_times.split(","):
        time_str = time_str.strip()
        if not time_str or time_str in seen:
            continue
        seen.add(time_str)
        hour, minute = time_str.split(":")
        _scheduler.add_job(_job, CronTrigger(day_of_week="mon-fri",
                                             hour=int(hour), minute=int(minute)),
                           id=f"run_{time_str}", max_instances=1, coalesce=True)
    _scheduler.start()
    logger.info("调度器已启动: %s", settings.schedule_times)


def shutdown():
    if _scheduler:
        _scheduler.shutdown(wait=False)


def is_enabled() -> bool:
    return _scheduler is not None and _scheduler.running


def schedule_times() -> str:
    return settings.schedule_times


def next_run_time() -> str | None:
    if _scheduler is None:
        return None
    times = [job.next_run_time for job in _scheduler.get_jobs() if job.next_run_time]
    if not times:
        return None
    return min(times).strftime("%Y-%m-%d %H:%M")
