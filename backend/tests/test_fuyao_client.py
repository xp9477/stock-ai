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
