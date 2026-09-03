"""P0：数据源注册表 + 失败策略校验 + 配置态列表。"""
from unittest.mock import patch

from app.runtime_settings import get_setting, invalidate_cache, list_settings, set_settings
from app.settings_registry import GROUPS, REGISTRY
from app.data.datasources import list_sources_status, fail_policy


def test_datasources_group_registered():
    ids = {g["id"] for g in GROUPS}
    assert "datasources" in ids
    assert "debug" in ids
    assert "datasources.sina.enabled" in REGISTRY
    assert REGISTRY["datasources.sina.enabled"].default is True
    assert "datasources.fuyao.fail_policy" in REGISTRY


def test_sina_defaults_and_override(db):
    invalidate_cache()
    with patch("app.runtime_settings._load_override_map", return_value={}):
        invalidate_cache()
        assert get_setting("datasources.sina.enabled") is True
        assert get_setting("datasources.sina.timeout_sec") == 25

    set_settings({"datasources.sina.enabled": False}, db)
    assert get_setting("datasources.sina.enabled", db) is False


def test_fail_policy_validation(db):
    invalidate_cache()
    try:
        set_settings({"datasources.fuyao.fail_policy": "explode"}, db)
        assert False, "should raise"
    except ValueError as err:
        assert "fallback" in str(err) or "fail_policy" in str(err)


def test_list_settings_includes_sina(db):
    items = list_settings(group="datasources", db=db)
    keys = {i["key"] for i in items}
    assert "datasources.sina.enabled" in keys
    assert "datasources.sina.timeout_sec" in keys
    labels = {i["key"]: i["label"] for i in items}
    assert "新浪" in labels["datasources.sina.enabled"]


def test_list_sources_status_shape(db):
    invalidate_cache()
    sources = list_sources_status()
    ids = {s["id"] for s in sources}
    assert ids == {"fuyao", "sina", "tushare", "rss"}
    sina = next(s for s in sources if s["id"] == "sina")
    assert sina["needs_key"] is False
    assert sina["enabled"] is True
    assert fail_policy("fuyao") in ("fallback", "hard", "skip")
