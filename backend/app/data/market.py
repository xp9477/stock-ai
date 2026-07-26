"""数据层:实时行情/日K 用腾讯接口,指数/交易日历用新浪(AKShare),新闻用东财。

东财 push2 行情接口在部分网络环境不可用,故行情主数据源选腾讯 qt.gtimg.cn。
"""
from datetime import date, timedelta

import akshare as ak
import pandas as pd
import requests

from .cache import ttl_cache

_TX_QUOTE_URL = "https://qt.gtimg.cn/q={symbols}"


def _tx_symbol(code: str) -> str:
    if code.startswith(("60", "68")):
        return f"sh{code}"
    return f"sz{code}"


@ttl_cache(30)
def _tx_quote_raw(code: str) -> list[str] | None:
    """腾讯单股实时行情原始字段列表。"""
    resp = requests.get(_TX_QUOTE_URL.format(symbols=_tx_symbol(code)), timeout=10)
    resp.encoding = "gbk"
    text = resp.text.strip()
    if '="' not in text:
        return None
    fields = text.split('="', 1)[1].rstrip('";\n').split("~")
    if len(fields) < 47 or not fields[3]:
        return None
    return fields


def get_quote(code: str) -> dict | None:
    """单只股票实时行情。找不到或停牌无价格返回 None。"""
    fields = _tx_quote_raw(code)
    if fields is None:
        return None

    def to_float(value: str) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    price = to_float(fields[3])
    if not price or price <= 0:
        price = to_float(fields[4])  # 停牌用昨收
    if not price:
        return None
    return {
        "code": code,
        "name": fields[1],
        "price": price,
        "pct_change": to_float(fields[32]) or 0.0,
        "volume": to_float(fields[36]) or 0.0,       # 成交量(手)
        "turnover": (to_float(fields[37]) or 0.0) * 1e4,  # 成交额(元)
        "pe": to_float(fields[39]),
        "pb": to_float(fields[46]),
        "market_cap": (to_float(fields[45]) or 0.0) * 1e8 or None,  # 总市值(元)
    }


@ttl_cache(1800)
def get_daily_kline(code: str) -> pd.DataFrame:
    """近 120 个交易日日 K(腾讯),列名对齐东财风格。"""
    start = (date.today() - timedelta(days=240)).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist_tx(symbol=_tx_symbol(code), start_date=start,
                               end_date=date.today().strftime("%Y%m%d"))
    df = df.rename(columns={"date": "日期", "open": "开盘", "close": "收盘",
                            "high": "最高", "low": "最低", "volume": "成交量"})
    close = df["收盘"].astype(float)
    df["涨跌幅"] = (close.pct_change() * 100).round(2)
    return df.tail(120).reset_index(drop=True)


@ttl_cache(3600)
def get_stock_info(code: str) -> dict:
    """个股基本信息。东财接口不可用时降级为腾讯行情推导。"""
    try:
        df = ak.stock_individual_info_em(symbol=code, timeout=10)
        return {str(row["item"]): str(row["value"]) for _, row in df.iterrows()}
    except Exception:  # noqa: BLE001
        quote = get_quote(code) or {}
        return {
            "股票简称": quote.get("name", ""),
            "总市值": quote.get("market_cap", ""),
            "市盈率(动态)": quote.get("pe", ""),
            "市净率": quote.get("pb", ""),
            "说明": "详细工商信息暂不可用,请基于估值与你的知识分析",
        }


@ttl_cache(600)
def get_news(code: str) -> list[dict]:
    """个股新闻近 10 条(东财)。"""
    df = ak.stock_news_em(symbol=code)
    items = []
    for _, row in df.head(10).iterrows():
        items.append({
            "title": str(row["新闻标题"]),
            "content": str(row["新闻内容"])[:200],
            "time": str(row["发布时间"]),
        })
    return items


@ttl_cache(1800)
def get_hs300_history() -> pd.DataFrame:
    """沪深300 指数日线(新浪),列名对齐东财风格。"""
    df = ak.stock_zh_index_daily(symbol="sh000300")
    df = df.rename(columns={"date": "日期", "close": "收盘"})
    df["日期"] = df["日期"].astype(str)
    start = (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")
    return df[df["日期"] >= start].reset_index(drop=True)


@ttl_cache(86400)
def _trade_dates() -> set:
    df = ak.tool_trade_date_hist_sina()
    return {d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in df["trade_date"]}


def is_trade_date(day: date | None = None) -> bool:
    day = day or date.today()
    try:
        return day.strftime("%Y-%m-%d") in _trade_dates()
    except Exception:  # noqa: BLE001
        return day.weekday() < 5  # 日历获取失败退化为工作日判断


def validate_code(code: str) -> dict | None:
    """校验代码是否为可交易的普通 A 股(排除 ST/北交所),返回行情或 None。"""
    if not (len(code) == 6 and code.isdigit()):
        return None
    if not code.startswith(("60", "00", "30", "68")):
        return None  # 仅沪深主板/创业板/科创板
    quote = get_quote(code)
    if quote is None:
        return None
    if "ST" in quote["name"].upper():
        return None
    return quote
