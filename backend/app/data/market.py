"""数据层：扶摇提供个股行情/日 K，新浪提供指数/交易日历，RSS 提供新闻。"""
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

from .cache import ttl_cache

logger = logging.getLogger(__name__)


@ttl_cache(30)
def get_quote(code: str) -> dict | None:
    """扶摇单股行情快照。找不到或价格无效时返回 ``None``。"""
    from . import fuyao_client as fuyao

    if not fuyao.available():
        return None
    try:
        items = fuyao.prices_snapshot([code])
    except Exception as err:  # noqa: BLE001
        logger.warning("扶摇快照失败 %s: %s", code, err)
        return None
    if not items:
        return None
    it = items[0]
    price = it.get("last_price") or it.get("close_price") or it.get("price")
    if price is None:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    pct = it.get("price_change_ratio_pct")
    if pct is None:
        pct = it.get("change_ratio") or it.get("pct_change") or 0.0
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        pct = 0.0
    received_at = datetime.now(timezone.utc).isoformat()
    prev_close = it.get("prev_price") or it.get("prev_close")
    open_price = it.get("open_price") or it.get("open")
    volume = float(it.get("volume") or 0)
    return {
        "code": code,
        "name": it.get("name") or "",
        "price": price,
        "pct_change": pct,
        "volume": volume,
        "turnover": float(it.get("turnover") or it.get("amount") or 0),
        "pe": None,
        "pb": None,
        "market_cap": None,
        "source": "fuyao",
        "prev_close": float(prev_close) if prev_close is not None else None,
        "open": float(open_price) if open_price is not None else None,
        # 扶摇 snapshot 当前不返回交易所时间；明确标记为客户端接收时间，
        # 避免把它误称为交易所时间戳。
        "quote_asof": received_at,
        "quote_asof_source": "client_received_at",
        "received_at": received_at,
        "tradable": volume > 0,
        "trade_status": "tradable" if volume > 0 else "suspended_or_unavailable",
    }


def get_trade_quote(
    code: str,
    *,
    avg_cost: float | None = None,
) -> dict | None:
    """交易/强平行情：仅使用扶摇，并用同源日 K 与成本做确定性校验。"""
    primary = get_quote(code)
    if primary is None:
        logger.warning("交易行情不可用 code=%s source=fuyao", code)
        return None

    if not bool(primary.get("tradable")):
        logger.warning("交易行情不可交易/疑似停牌 code=%s status=%s",
                       code, primary.get("trade_status"))
        return None

    cleaned = sanitize_quote(primary, code=code, avg_cost=avg_cost)
    if cleaned is None:
        logger.error(
            "交易行情未通过校验 code=%s primary=%s avg_cost=%s",
            code,
            {k: primary.get(k) for k in ("price", "source", "pct_change")},
            avg_cost,
        )
        return None

    logger.info(
        "交易行情 code=%s price=%.4f source=%s pct=%s cost=%s",
        code, cleaned["price"], cleaned.get("source"),
        cleaned.get("pct_change"), avg_cost,
    )
    return cleaned


def get_execution_quote(
    code: str,
    *,
    avg_cost: float | None = None,
    force_refresh: bool = True,
    require_session: bool = True,
) -> dict | None:
    """审批/执行瞬间使用的报价快照。

    与展示行情不同：默认绕过 30 秒缓存、要求连续竞价时段，并要求可交易、
    开盘价、昨收价和供应商时间戳齐全。任何缺失都 fail closed。
    """
    if require_session and not is_trading_session():
        logger.warning("非连续竞价时段，拒绝生成执行报价 code=%s", code)
        return None
    if force_refresh and hasattr(get_quote, "cache_clear"):
        get_quote.cache_clear()
    quote = get_trade_quote(code, avg_cost=avg_cost)
    if quote is None or not bool(quote.get("tradable", True)):
        return None
    required = ("price", "open", "prev_close", "quote_asof", "received_at")
    if any(quote.get(key) in (None, "") for key in required):
        logger.warning(
            "执行报价字段不完整 code=%s missing=%s source=%s",
            code, [key for key in required if quote.get(key) in (None, "")],
            quote.get("source"),
        )
        return None
    try:
        price = float(quote["price"])
        open_price = float(quote["open"])
        prev_close = float(quote["prev_close"])
    except (TypeError, ValueError):
        return None
    if min(price, open_price, prev_close) <= 0:
        return None
    return {
        **quote,
        "opening_gap_pct": open_price / prev_close - 1.0,
    }


@ttl_cache(1800)
def get_daily_kline(code: str) -> pd.DataFrame:
    """近 120 个交易日日 K（扶摇），列名对齐既有指标代码。"""
    from . import fuyao_client as fuyao

    if not fuyao.available():
        raise RuntimeError("扶摇未配置或已禁用，无法拉取日 K")
    start = date.today() - timedelta(days=240)
    df = fuyao.daily_bars(code, start, date.today())
    if df is None or df.empty:
        return pd.DataFrame(
            columns=["日期", "开盘", "收盘", "最高", "最低", "成交量", "涨跌幅"])
    df = df.rename(columns={"date": "日期", "open": "开盘", "close": "收盘",
                            "high": "最高", "low": "最低", "volume": "成交量"})
    close = df["收盘"].astype(float)
    df["涨跌幅"] = (close.pct_change() * 100).round(2)
    return df.tail(120).reset_index(drop=True)


@ttl_cache(3600)
def get_stock_info(code: str) -> dict:
    """个股基本信息。东财接口不可用时降级为扶摇行情摘要。"""
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
def get_news(code: str, name: str = "") -> list[dict]:
    """个股直接相关新闻（公开 RSS，与 factsheet 同源）。

    受 datasources.rss.* 控制：禁用时按 fail_policy 返回空或抛错。

    通用宏观头条不得混入这里。这个返回值会冻结进交易计划的信息
    基线，若把 general fallback 当作个股新闻，盘前门禁就会把空的直接
    新闻集合误报成“新增个股新闻”。
    """
    from . import datasources as ds
    from . import news_rss

    if not ds.is_enabled("rss"):
        if ds.fail_policy("rss", "skip") == "hard":
            raise RuntimeError("新闻 RSS 已禁用（设置 → 数据源）")
        return []
    return news_rss.news_for_stock(
        code, name=name, limit=10, include_general=False)


@ttl_cache(1800)
def get_index_daily(symbol: str) -> pd.DataFrame:
    """指数日线(新浪),symbol 如 sh000001 / sh000300。受 datasources.sina 控制。"""
    from . import datasources as ds
    from .http_timeout import call_with_timeout

    if not ds.is_enabled("sina"):
        if ds.fail_policy("sina", "hard") == "skip":
            return pd.DataFrame(columns=["日期", "开盘", "收盘", "最高", "最低", "成交量", "涨跌幅"])
        raise RuntimeError("新浪数据源已禁用（设置 → 数据源），无法拉取指数")

    to = ds.timeout_sec("sina", 25)
    df = call_with_timeout(ak.stock_zh_index_daily, to, symbol=symbol)
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
    df = get_index_daily("sh000300")
    if df is None or df.empty:
        return pd.DataFrame(columns=["日期", "收盘"])
    start = (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")
    out = df[["日期", "收盘"]].copy()
    return out[out["日期"] >= start].reset_index(drop=True)


@ttl_cache(86400)
def _trade_dates() -> set:
    from . import datasources as ds
    from .http_timeout import call_with_timeout

    if not ds.is_enabled("sina"):
        return set()
    to = ds.timeout_sec("sina", 25)
    df = call_with_timeout(ak.tool_trade_date_hist_sina, to)
    return {d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in df["trade_date"]}


def is_trade_date(day: date | None = None) -> bool:
    day = day or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    try:
        return day.strftime("%Y-%m-%d") in _trade_dates()
    except Exception as err:  # noqa: BLE001
        # 工作日不等于交易日。节假日日历不可验证时，交易安全边界必须
        # fail closed，不能把普通周一误当作可成交日。
        logger.error("交易日日历不可用，拒绝按工作日猜测: %s", err)
        return False


def is_trading_session(now: datetime | None = None) -> bool:
    """A 股连续竞价大致时段。非交易日 / 夜盘一律 False。"""
    from datetime import time as dtime

    shanghai = ZoneInfo("Asia/Shanghai")
    now = now or datetime.now(shanghai)
    if now.tzinfo is not None:
        now = now.astimezone(shanghai).replace(tzinfo=None)
    if not is_trade_date(now.date()):
        return False
    t = now.time().replace(tzinfo=None)
    return (
        dtime(9, 30) <= t <= dtime(11, 30)
        or dtime(13, 0) <= t <= dtime(15, 0)
    )


def last_close_price(code: str) -> float | None:
    """日 K 最近收盘价，用于校验异常实时价。"""
    try:
        df = get_daily_kline(code)
        if df is None or df.empty:
            return None
        return float(df.iloc[-1]["收盘"])
    except Exception:  # noqa: BLE001
        return None


def sanitize_quote(
    quote: dict | None,
    *,
    code: str = "",
    avg_cost: float | None = None,
    max_dev_from_close: float = 0.20,
    max_dev_from_cost: float = 0.55,
) -> dict | None:
    """过滤明显错误的实时价（脏数据 / 非交易时段乱价）。

    历史事故：盘后监控拿到全市场价=7.0，深亏规则按假价强平导致账户腰斩。
    """
    if not quote:
        return None
    try:
        price = float(quote.get("price") or 0)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    # 与成本偏离过大 → 必须有日 K 收盘印证
    if avg_cost and avg_cost > 0:
        dev_cost = abs(price / avg_cost - 1.0)
        if dev_cost >= max_dev_from_cost:
            close = last_close_price(code or quote.get("code") or "")
            if close is None or close <= 0:
                logger.warning(
                    "拒绝异常报价 %s price=%.4f cost=%.4f（无日K校验）",
                    code, price, avg_cost,
                )
                return None
            if abs(price / close - 1.0) > max_dev_from_close:
                logger.warning(
                    "拒绝异常报价 %s price=%.4f close=%.4f cost=%.4f",
                    code, price, close, avg_cost,
                )
                return None

    # 无成本时也与日 K 比对一次（防全市场假价）
    if not avg_cost:
        close = last_close_price(code or quote.get("code") or "")
        if close and close > 0 and abs(price / close - 1.0) > max_dev_from_close:
            logger.warning(
                "拒绝异常报价 %s price=%.4f close=%.4f", code, price, close,
            )
            return None

    return quote


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


def strategy_eligible_quote(code: str) -> tuple[dict | None, str]:
    """Return a quote only when a new position can satisfy hard eligibility.

    The watchlist is an observation list, not an execution universe.  Before
    an LLM is allowed to consider a *new* position, code class, live quote
    validity, ST/delisting labels and one-board-lot affordability are enforced
    by deterministic code.  Existing positions are added separately by the
    engine so an ineligible stock can still be reviewed and sold.
    """
    if not (len(code) == 6 and code.isdigit()):
        return None, "invalid_code"
    if not code.startswith(("60", "00", "30", "68")):
        return None, "unsupported_board"
    quote = get_quote(code)
    if quote is None:
        return None, "quote_unavailable"
    if not bool(quote.get("tradable", True)):
        return None, "not_tradable"
    name = str(quote.get("name") or "")
    if "ST" in name.upper() or "退" in name:
        return None, "special_treatment_or_delisting"
    try:
        price = float(quote.get("price") or 0.0)
    except (TypeError, ValueError):
        return None, "invalid_price"
    if price <= 0:
        return None, "invalid_price"

    from ..runtime_settings import get_setting

    authorized = float(get_setting("capital.authorized_capital"))
    absolute_cap = float(get_setting("capital.max_stock_exposure"))
    single_cap = authorized * float(get_setting("risk.max_position_pct"))
    lot_budget = min(authorized, absolute_cap, single_cap)
    if price * 100 > lot_budget + 1e-9:
        return None, "one_lot_exceeds_single_position_cap"
    return quote, "eligible"


# ---------- 自动选股 ----------
# 初筛阈值默认见 settings_registry；运行时用 get_setting 读取（可被设置页覆盖）
# 行情主源：扶摇（沪深300+中证500 成分）；新浪全市场仅作兜底（常 Broken pipe / 超时）

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
    """选股用截面快照。优先扶摇(指数成分)，失败再按设置试新浪全市场。

    受 datasources.* 启停 / 失败策略控制（见设置页「数据源」）。
    """
    from . import datasources as ds

    errors: list[str] = []

    # ---- 扶摇主源 ----
    if ds.is_enabled("fuyao"):
        try:
            df = _snapshot_via_fuyao()
            logger.info("选股快照来源=扶摇, 行数=%d", len(df))
            return df
        except Exception as err:  # noqa: BLE001
            errors.append(f"扶摇: {err}")
            policy = ds.fail_policy("fuyao", "fallback")
            logger.warning("扶摇选股快照失败 (policy=%s): %s", policy, err)
            if policy == "hard":
                raise RuntimeError(f"选股行情快照失败（扶摇硬失败: {err}）") from err
            if policy == "skip" and not ds.is_enabled("sina"):
                raise RuntimeError(f"选股行情快照失败（扶摇 skip 且新浪未启用: {err}）") from err
            # fallback → 继续新浪
    else:
        errors.append("扶摇: 已禁用")

    # ---- 新浪兜底 ----
    if not ds.is_enabled("sina"):
        msg = "；".join(errors) or "无可用数据源"
        raise RuntimeError(
            f"选股行情快照失败（{msg}；新浪未启用）。请在设置 → 数据源中开启扶摇或新浪。"
        )

    try:
        from .http_timeout import call_with_timeout

        to = ds.timeout_sec("sina", 25)
        df = call_with_timeout(ak.stock_zh_a_spot, to)
        logger.info("选股快照来源=新浪, 行数=%d", len(df))
        return df
    except Exception as err:  # noqa: BLE001
        errors.append(f"新浪: {err}")
        policy = ds.fail_policy("sina", "hard")
        msg = "；".join(errors)
        if policy == "skip":
            logger.warning("新浪快照失败且 policy=skip，返回空表: %s", err)
            return pd.DataFrame(columns=["代码", "名称", "最新价", "涨跌幅", "成交额"])
        raise RuntimeError(
            f"选股行情快照失败（{msg}）。"
            "请确认扶摇 Key 可用；新浪全市场接口常被掐断。可在设置 → 数据源调整启停与失败策略。"
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
