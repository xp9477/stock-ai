"""扶摇客户端与统一行情适配测试（无网络）。"""
import pandas as pd

from app.data import market
from app.data.fuyao_client import from_thscode, to_thscode


def test_to_thscode_sh():
    assert to_thscode("600519") == "600519.SH"
    assert to_thscode("510300") == "510300.SH"


def test_to_thscode_sz():
    assert to_thscode("000001") == "000001.SZ"
    assert to_thscode("300750") == "300750.SZ"


def test_to_thscode_passthrough():
    assert to_thscode("600519.SH") == "600519.SH"


def test_from_thscode():
    assert from_thscode("600519.SH") == "600519"


def test_market_quote_uses_fuyao_snapshot(monkeypatch):
    from app.data import fuyao_client

    monkeypatch.setattr(fuyao_client, "available", lambda: True)
    monkeypatch.setattr(fuyao_client, "prices_snapshot", lambda _codes: [{
        "ticker": "600519", "last_price": 1355.29, "prev_price": 1343.0,
        "open_price": 1338.0, "volume": 100, "turnover": 1000,
        "price_change_ratio_pct": 0.91,
    }])
    market.get_quote.cache_clear()

    quote = market.get_quote("600519")

    assert quote["source"] == "fuyao"
    assert quote["price"] == 1355.29
    assert quote["prev_close"] == 1343.0
    assert quote["tradable"] is True


def test_market_daily_kline_uses_fuyao(monkeypatch):
    from app.data import fuyao_client

    monkeypatch.setattr(fuyao_client, "available", lambda: True)
    monkeypatch.setattr(fuyao_client, "daily_bars", lambda *_args: pd.DataFrame([
        {"date": "2026-08-12", "open": 10, "high": 11, "low": 9,
         "close": 10, "volume": 100, "amount": 1000},
        {"date": "2026-08-13", "open": 10, "high": 12, "low": 10,
         "close": 11, "volume": 120, "amount": 1200},
    ]))
    market.get_daily_kline.cache_clear()

    bars = market.get_daily_kline("600519")

    assert list(bars["收盘"]) == [10, 11]
    assert bars.iloc[-1]["涨跌幅"] == 10.0

import time
from datetime import date, timedelta
import pytest
import requests


def test_fuyao_get_retries_429_with_retry_after(monkeypatch):
    from app.data import fuyao_client

    sleep_calls = []
    monkeypatch.setattr(fuyao_client.time, "sleep", lambda s: sleep_calls.append(s))
    monkeypatch.setattr(fuyao_client, "available", lambda: True)

    req_count = 0

    def fake_get(url, headers, params, timeout):
        nonlocal req_count
        req_count += 1
        resp = requests.Response()
        if req_count == 1:
            resp.status_code = 429
            resp.headers["Retry-After"] = "2"
        else:
            resp.status_code = 200
            resp._content = b'{"code": 0, "message": "ok", "data": {"test": 1}}'
        return resp

    monkeypatch.setattr(fuyao_client.requests, "get", fake_get)
    data = fuyao_client._get("/test")
    assert data == {"test": 1}
    assert req_count == 2
    assert 2.0 in sleep_calls


def test_fuyao_get_retries_transient_5xx(monkeypatch):
    from app.data import fuyao_client

    sleep_calls = []
    monkeypatch.setattr(fuyao_client.time, "sleep", lambda s: sleep_calls.append(s))
    monkeypatch.setattr(fuyao_client, "available", lambda: True)

    req_count = 0

    def fake_get(url, headers, params, timeout):
        nonlocal req_count
        req_count += 1
        resp = requests.Response()
        if req_count == 1:
            resp.status_code = 502
        else:
            resp.status_code = 200
            resp._content = b'{"code": 0, "message": "ok", "data": {"success": true}}'
        return resp

    monkeypatch.setattr(fuyao_client.requests, "get", fake_get)
    data = fuyao_client._get("/test")
    assert data == {"success": True}
    assert req_count == 2


def test_fuyao_get_bounded_retries_fails_closed(monkeypatch):
    from app.data import fuyao_client

    monkeypatch.setattr(fuyao_client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(fuyao_client, "available", lambda: True)

    req_count = 0

    def fake_get(url, headers, params, timeout):
        nonlocal req_count
        req_count += 1
        resp = requests.Response()
        resp.status_code = 429
        return resp

    monkeypatch.setattr(fuyao_client.requests, "get", fake_get)
    with pytest.raises(requests.exceptions.HTTPError):
        fuyao_client._get("/test")
    assert req_count == 4


def test_fuyao_get_non_retryable_client_error(monkeypatch):
    from app.data import fuyao_client

    monkeypatch.setattr(fuyao_client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(fuyao_client, "available", lambda: True)

    req_count = 0

    def fake_get(url, headers, params, timeout):
        nonlocal req_count
        req_count += 1
        resp = requests.Response()
        resp.status_code = 403
        return resp

    monkeypatch.setattr(fuyao_client.requests, "get", fake_get)
    with pytest.raises(requests.exceptions.HTTPError):
        fuyao_client._get("/test")
    assert req_count == 1


def test_fuyao_throttle_enforces_minimum_interval(monkeypatch):
    from app.data import fuyao_client

    sleeps = []
    monkeypatch.setattr(fuyao_client.time, "sleep", lambda s: sleeps.append(s))

    with fuyao_client._RATE_LOCK:
        fuyao_client._LAST_REQUEST_TIME = time.monotonic()

    fuyao_client._throttle()
    assert len(sleeps) >= 1
    assert sleeps[0] > 0


def test_prefetch_quotes_and_get_quote_share_cache(monkeypatch):
    from app.data import fuyao_client

    monkeypatch.setattr(fuyao_client, "available", lambda: True)
    snapshot_calls = []

    def fake_prices_snapshot(codes):
        snapshot_calls.append(list(codes))
        return [
            {
                "ticker": c, "last_price": 10.0 + idx, "prev_price": 9.9,
                "open_price": 9.95, "volume": 1000, "turnover": 10000,
                "price_change_ratio_pct": 1.0,
            }
            for idx, c in enumerate(codes)
        ]

    monkeypatch.setattr(fuyao_client, "prices_snapshot", fake_prices_snapshot)
    market.get_quote.cache_clear()

    res = market.prefetch_quotes(["600519", "000001", "300750"])
    assert len(snapshot_calls) == 1
    assert set(snapshot_calls[0]) == {"600519", "000001", "300750"}
    assert len(res) == 3

    q1 = market.get_quote("600519")
    q2 = market.get_quote("000001")
    q3 = market.get_quote("300750")
    assert q1 is not None and q1["price"] == 10.0
    assert q2 is not None and q2["price"] == 11.0
    assert q3 is not None and q3["price"] == 12.0
    assert len(snapshot_calls) == 1

    market.get_quote.cache_clear()
    q1_fresh = market.get_quote("600519")
    assert q1_fresh is not None
    assert len(snapshot_calls) == 2


def test_roe_history_with_limit_and_default(monkeypatch):
    from app.data import fuyao_client

    fuyao_client.roe_history.cache_clear()
    indicator_calls = []

    def fake_indicators(code, rep):
        indicator_calls.append((code, rep))
        return {"report": rep, "roe": 12.5, "roa": 5.0}

    monkeypatch.setattr(fuyao_client, "financial_indicators", fake_indicators)

    df_limited = fuyao_client.roe_history("600519", years=4, limit=2)
    assert len(df_limited) == 2
    assert len(indicator_calls) == 2
    assert list(df_limited.columns) == ["ann_date", "end_date", "report", "roe", "roa"]

    fuyao_client.roe_history.cache_clear()
    indicator_calls.clear()
    df_full = fuyao_client.roe_history("600519", years=1, limit=None)
    assert len(df_full) >= 3
    assert len(indicator_calls) == len(df_full)


def test_latest_factor_snapshot_uses_400_day_window_matching_factsheet(monkeypatch):
    from app.data import fuyao_client
    from app.factors import panel

    bars_calls = []

    def fake_daily_bars(code, start, end, adjust="forward"):
        bars_calls.append((code, start, end))
        return pd.DataFrame([
            {"date": "2026-08-01", "open": 10.0, "high": 10.5, "low": 9.5,
             "close": 10.2, "volume": 100, "amount": 1020}
        ])

    monkeypatch.setattr(fuyao_client, "available", lambda: True)
    monkeypatch.setattr(fuyao_client, "daily_bars", fake_daily_bars)
    monkeypatch.setattr(fuyao_client, "valuation_snapshot", lambda codes: pd.DataFrame([
        {"code": c, "pe_ttm": 15.0, "pb": 2.0} for c in codes
    ]))
    monkeypatch.setattr(fuyao_client, "roe_history", lambda code, **kwargs: pd.DataFrame([
        {"ann_date": "20260420", "end_date": "20260331", "report": "2026-1", "roe": 10.0, "roa": 4.0},
        {"ann_date": "20260820", "end_date": "20260630", "report": "2026-2", "roe": 12.0, "roa": 5.0},
    ]))

    asof = date(2026, 9, 4)
    panel.latest_factor_snapshot(["600519"], asof=asof)
    assert len(bars_calls) == 1
    code, start, end = bars_calls[0]
    assert code == "600519"
    assert start == asof - timedelta(days=400)
    assert end == asof
