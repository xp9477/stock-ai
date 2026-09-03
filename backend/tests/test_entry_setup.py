"""Deterministic entry shortlist contract."""
from unittest.mock import patch

import pandas as pd

from app.data.factsheet import build_factsheet
from app.data.indicators import latest_indicator_snapshot
from app.strategies.entry_setup import assess_entry_setup


def _sheet(**updates):
    sheet = {
        "factors": {
            "rank": 2,
            "universe_size": 30,
            "score": 0.8,
            "mom_short": 0.06,
            "mom_mid": 0.12,
        },
        "technical": {
            "close": 12.0,
            "ma20": 11.5,
            "macd": 0.08,
            "rsi14": 58.0,
        },
        "valuation": {"pe": 24.0},
    }
    sheet.update(updates)
    return sheet


def test_entry_setup_actionable_requires_rank_and_three_confirmations():
    result = assess_entry_setup(
        _sheet(), top_pct=0.2, watch_pct=0.35,
        min_confirmations=3, max_rsi=75,
    )

    assert result["version"] == "entry_setup_v1"
    assert result["classification"] == "provisional"
    assert result["status"] == "actionable"
    assert result["actionable"] is True
    assert result["positive_confirmations"] == 4
    assert result["factor_rank_pct"] == 0.066667


def test_entry_setup_watch_does_not_authorize_new_entry():
    sheet = _sheet()
    sheet["factors"]["rank"] = 8
    sheet["factors"]["mom_mid"] = -0.02
    sheet["technical"]["macd"] = -0.01

    result = assess_entry_setup(
        sheet, top_pct=0.2, watch_pct=0.35,
        min_confirmations=3, max_rsi=75,
    )

    assert result["status"] == "watch"
    assert result["actionable"] is False
    assert result["positive_confirmations"] == 2


def test_entry_setup_fails_closed_on_missing_comparable_data():
    result = assess_entry_setup(
        {"factors": {}, "technical": {}},
        top_pct=0.2, watch_pct=0.35,
        min_confirmations=3, max_rsi=75,
    )

    assert result["status"] == "data_insufficient"
    assert result["actionable"] is False
    assert any("缺少" in item for item in result["hard_blockers"])


def test_entry_setup_blocks_overheated_candidate():
    sheet = _sheet()
    sheet["technical"]["rsi14"] = 82
    result = assess_entry_setup(
        sheet, top_pct=0.2, watch_pct=0.35,
        min_confirmations=3, max_rsi=75,
    )

    assert result["actionable"] is False
    assert any("过热线" in item for item in result["hard_blockers"])


def test_latest_indicator_snapshot_is_structured_and_json_safe():
    close = [10 + i * 0.1 for i in range(30)]
    frame = pd.DataFrame({
        "日期": pd.date_range("2026-01-01", periods=30).strftime("%Y-%m-%d"),
        "开盘": close,
        "收盘": close,
        "最高": [item + 0.2 for item in close],
        "最低": [item - 0.2 for item in close],
        "成交量": [1000 + i * 10 for i in range(30)],
        "涨跌幅": [0.0] * 30,
    })

    result = latest_indicator_snapshot(frame)

    assert result["close"] == close[-1]
    assert result["ma20"] is not None
    assert result["return_20d"] is not None
    assert all(value is None or isinstance(value, float) for value in result.values())


def test_factsheet_reuses_shared_factor_row_without_rebuilding_cross_section():
    bars = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30).strftime("%Y-%m-%d"),
        "open": [10.0] * 30,
        "close": [10.0 + i * 0.1 for i in range(30)],
        "high": [11.0 + i * 0.1 for i in range(30)],
        "low": [9.0 + i * 0.1 for i in range(30)],
        "volume": [1000.0] * 30,
        "amount": [10000.0] * 30,
    })
    factor_data = {
        "rank": 2, "universe_size": 30, "score": 0.8,
        "mom_short": 0.06, "mom_mid": 0.12, "low_vol": -0.01,
        "ep": 0.05, "bp": 0.4, "quality_roe": 12.0,
        "rev_1m": -0.06, "low_turn": -1.0, "growth_roe": 1.0,
    }
    quote = {
        "price": 12.9, "pct_change": 1.0, "turnover": 1e8,
        "volume": 1e6, "quote_asof": "2026-08-13T07:00:00+00:00",
        "source": "fuyao", "name": "测试股",
    }

    with patch("app.data.factsheet.fuyao.available", return_value=True), \
         patch("app.data.factsheet.market.get_quote", return_value=quote), \
         patch("app.data.factsheet.fuyao.daily_bars", return_value=bars), \
         patch("app.data.factsheet.news_rss.news_for_stock", return_value=[]), \
         patch("app.data.factsheet.latest_factor_snapshot") as snapshot, \
         patch("app.data.factsheet.fuyao.valuation_snapshot") as valuation:
        sheet = build_factsheet(
            "600000", "测试股", peer_codes=["600001"],
            factor_data=factor_data,
        )

    assert sheet["factors"]["rank"] == 2
    assert sheet["valuation"]["pe"] == 20.0
    assert sheet["technical"]["ma20"] is not None
    snapshot.assert_not_called()
    valuation.assert_not_called()
