"""行业相对因子 — 行业内中位数偏差 + MAD 标准化 + 行业排名百分位"""
from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _detect_factor_cols(df: pd.DataFrame, industry_col: str) -> list[str]:
    """自动检测数值列作为因子列"""
    return [c for c in df.columns
            if c != industry_col and pd.api.types.is_numeric_dtype(df[c])]


def _adjust_single_factor(
    group: pd.DataFrame, col: str, min_size: int,
) -> pd.DataFrame:
    """单因子行业内中位数偏差 + MAD 标准化"""
    if len(group) < min_size:
        group[f"{col}_ind_rel"] = np.nan
        group[f"{col}_ind_rank"] = np.nan
        return group
    median_val = group[col].median()
    mad = (group[col] - median_val).abs().median()
    if mad > 1e-10:
        group[f"{col}_ind_rel"] = (group[col] - median_val) / (1.4826 * mad)
    else:
        group[f"{col}_ind_rel"] = 0.0
    group[f"{col}_ind_rank"] = group[col].rank(pct=True)
    return group


def compute_industry_relative(
    factor_df: pd.DataFrame,
    industry_col: str = "industry",
    factor_cols: list[str] | None = None,
    min_group_size: int = 5,
) -> pd.DataFrame:
    """计算行业相对因子值

    对每个因子列，在行业组内执行:
    1. 中位数偏差 / MAD 标准化 → ``{col}_ind_rel``
    2. 行业内百分位排名 → ``{col}_ind_rank``

    Parameters
    ----------
    factor_df : 含因子值和行业列的 DataFrame
    industry_col : 行业列名
    factor_cols : 指定因子列; None 时自动检测数值列
    min_group_size : 行业组最小样本数, 不足的输出 NaN
    """
    if industry_col not in factor_df.columns:
        raise KeyError(f"行业列 '{industry_col}' 不存在")

    cols = factor_cols or _detect_factor_cols(factor_df, industry_col)
    if not cols:
        logger.warning("未检测到因子列")
        return factor_df

    result = factor_df.copy()
    for col in cols:
        result = result.groupby(industry_col, group_keys=False).apply(
            _adjust_single_factor, col=col, min_size=min_group_size,
        )
    return result
