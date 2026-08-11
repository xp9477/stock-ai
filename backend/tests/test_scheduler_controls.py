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
        self.removed = []

    def add_job(self, _func, _trigger, **kwargs):
        self.job_ids.append(kwargs["id"])

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
        "decision_time": "16:00",
        "select_enabled": True,
        "select_time": "15:30",
        "monitor_minutes": 5,
    }
    with patch.object(scheduler, "_sched_params", return_value=params):
        scheduler._register_jobs(fake)

    assert set(fake.job_ids) == {"daily_decision", "stock_select", "monitor"}
    assert "rule_rebalance" not in fake.job_ids
    assert fake.removed == ["rule_rebalance"]
