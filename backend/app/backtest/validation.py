"""Point-in-time splits for research and backtests.

The final holdout is deliberately excluded from grid search.  It is only
consumed when a concrete hypothesis is evaluated for promotion.
"""
from __future__ import annotations

import pandas as pd


def split_development_holdout(
    panel: pd.DataFrame,
    development_ratio: float = 0.8,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a factor panel by unique dates, never by rows/stocks."""
    if panel is None or panel.empty:
        return pd.DataFrame(), pd.DataFrame()
    dates = pd.Index(pd.to_datetime(panel["date"]).dropna().unique()).sort_values()
    if len(dates) < 10:
        return panel.copy(), pd.DataFrame()
    cut = min(max(int(len(dates) * development_ratio), 2), len(dates) - 2)
    cutoff = dates[cut]
    normalized = panel.copy()
    normalized["date"] = pd.to_datetime(normalized["date"])
    return (
        normalized[normalized["date"] < cutoff].copy(),
        normalized[normalized["date"] >= cutoff].copy(),
    )
