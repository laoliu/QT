"""行业相对因子计算模块 — 截面行业中位数调整。

O10 特征空间扩展: 对现有基本面因子做行业中位数调整,
提取行业内相对优势 (industry-relative alpha)。

**与 neutralize.py 的区别**:
- neutralize.py: OLS 回归残差 (Barra 风格, 同时控制市值+行业)
- 本模块: 简单行业中位数/均值调整, 更直观, 适合快速因子扩展

**输出因子 (对每个输入因子 X)**:
- X_ind_rel: (X - industry_median) / industry_mad, MAD 标准化
- X_ind_rank: 行业内排名百分位 (0~1)

理论基础:
- 行业内比较消除行业间系统性差异 (如银行 PE 天然低于科技)
- 行业相对值比绝对值更能预测截面收益差异 (Asness et al., 2013)
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_industry_relative(
    factor_df: pd.DataFrame,
    industry_col: str = "industry",
    factor_cols: list[str] | None = None,
    min_group_size: int = 5,
) -> pd.DataFrame:
    """计算行业相对因子。

    Args:
        factor_df: 截面数据, 必须包含列:
            symbol (或 ts_code), datetime (或 trade_date),
            industry_col, + factor_cols
        industry_col: 行业分类列名
        factor_cols: 要计算行业相对的因子列; None 则自动检测
        min_group_size: 行业最少标的数, 不足则跳过

    Returns:
        原 DataFrame 追加 X_ind_rel 和 X_ind_rank 列
    """
    if factor_df.empty:
        logger.warning("输入为空，跳过行业相对因子计算")
        return factor_df

    df = factor_df.copy()

    # 标准化列名
    if "ts_code" in df.columns and "symbol" not in df.columns:
        df = df.rename(columns={"ts_code": "symbol"})
    if "trade_date" in df.columns and "datetime" not in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
        df = df.rename(columns={"trade_date": "datetime"})

    if industry_col not in df.columns:
        logger.warning("缺少行业列 '%s'，跳过行业相对因子", industry_col)
        return df

    # 确定因子列
    if factor_cols is None:
        factor_cols = _detect_factor_cols(df, industry_col)
    if not factor_cols:
        logger.warning("未检测到数值因子列")
        return df

    # 确定截面分组 key (datetime 或 trade_date)
    date_col = "datetime" if "datetime" in df.columns else None
    if date_col is None:
        logger.info("无日期列，按全量截面计算行业相对因子")
        for col in factor_cols:
            df = df.groupby(
                industry_col, group_keys=False,
            ).apply(
                _adjust_single_factor, col=col, min_size=min_group_size,
                include_groups=False,
            )
            # apply 不含 grouping col, 需重建
            if industry_col not in df.columns:
                df = factor_df[[industry_col]].join(df)
    else:
        for col in factor_cols:
            df = df.groupby(
                [date_col, industry_col], group_keys=False,
            ).apply(
                _adjust_single_factor, col=col, min_size=min_group_size,
                include_groups=False,
            )
            # apply 不含 grouping cols, 需重建
            for gc in [date_col, industry_col]:
                if gc not in df.columns:
                    df[gc] = factor_df[gc].values

    n_new = sum(1 for c in df.columns if c.endswith("_ind_rel"))
    logger.info(
        "行业相对因子: %d 个原始因子 → %d 个新因子 (×2), %d 条",
        len(factor_cols), n_new, len(df),
    )
    return df


def _detect_factor_cols(
    df: pd.DataFrame, industry_col: str,
) -> list[str]:
    """自动检测数值型因子列 (排除 id/date/industry)。"""
    skip = {"symbol", "ts_code", "datetime", "trade_date", "date",
            industry_col, "name", "ann_date", "end_date", "index"}
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    return [c for c in numeric_cols if c.lower() not in skip]


def _adjust_single_factor(
    group: pd.DataFrame, col: str, min_size: int,
) -> pd.DataFrame:
    """对单个截面、单个因子做行业内中位数调整。

    X_ind_rel = (X - median_X) / MAD_X
    X_ind_rank = rank(X) / count (percentile 0~1)
    """
    rel_col = f"{col}_ind_rel"
    rank_col = f"{col}_ind_rank"

    vals = group[col]
    n_valid = vals.notna().sum()

    if n_valid < min_size:
        group[rel_col] = np.nan
        group[rank_col] = np.nan
        return group

    med = vals.median()
    mad = (vals - med).abs().median()
    if mad < 1e-12:
        mad = vals.std()
    if mad < 1e-12:
        mad = 1.0

    group[rel_col] = (vals - med) / mad
    group[rank_col] = vals.rank(pct=True, method="average")
    return group
