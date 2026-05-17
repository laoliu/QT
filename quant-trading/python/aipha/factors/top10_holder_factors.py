"""前十大股东因子 — 股权集中度与变动.

Phase F / F-7: A股基本面 L-4 P2 因子.

经济学解释 (原则 A):
    股权集中度越高 → 大股东监督力度越大 → 公司治理效应.
    机构投资者持股变化 → 信息优势信号.
    学术支持: Shleifer & Vishny (1986) 大股东监督;
    Nofsinger & Sias (1999) 机构持仓变化与未来收益.

因子列表:
    - top10_concentration: 前十大合计持股比例
    - top10_concentration_change: 集中度季度环比变化
    - top10_institutional_ratio: 机构类股东持股占比

数据源: TuShareTop10HolderProvider.load_top10_holders()
PIT: 使用 ann_date (公告日) 做时点对齐.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_top10_holder_factors(
    holders_df: pd.DataFrame,
) -> pd.DataFrame:
    """计算前十大股东因子 (单只股票多期).

    Args:
        holders_df: top10_holders API 原始数据.
            必须包含 [ts_code, ann_date, end_date,
            hold_ratio, holder_type].

    Returns:
        DataFrame: 按 (ts_code, end_date) 聚合, 增加因子列.
    """
    if holders_df.empty:
        return holders_df

    df = holders_df.copy()
    for col in ("hold_ratio", "hold_float_ratio"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 按报告期聚合
    agg = df.groupby(["ts_code", "end_date", "ann_date"]).agg(
        top10_concentration=("hold_ratio", "sum"),
        holder_count=("hold_ratio", "count"),
    ).reset_index()

    # 机构类股东持股占比
    if "holder_type" in df.columns:
        inst_mask = df["holder_type"].isin(["基金", "QFII", "社保", "保险", "信托"])
        inst_agg = (
            df[inst_mask]
            .groupby(["ts_code", "end_date"])["hold_ratio"]
            .sum()
            .reset_index()
            .rename(columns={"hold_ratio": "top10_institutional_ratio"})
        )
        agg = agg.merge(inst_agg, on=["ts_code", "end_date"], how="left")
        agg["top10_institutional_ratio"] = agg[
            "top10_institutional_ratio"
        ].fillna(0)
    else:
        agg["top10_institutional_ratio"] = np.nan

    # 集中度环比变化
    agg = agg.sort_values(["ts_code", "end_date"]).reset_index(drop=True)
    agg["top10_concentration_change"] = agg.groupby("ts_code")[
        "top10_concentration"
    ].diff()

    return agg
