from unittest.mock import patch

from app import scheduler


class FakeScheduler:
    def __init__(self):
        self.running = True
        self.shutdown_called = False

    def shutdown(self, wait=False):
        self.shutdown_called = True
        self.running = False


class RecordingScheduler:
    def __init__(self):
        self.job_ids = []
        self.jobs = []
        self.removed = []

    def add_job(self, func, trigger, **kwargs):
        self.job_ids.append(kwargs["id"])
        self.jobs.append((func, trigger, kwargs))

    def remove_job(self, job_id):
        self.removed.append(job_id)


def test_reload_jobs_stops_scheduler_when_master_switch_is_off():
    fake = FakeScheduler()
    scheduler._scheduler = fake
    try:
        with patch("app.runtime_settings.get_setting", return_value=False):
            scheduler.reload_jobs()
        assert fake.shutdown_called is True
        assert scheduler._scheduler is None
    finally:
        scheduler._scheduler = None


def test_register_jobs_never_schedules_capitalized_rule_rebalance():
    fake = RecordingScheduler()
    params = {
        "morning_decision_time": "09:35",
        "decision_time": "14:10",
        "select_enabled": True,
        "select_time": "08:50",
        "monitor_minutes": 5,
    }
    with patch.object(scheduler, "_sched_params", return_value=params):
        scheduler._register_jobs(fake)

    assert set(fake.job_ids) == {
        "decision_morning", "decision_afternoon", "stock_select", "monitor",
    }
    sessions = {
        kwargs["id"]: kwargs.get("args")
        for _func, _trigger, kwargs in fake.jobs
        if kwargs["id"].startswith("decision_")
    }
    assert sessions == {
        "decision_morning": ["morning"],
        "decision_afternoon": ["afternoon"],
    }
    assert "rule_rebalance" not in fake.job_ids
    assert fake.removed == ["daily_decision", "rule_rebalance"]


def test_decision_job_records_which_session_triggered_it():
    with patch.object(scheduler.market, "is_trade_date", return_value=True), \
            patch.object(scheduler.engine, "run_pipeline") as run_pipeline:
        scheduler._decision_job("morning")
        scheduler._decision_job("afternoon")

    assert [call.kwargs["trigger"] for call in run_pipeline.call_args_list] == [
        "schedule_morning", "schedule_afternoon",
    ]


def test_schedule_defaults_cover_both_trading_sessions():
    assert scheduler.MONITOR_WINDOWS == (
        (scheduler.dtime(9, 30), scheduler.dtime(11, 30)),
        (scheduler.dtime(13, 0), scheduler.dtime(14, 50)),
    )
