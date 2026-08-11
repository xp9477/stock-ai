"""S3 因子与回测引擎单测（纯合成数据，不依赖网络）。"""
import numpy as np
import pandas as pd

from app.backtest.engine import run_equal_weight_buyhold, run_factor_weekly
from app.backtest.metrics import compute_metrics, mark_sample_ok
from app.factors.definitions import FACTOR_NAMES, board_of_code, compute_all_factors, compute_price_factors
from app.factors.score import composite_scores, select_top_n
from app.ledger import factsheet_hash


def _synth_bars(n: int = 120, seed: int = 0, drift: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-01", periods=n)
    rets = rng.normal(drift, 0.02, size=n)
    close = 100 * np.cumprod(1 + rets)
    volume = rng.integers(1e5, 1e6, size=n).astype(float)
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.99,
        "high": close * 1.01,
        "low": close * 0.98,
        "close": close,
        "volume": volume,
        "amount": volume * close,
    })


def test_price_factors_s3_columns():
    bars = _synth_bars()
    df = compute_price_factors(bars)
    for col in ("mom_short", "mom_mid", "low_vol", "rev_1m", "low_turn", "size_proxy"):
        assert col in df.columns
    assert df["mom_short"].notna().sum() > 50
    assert df["rev_1m"].notna().sum() > 50
    assert df["low_turn"].notna().sum() > 50


def test_all_factors_with_valuation_and_roe():
    bars = _synth_bars(80, seed=1)
    basic = pd.DataFrame({
        "date": bars["date"],
        "pe_ttm": np.linspace(8, 20, len(bars)),
        "pb": np.linspace(1, 3, len(bars)),
    })
    fina = pd.DataFrame({
        "ann_date": ["20220115", "20220420", "20220720"],
        "end_date": ["20211231", "20220331", "20220630"],
        "roe": [8.0, 10.0, 12.0],
    })
    df = compute_all_factors(bars, basic, fina)
    for name in FACTOR_NAMES:
        assert name in df.columns
    early = df[df["date"] < "2022-01-15"]
    if not early.empty:
        assert early["quality_roe"].isna().all() or early["quality_roe"].iloc[-1] != 12.0
    late = df[df["date"] >= "2022-07-20"]
    assert (late["quality_roe"] == 12.0).all()
    # 第三期相对第二期 growth = 2
    assert (late["growth_roe"] == 2.0).all()


def test_composite_and_top_n_and_neutralize():
    snap = pd.DataFrame({
        "code": ["600000", "000001", "300001", "688001"],
        "mom_short": [0.1, 0.0, -0.1, 0.05],
        "mom_mid": [0.2, 0.1, -0.05, 0.0],
        "low_vol": [-0.01, -0.02, -0.03, -0.015],
        "ep": [0.1, 0.05, 0.02, 0.08],
        "bp": [0.5, 0.4, 0.3, 0.45],
        "quality_roe": [15, 10, 5, 12],
        "rev_1m": [0.02, -0.01, 0.03, 0.0],
        "low_turn": [-1.0, -1.2, -0.8, -1.1],
        "growth_roe": [1, 0, -1, 0.5],
        "size_proxy": [10, 12, 8, 9],
    })
    scored = composite_scores(snap, neutralize_size=True, neutralize_board=True)
    assert scored["score"].notna().all()
    assert "z_rev_1m" in scored.columns
    top = select_top_n(scored, n=2)
    assert len(top) == 2
    assert top[0] in scored["code"].values

    # 关闭中性化也应能跑
    scored2 = composite_scores(snap, neutralize_size=False, neutralize_board=False)
    assert scored2["score"].notna().all()


def test_board_of_code():
    assert board_of_code("600519") == "SH"
    assert board_of_code("000001") == "SZ"
    assert board_of_code("300750") == "ChiNext"
    assert board_of_code("688981") == "STAR"


def test_backtest_equal_weight_and_factor():
    frames = []
    for i, code in enumerate(["600000", "600001", "600002", "600003", "600004"]):
        bars = _synth_bars(150, seed=i, drift=0.0005 + i * 0.0002)
        fac = compute_price_factors(bars)
        fac["ep"] = 0.05 + i * 0.01
        fac["bp"] = 0.3 + i * 0.02
        fac["quality_roe"] = 8 + i
        fac["growth_roe"] = 0.5 * i
        fac["code"] = code
        frames.append(fac)
    panel = pd.concat(frames, ignore_index=True)
    eq = run_equal_weight_buyhold(panel, initial_cash=1_000_000)
    fac = run_factor_weekly(panel, top_n=3, initial_cash=1_000_000)
    assert len(eq.equity) > 50
    assert len(fac.equity) > 50
    assert eq.metrics["n_days"] > 50
    assert "sharpe" in fac.metrics
    assert fac.closed_trades >= 0


def test_metrics_sample_ok():
    eq = pd.Series(np.linspace(1.0, 1.2, 70))
    m = compute_metrics(eq, closed_trades=10)
    assert "sharpe" in m
    flagged = mark_sample_ok({**m, "closed_trades": 10}, min_days=60, min_trades=100)
    assert flagged["sample_ok"] is False
    flagged2 = mark_sample_ok({**m, "closed_trades": 120}, min_days=60, min_trades=100)
    assert flagged2["sample_ok"] is True


def test_factsheet_hash_stable():
    h1 = factsheet_hash({"a": 1, "b": 2})
    h2 = factsheet_hash({"b": 2, "a": 1})
    assert h1 == h2 and len(h1) == 16


def test_factor_rebalance_uses_previous_close_signal():
    dates = [pd.Timestamp("2024-01-05"), pd.Timestamp("2024-01-08")]
    rows = []
    for dt in dates:
        monday = dt.weekday() == 0
        rows.extend([
            {"date": dt, "code": "000001", "close": 10.0,
             "mom_short": -5.0 if monday else 5.0, "mom_mid": -5.0 if monday else 5.0},
            {"date": dt, "code": "000002", "close": 10.0,
             "mom_short": 5.0 if monday else -5.0, "mom_mid": 5.0 if monday else -5.0},
        ])
    result = run_factor_weekly(
        pd.DataFrame(rows), top_n=1, initial_cash=100_000, rebalance="W-MON")
    assert result.holdings_log[0]["date"] == "2024-01-08"
    assert result.holdings_log[0]["signal_date"] == "2024-01-05"
    assert result.holdings_log[0]["codes"] == ["000001"]
