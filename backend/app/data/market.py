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
def get_index_daily(symbol: str) -> pd.DataFrame:
    """指数日线(新浪),symbol 如 sh000001 / sh000300。"""
    df = ak.stock_zh_index_daily(symbol=symbol)
    df = df.rename(columns={"date": "日期", "open": "开盘", "close": "收盘",
                            "high": "最高", "low": "最低", "volume": "成交量"})
    df["日期"] = df["日期"].astype(str)
    close = df["收盘"].astype(float)
    df["涨跌幅"] = (close.pct_change() * 100).round(2)
    return df


def market_overview_text() -> str:
    """大盘环境文本:上证指数 + 沪深300 近 30 日概览,供市场环境分析师。"""
    lines = []
    for symbol, label in (("sh000001", "上证指数"), ("sh000300", "沪深300")):
        df = get_index_daily(symbol).tail(30)
        last = df.iloc[-1]
        chg_5 = (float(last["收盘"]) / float(df.iloc[-6]["收盘"]) - 1) * 100 if len(df) >= 6 else 0
        chg_20 = (float(last["收盘"]) / float(df.iloc[-21]["收盘"]) - 1) * 100 if len(df) >= 21 else 0
        lines.append(
            f"【{label}】最新收盘 {last['收盘']}({last['日期']}),"
            f"当日涨跌 {last['涨跌幅']}%,近5日 {chg_5:+.2f}%,近20日 {chg_20:+.2f}%")
        lines.append(f"近30日收盘序列(日期,收盘,涨跌幅%):")
        for _, row in df.iterrows():
            lines.append(f"{row['日期']}, {row['收盘']}, {row['涨跌幅']}")
        lines.append("")
    return "\n".join(lines)


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


# ---------- 自动选股 ----------
# 初筛阈值默认见 settings_registry；运行时用 get_setting 读取（可被设置页覆盖）
# 行情主源：扶摇（沪深300+中证500 成分）；新浪全市场仅作兜底（常 Broken pipe / 超时）

import logging

logger = logging.getLogger(__name__)

# 兼容旧测试/引用的模块级常量（等于注册表默认，不随 DB 覆盖变化）
SCREEN_MIN_PRICE = 3.0
SCREEN_MAX_PRICE = 100.0
SCREEN_MIN_TURNOVER = 2e8
SCREEN_MIN_PCT = -3.0
SCREEN_MAX_PCT = 7.0
SCREEN_TOP_N = 30

# 扶摇 snapshot 单次 thscodes 不宜过长
_FUYAO_BATCH = 80


def _symbol_prefix(code: str) -> str:
    code = str(code).zfill(6)
    return f"sh{code}" if code.startswith(("60", "68", "9")) else f"sz{code}"


@ttl_cache(3600)
def get_screen_universe() -> list[tuple[str, str]]:
    """选股宇宙：(code, name)。优先 沪深300 + 中证500 成分。"""
    pairs: dict[str, str] = {}
    for symbol in ("000300", "000905"):  # 沪深300 / 中证500
        try:
            df = ak.index_stock_cons(symbol=symbol)
        except Exception as err:  # noqa: BLE001
            logger.warning("指数成分 %s 拉取失败: %s", symbol, err)
            continue
        if df is None or df.empty:
            continue
        code_col = "品种代码" if "品种代码" in df.columns else (
            "成分券代码" if "成分券代码" in df.columns else df.columns[0]
        )
        name_col = "品种名称" if "品种名称" in df.columns else (
            "成分券名称" if "成分券名称" in df.columns else None
        )
        for _, row in df.iterrows():
            code = str(row[code_col]).zfill(6)[-6:]
            if not code.isdigit():
                continue
            name = str(row[name_col]) if name_col else code
            pairs[code] = name
    if not pairs:
        raise RuntimeError("无法获取选股宇宙（沪深300/中证500 成分）")
    return sorted(pairs.items(), key=lambda x: x[0])


def _snapshot_via_fuyao() -> pd.DataFrame:
    """扶摇批量行情 → 与 screen_candidates 兼容的 DataFrame 列。"""
    from . import fuyao_client as fuyao

    if not fuyao.available():
        raise RuntimeError("扶摇未配置")

    universe = get_screen_universe()
    name_map = dict(universe)
    codes = [c for c, _ in universe]
    rows = []
    for i in range(0, len(codes), _FUYAO_BATCH):
        chunk = codes[i:i + _FUYAO_BATCH]
        try:
            items = fuyao.prices_snapshot(chunk)
        except Exception as err:  # noqa: BLE001
            logger.warning("扶摇 snapshot 批次失败 %s…: %s", chunk[:3], err)
            continue
        for it in items or []:
            code = str(it.get("ticker") or fuyao.from_thscode(it.get("thscode", ""))).zfill(6)[-6:]
            price = it.get("last_price")
            pct = it.get("price_change_ratio_pct")
            turnover = it.get("turnover")
            if price is None:
                continue
            rows.append({
                "代码": _symbol_prefix(code),
                "名称": name_map.get(code, code),
                "最新价": float(price),
                "涨跌幅": float(pct) if pct is not None else 0.0,
                "成交额": float(turnover) if turnover is not None else 0.0,
            })
    if not rows:
        raise RuntimeError("扶摇行情快照为空")
    return pd.DataFrame(rows)


@ttl_cache(600)
def get_market_snapshot() -> pd.DataFrame:
    """选股用截面快照。优先扶摇(指数成分)，失败再试新浪全市场。"""
    errors: list[str] = []
    try:
        df = _snapshot_via_fuyao()
        logger.info("选股快照来源=扶摇, 行数=%d", len(df))
        return df
    except Exception as err:  # noqa: BLE001
        errors.append(f"扶摇: {err}")
        logger.warning("扶摇选股快照失败,尝试新浪: %s", err)

    try:
        df = ak.stock_zh_a_spot()
        logger.info("选股快照来源=新浪, 行数=%d", len(df))
        return df
    except Exception as err:  # noqa: BLE001
        errors.append(f"新浪: {err}")
        msg = "；".join(errors)
        # 常见：Broken pipe / ConnectTimeout — 给前端可读说明
        raise RuntimeError(
            f"选股行情快照失败（{msg}）。"
            "请确认 FUYAO_API_KEY 可用；新浪全市场接口常被掐断。"
        ) from err


def screen_candidates(exclude_codes: set[str] | None = None,
                      snapshot: pd.DataFrame | None = None) -> list[dict]:
    """规则初筛候选股:普通沪深 A 股、非 ST、价格/流动性/涨跌幅过滤,
    按成交额降序取前 top_n（可配置）。"""
    from ..runtime_settings import get_setting

    min_price = float(get_setting("selector.screen.min_price"))
    max_price = float(get_setting("selector.screen.max_price"))
    min_turnover = float(get_setting("selector.screen.min_turnover_yi")) * 1e8
    min_pct = float(get_setting("selector.screen.min_pct"))
    max_pct = float(get_setting("selector.screen.max_pct"))
    top_n = int(get_setting("selector.screen.top_n"))

    exclude_codes = exclude_codes or set()
    df = snapshot if snapshot is not None else get_market_snapshot()
    out = []
    for _, row in df.iterrows():
        symbol = str(row["代码"])  # 如 sh600519 / bj920000
        code = symbol[-6:]
        if not symbol.startswith(("sh", "sz")):
            continue
        if not code.startswith(("60", "00", "30", "68")):
            continue
        if code in exclude_codes:
            continue
        name = str(row["名称"])
        if "ST" in name.upper() or "退" in name:
            continue
        try:
            price = float(row["最新价"])
            pct = float(row["涨跌幅"])
            turnover = float(row["成交额"])
        except (TypeError, ValueError):
            continue
        if not (min_price <= price <= max_price):
            continue
        if turnover < min_turnover:
            continue
        if not (min_pct <= pct <= max_pct):
            continue
        out.append({"code": code, "name": name, "price": price,
                    "pct_change": pct, "turnover": turnover})
    out.sort(key=lambda x: x["turnover"], reverse=True)
    return out[:top_n]
