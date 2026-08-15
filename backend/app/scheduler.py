"""APScheduler：每日决策、选股与盘中复审。"""
import logging
from datetime import datetime, time as dtime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .agents import engine, monitor, selector
from .config import settings
from .data import market

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# 连续竞价期间监控已有持仓；无阈值事件时不会调用 LLM。
MONITOR_WINDOWS = ((dtime(9, 30), dtime(11, 30)), (dtime(13, 0), dtime(14, 50)))


def _decision_job(session: str):
    if not market.is_trade_date():
        logger.info("今日非交易日,跳过%s决策", session)
        return
    engine.run_pipeline(trigger=f"schedule_{session}")


def _selector_job():
    if not market.is_trade_date():
        logger.info("今日非交易日,跳过自动选股")
        return
    selector.run_selector(trigger="schedule")


def _monitor_job():
    # 双重门禁：日历 + 连续竞价时段（run_monitor 内部仍会再检查）
    if not market.is_trade_date():
        return
    if not market.is_trading_session():
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


def _sched_params() -> dict:
    """从运行时配置读取调度参数（设置页可改）。"""
    try:
        from .runtime_settings import get_setting
        return {
            "morning_decision_time": str(get_setting("schedule.morning_decision_time")),
            "decision_time": str(get_setting("schedule.daily_decision_time")),
            "select_enabled": bool(get_setting("schedule.stock_select_enabled")),
            "select_time": str(get_setting("schedule.stock_select_time")),
            "monitor_minutes": int(get_setting("schedule.monitor_interval_minutes")),
        }
    except Exception:  # noqa: BLE001
        return {
            "morning_decision_time": settings.morning_decision_time,
            "decision_time": settings.daily_decision_time,
            "select_enabled": settings.stock_select_enabled,
            "select_time": settings.stock_select_time,
            "monitor_minutes": settings.monitor_interval_minutes,
        }


def _register_jobs(sched: BackgroundScheduler) -> None:
    p = _sched_params()
    morning_hour, morning_minute = p["morning_decision_time"].strip().split(":")
    sched.add_job(
        _decision_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=int(morning_hour),
            minute=int(morning_minute),
        ),
        args=["morning"],
        id="decision_morning",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    hour, minute = p["decision_time"].strip().split(":")
    sched.add_job(
        _decision_job,
        CronTrigger(day_of_week="mon-fri", hour=int(hour), minute=int(minute)),
        args=["afternoon"],
        id="decision_afternoon",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

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

    # misfire_grace_time 短：进程重启时不要补跑盘后「漏掉」的监控（曾导致脏价强平）
    sched.add_job(
        _monitor_job,
        CronTrigger(day_of_week="mon-fri", hour="9-14",
                    minute=f"*/{p['monitor_minutes']}"),
        id="monitor", max_instances=1, coalesce=True, replace_existing=True,
        misfire_grace_time=60,
    )

    # 清理旧版本遗留 job，且不再注册。
    for legacy_job_id in ("daily_decision", "rule_rebalance"):
        try:
            sched.remove_job(legacy_job_id)
        except Exception:  # noqa: BLE001
            pass


def start():
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return
    try:
        from .runtime_settings import get_setting
        enabled = bool(get_setting("schedule.enabled"))
    except Exception:  # noqa: BLE001
        enabled = settings.schedule_enabled
    if not enabled:
        logger.info("定时调度已关闭 (schedule.enabled / SCHEDULE_ENABLED=false)")
        return
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _register_jobs(_scheduler)
    _scheduler.start()
    p = _sched_params()
    logger.info(
        "调度器已启动: 决策 %s/%s, 自动选股 %s, 盘中监控每 %d 分钟",
        p["morning_decision_time"],
        p["decision_time"],
        p["select_time"] if p["select_enabled"] else "关闭",
        p["monitor_minutes"],
    )


def reload_jobs() -> None:
    """设置页改调度参数后热重载 job（不重启进程）。"""
    global _scheduler
    from .runtime_settings import get_setting

    if not bool(get_setting("schedule.enabled")):
        if _scheduler is not None and _scheduler.running:
            _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("调度器已关闭")
        return
    if _scheduler is None or not _scheduler.running:
        start()
        return
    _register_jobs(_scheduler)
    p = _sched_params()
    logger.info(
        "调度器已重载: 决策 %s/%s, 自动选股 %s, 监控每 %d 分钟",
        p["morning_decision_time"],
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
    parts = [f"决策 {p['morning_decision_time']}/{p['decision_time']}"]
    if p["select_enabled"]:
        parts.append(f"选股 {p['select_time']}")
    parts.append(f"监控每{p['monitor_minutes']}分")
    return " · ".join(parts)


def next_run_time() -> str | None:
    if _scheduler is None:
        return None
    run_times = [
        job.next_run_time
        for job_id in ("decision_morning", "decision_afternoon")
        if (job := _scheduler.get_job(job_id)) is not None
        and job.next_run_time is not None
    ]
    if not run_times:
        return None
    return min(run_times).strftime("%Y-%m-%d %H:%M")
