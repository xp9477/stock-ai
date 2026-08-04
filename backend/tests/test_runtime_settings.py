"""运行时配置：注册表、覆盖、校验、恢复默认。"""
from app.runtime_settings import (get_setting, invalidate_cache, list_settings,
                                  reset_settings, set_settings)
from app.settings_registry import GROUPS, REGISTRY
from app.models import SettingOverride


def test_registry_has_core_groups():
    ids = {g["id"] for g in GROUPS}
    assert {"secrets", "selector", "prompt", "risk", "schedule"}.issubset(ids)
    assert "selector.pool_max" in REGISTRY
    assert "prompt.selector" in REGISTRY
    assert "risk.deep_loss_pct" in REGISTRY
    assert "secrets.fuyao_api_key" in REGISTRY
    assert REGISTRY["secrets.llm_api_key"].secret is True


def test_default_get_setting(db):
    """无覆盖时等于注册表默认（用测试库，避免污染真实 DB）。"""
    from unittest.mock import patch
    invalidate_cache()
    with patch("app.runtime_settings._load_override_map", return_value={}):
        invalidate_cache()
        assert get_setting("selector.pool_max") == 30
        assert get_setting("selector.screen.min_turnover_yi") == 2.0
        assert "选股策略师" in get_setting("prompt.selector")


def test_set_and_reset_override(db):
    invalidate_cache()
    result = set_settings({"selector.pool_max": 12}, db)
    assert "selector.pool_max" in result["updated"]
    assert get_setting("selector.pool_max", db) == 12
    assert db.query(SettingOverride).filter_by(key="selector.pool_max").first()

    reset = reset_settings(keys=["selector.pool_max"], db=db)
    assert "selector.pool_max" in reset["removed"]
    assert get_setting("selector.pool_max", db) == 30


def test_validation_rejects_bad_time(db):
    invalidate_cache()
    try:
        set_settings({"schedule.daily_decision_time": "25:99"}, db)
        assert False, "should raise"
    except ValueError as err:
        assert "HH:MM" in str(err) or "时间" in str(err)


def test_validation_rejects_out_of_range(db):
    invalidate_cache()
    try:
        set_settings({"selector.pool_max": 0}, db)
        assert False, "should raise"
    except ValueError:
        pass


def test_list_settings_shape(db):
    items = list_settings(group="risk", db=db)
    assert items
    keys = {i["key"] for i in items}
    assert "risk.deep_loss_pct" in keys
    sample = items[0]
    assert "value" in sample and "default" in sample and "label" in sample


def test_scheduler_flag_on_schedule_change(db):
    result = set_settings({"schedule.monitor_interval_minutes": 20}, db)
    assert result["reload_scheduler"] is True
    reset_settings(keys=["schedule.monitor_interval_minutes"], db=db)


def test_secret_masked_in_list(db):
    from app.runtime_settings import list_settings, set_settings, reset_settings, get_setting
    invalidate_cache()
    set_settings({"secrets.fuyao_api_key": "sk-test-secret-key-9999"}, db)
    items = {i["key"]: i for i in list_settings(group="secrets", db=db)}
    row = items["secrets.fuyao_api_key"]
    assert row["value"] == ""
    assert row["configured"] is True
    assert row["masked"].endswith("9999")
    assert "sk-test" not in row["masked"]
    assert get_setting("secrets.fuyao_api_key", db) == "sk-test-secret-key-9999"
    # 空写跳过
    r2 = set_settings({"secrets.fuyao_api_key": ""}, db)
    assert "secrets.fuyao_api_key" in r2["skipped"]
    assert get_setting("secrets.fuyao_api_key", db) == "sk-test-secret-key-9999"
    reset_settings(keys=["secrets.fuyao_api_key"], db=db)
