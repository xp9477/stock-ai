"""S3 因子集：S2 基础 + 反转 / 低换手 / ROE 改善；截面规模与板块中性。"""
from .definitions import FACTOR_NAMES, compute_price_factors
from .panel import build_factor_panel, latest_factor_snapshot
from .score import composite_scores, select_top_n

__all__ = [
    "FACTOR_NAMES",
    "compute_price_factors",
    "build_factor_panel",
    "latest_factor_snapshot",
    "composite_scores",
    "select_top_n",
]
