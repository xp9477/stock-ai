import pandas as pd

from app.data.indicators import compute_indicators, indicators_text


def make_kline(n=80):
    rows = []
    price = 100.0
    for day_index in range(n):
        price *= 1.003
        rows.append({
            "日期": f"2026-01-{(day_index % 28) + 1:02d}",
            "开盘": round(price * 0.99, 2), "收盘": round(price, 2),
            "最高": round(price * 1.01, 2), "最低": round(price * 0.98, 2),
            "成交量": 10000 + day_index * 10, "涨跌幅": 0.3,
        })
    return pd.DataFrame(rows)


def test_compute_indicators_columns():
    df = compute_indicators(make_kline())
    for col in ("MA5", "MA10", "MA20", "DIF", "DEA", "MACD", "RSI14", "VOL_RATIO"):
        assert col in df.columns
    last = df.iloc[-1]
    assert last["MA5"] > last["MA20"]  # 持续上涨,短期均线在上
    assert last["DIF"] > 0
    assert last["RSI14"] > 50


def test_indicators_text_contains_summary():
    text = indicators_text(make_kline(), days=30)
    assert "最新收盘价" in text
    assert "MA5" in text
    assert "近30日K线" in text
