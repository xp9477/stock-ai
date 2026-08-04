"""截面标准化、中性化与综合打分（S3）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .definitions import FACTOR_NAMES, board_of_code


def _cs_zscore(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    mu = s.mean(skipna=True)
    sigma = s.std(skipna=True)
    if sigma is None or not np.isfinite(sigma) or sigma < 1e-12:
        return s * 0.0
    return (s - mu) / sigma


def _winsorize(series: pd.Series, p: float = 0.025) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    if s.notna().sum() < 5:
        return s
    lo, hi = s.quantile(p), s.quantile(1 - p)
    return s.clip(lo, hi)


def _residualize(y: pd.Series, *controls: pd.Series) -> pd.Series:
    """截面 OLS 残差：y ~ 1 + controls。样本不足时退回原序列。"""
    yv = pd.to_numeric(y, errors="coerce")
    mats = [pd.to_numeric(c, errors="coerce") for c in controls if c is not None]
    df = pd.concat([yv.rename("y")] + [m.rename(f"x{i}") for i, m in enumerate(mats)], axis=1)
    df = df.dropna()
    if len(df) < max(6, 2 + len(mats)):
        return yv
    y_arr = df["y"].to_numpy(dtype=float)
    x_cols = [c for c in df.columns if c != "y"]
    x_arr = df[x_cols].to_numpy(dtype=float)
    # 加截距
    x_arr = np.column_stack([np.ones(len(x_arr)), x_arr])
    try:
        beta, _, _, _ = np.linalg.lstsq(x_arr, y_arr, rcond=None)
        fitted = x_arr @ beta
        resid = pd.Series(np.nan, index=y.index, dtype=float)
        resid.loc[df.index] = y_arr - fitted
        return resid
    except np.linalg.LinAlgError:
        return yv


def neutralize_cross_section(
    snapshot: pd.DataFrame,
    factor_names: tuple[str, ...] = FACTOR_NAMES,
    use_size: bool = True,
    use_board: bool = True,
) -> pd.DataFrame:
    """对每个因子做截面去极值 → 对 size/板块回归取残差 → 再 z-score。

    返回副本，写入 z_* 列（若中性化开启，z 基于残差）。
    """
    out = snapshot.copy()
    if out.empty:
        return out

    size = None
    if use_size and "size_proxy" in out.columns:
        size = _winsorize(out["size_proxy"])

    board_dummies = None
    if use_board:
        boards = out["code"].astype(str).map(board_of_code) if "code" in out.columns else None
        if boards is not None and boards.nunique(dropna=True) >= 2:
            board_dummies = pd.get_dummies(boards, prefix="b", drop_first=True)

    for name in factor_names:
        if name not in out.columns:
            out[name] = np.nan
        raw = _winsorize(out[name])
        if use_size or use_board:
            controls = []
            if size is not None:
                controls.append(size)
            if board_dummies is not None:
                for col in board_dummies.columns:
                    controls.append(board_dummies[col])
            if controls:
                raw = _residualize(raw, *controls)
        out[f"z_{name}"] = _cs_zscore(raw)
    return out


def composite_scores(
    snapshot: pd.DataFrame,
    factor_names: tuple[str, ...] = FACTOR_NAMES,
    min_factors: int = 2,
    neutralize_size: bool | None = None,
    neutralize_board: bool | None = None,
) -> pd.DataFrame:
    """截面 snapshot → z_* 与 score。

    score = 可用因子 z 分等权平均；有效因子数 < min_factors 的股票 score=NaN。
    默认从 runtime settings 读中性化开关。
    """
    if snapshot is None or snapshot.empty:
        return pd.DataFrame()

    if neutralize_size is None or neutralize_board is None:
        try:
            from ..runtime_settings import get_setting
            if neutralize_size is None:
                neutralize_size = bool(get_setting("factor.neutralize_size"))
            if neutralize_board is None:
                neutralize_board = bool(get_setting("factor.neutralize_board"))
        except Exception:  # noqa: BLE001
            neutralize_size = True if neutralize_size is None else neutralize_size
            neutralize_board = True if neutralize_board is None else neutralize_board

    out = neutralize_cross_section(
        snapshot,
        factor_names=factor_names,
        use_size=bool(neutralize_size),
        use_board=bool(neutralize_board),
    )

    z_cols = [f"z_{n}" for n in factor_names]
    for zc in z_cols:
        if zc not in out.columns:
            out[zc] = np.nan

    z_mat = out[z_cols]
    out["score"] = z_mat.mean(axis=1, skipna=True)
    valid_cnt = z_mat.notna().sum(axis=1)
    out.loc[valid_cnt < min_factors, "score"] = np.nan
    out["n_factors"] = valid_cnt
    return out


def select_top_n(scored: pd.DataFrame, n: int = 10, code_col: str = "code") -> list[str]:
    """按 score 降序取前 n 只代码。"""
    if scored is None or scored.empty or "score" not in scored.columns:
        return []
    df = scored.dropna(subset=["score"]).sort_values("score", ascending=False)
    return df[code_col].astype(str).head(n).tolist()
