"""本地计算技术指标 (pandas)。"""
from typing import Any

import pandas as pd


def compute_indicators(kline: pd.DataFrame) -> pd.DataFrame:
    """输入 AKShare 日 K DataFrame(含 收盘/成交量 列),返回附加指标列的副本。"""
    df = kline.copy()
    close = df["收盘"].astype(float)

    df["MA5"] = close.rolling(5).mean()
    df["MA10"] = close.rolling(10).mean()
    df["MA20"] = close.rolling(20).mean()

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["DIF"] = ema12 - ema26
    df["DEA"] = df["DIF"].ewm(span=9, adjust=False).mean()
    df["MACD"] = (df["DIF"] - df["DEA"]) * 2

    # RSI (14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - 100 / (1 + rs)
    # 无下跌日时 loss=0, RSI 定义为 100
    df["RSI14"] = rsi.mask(loss.eq(0) & gain.gt(0), 100.0).astype(float)

    # 量比: 当日成交量 / 5 日均量
    vol = df["成交量"].astype(float)
    df["VOL_RATIO"] = vol / vol.rolling(5).mean()

    return df


def indicators_text(kline: pd.DataFrame, days: int = 60) -> str:
    """生成给 LLM 的技术面文本摘要:最近指标值 + 近 N 日 K 线表。"""
    df = compute_indicators(kline).tail(days)
    last = df.iloc[-1]

    def fmt(value, digits=2):
        return "N/A" if pd.isna(value) else f"{value:.{digits}f}"

    lines = [
        f"最新收盘价: {fmt(last['收盘'])}",
        f"MA5: {fmt(last['MA5'])}  MA10: {fmt(last['MA10'])}  MA20: {fmt(last['MA20'])}",
        f"MACD: DIF={fmt(last['DIF'], 3)} DEA={fmt(last['DEA'], 3)} MACD柱={fmt(last['MACD'], 3)}",
        f"RSI14: {fmt(last['RSI14'], 1)}",
        f"量比(对5日均量): {fmt(last['VOL_RATIO'])}",
        "",
        f"近{min(days, len(df))}日K线 (日期, 开盘, 收盘, 最高, 最低, 成交量, 涨跌幅%):",
    ]
    for _, row in df.iterrows():
        lines.append(
            f"{row['日期']}, {row['开盘']}, {row['收盘']}, {row['最高']}, {row['最低']}, "
            f"{row['成交量']}, {row['涨跌幅']}"
        )
    return "\n".join(lines)


def latest_indicator_snapshot(kline: pd.DataFrame) -> dict[str, Any]:
    """Return a compact, JSON-safe latest technical snapshot.

    The long text table remains useful for audit, but strategy gates must not
    parse prose.  This structure is therefore the machine-readable source used
    by the entry setup contract.
    """
    if kline is None or kline.empty:
        return {}
    df = compute_indicators(kline)
    if df.empty:
        return {}
    last = df.iloc[-1]

    def number(column: str) -> float | None:
        value = last.get(column)
        if value is None or pd.isna(value):
            return None
        return float(value)

    close = pd.to_numeric(df["收盘"], errors="coerce")

    def trailing_return(periods: int) -> float | None:
        clean = close.dropna()
        if len(clean) <= periods:
            return None
        base = float(clean.iloc[-periods - 1])
        return None if base <= 0 else float(clean.iloc[-1] / base - 1.0)

    return {
        "close": number("收盘"),
        "ma5": number("MA5"),
        "ma10": number("MA10"),
        "ma20": number("MA20"),
        "dif": number("DIF"),
        "dea": number("DEA"),
        "macd": number("MACD"),
        "rsi14": number("RSI14"),
        "volume_ratio_5d": number("VOL_RATIO"),
        "return_5d": trailing_return(5),
        "return_20d": trailing_return(20),
    }
