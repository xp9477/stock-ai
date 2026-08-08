"""P1：数据源真接线、超时包装、账户/撮合设置、日志。"""
from concurrent.futures import TimeoutError as FuturesTimeout
from unittest.mock import patch

from app.data.http_timeout import call_with_timeout
from app.data.news_rss import RSS_FEEDS
from app.runtime_settings import get_setting, invalidate_cache, list_settings, set_settings
from app.settings_registry import GROUPS, REGISTRY
from app.trading import broker


def test_rss_has_cn_and_global():
    regions = {f.get("region") for f in RSS_FEEDS}
    assert "cn" in regions and "global" in regions
    assert any(f["id"] == "chinanews_finance" for f in RSS_FEEDS)
    assert any(f["id"] == "bbc_biz" for f in RSS_FEEDS)


def test_timeout_wrapper_raises():
    import time

    def slow():
        time.sleep(2)
        return 1

    try:
        call_with_timeout(slow, 0.2)
        assert False, "should timeout"
    except TimeoutError:
        pass


def test_account_and_trading_in_registry():
    ids = {g["id"] for g in GROUPS}
    assert "account" in ids and "trading" in ids and "logs" in ids
    assert "account.initial_cash" in REGISTRY
    assert REGISTRY["account.initial_cash"].danger == "frozen"
    assert REGISTRY["trading.commission_rate"].danger == "confirm"
    assert "logs.retention_days" in REGISTRY
    assert REGISTRY["logs.retention_days"].default == 30


def test_list_settings_exposes_danger(db):
    items = list_settings(group="trading", db=db)
    assert items
    assert all("danger" in i for i in items)
    row = next(i for i in items if i["key"] == "trading.stamp_tax_rate")
    assert row["danger"] == "confirm"


def test_broker_fees_use_runtime_settings(monkeypatch):
    vals = {
        "trading.commission_rate": 0.001,
        "trading.commission_min": 1.0,
        "trading.transfer_fee_rate": 0.0,
        "trading.stamp_tax_rate": 0.001,
    }
    monkeypatch.setattr("app.trading.broker.get_setting", lambda k, db=None: vals[k])
    # 10000 * 0.001 = 10 > min 1 → commission 10
    buy = broker.calc_buy_fee(10_000)
    assert abs(buy - 10.0) < 1e-6
    sell = broker.calc_sell_fee(10_000)
    # 10 commission + 10 stamp
    assert abs(sell - 20.0) < 1e-6


def test_tushare_available_respects_enabled(db, monkeypatch):
    from app.data import tushare_client

    invalidate_cache()
    set_settings({"datasources.tushare.enabled": False, "secrets.tushare_token": "tok-xxx"}, db)
    # get_setting without db uses file db; patch is_enabled instead
    monkeypatch.setattr("app.data.datasources.is_enabled", lambda sid: False if sid == "tushare" else True)
    assert tushare_client.available() is False


def test_system_log_list_and_redact():
    from app.system_log import list_logs, _redact
    assert "***" in _redact("api_key=sk-secret-value-here")
    # list_logs should not throw even if empty
    items = list_logs(limit=5)
    assert isinstance(items, list)
