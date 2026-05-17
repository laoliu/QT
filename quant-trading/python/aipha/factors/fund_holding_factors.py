"""基金持仓因子 — 机构拥挤度与变动.

Phase F / F-7: A股基本面 L-4 P2 因子.

经济学解释 (原则 A):
    基金重仓股 → 机构拥挤风险 (2024 DMA 踩踏教训);
    基金增仓 → 信息优势信号; 减仓 → 抛售压力.
    学术支持: Sias (2004) 机构交易自相关;
    Anton & Polk (2014) 共同持仓关联流动性.

因子列表:
    - fund_crowd_score: 持有该股票的基金数量 (拥挤度)
    - fund_holding_mv_ratio: 基金持有市值占总流通市值比
    - fund_holding_change: 基金持仓市值季度环比变化

数据源: TuShareFundPortfolioProvider.load_fund_portfolio()
PIT: 使用 ann_date (公告日) 做时点对齐.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_fund_holding_factors(
    fund_df: pd.DataFrame,
) -> pd.DataFrame:
    """计算基金持仓因子 (按被持股票聚合).

    Args:
        fund_df: fund_portfolio API 原始数据.
            必须包含 [symbol, ann_date, end_date, mkv].
            symbol 此处为被持有的股票代码.

    Returns:
        DataFrame: 按 (symbol, end_date) 聚合, 增加因子列.
    """
    if fund_df.empty:
        return fund_df

    df = fund_df.copy()
    df["mkv"] = pd.to_numeric(df["mkv"], errors="coerce")

    # 按 (被持股票, 报告期) 聚合
    agg = df.groupby(["symbol", "end_date"]).agg(
        fund_crowd_score=("ts_code", "nunique"),
        fund_holding_total_mv=("mkv", "sum"),
        ann_date=("ann_date", "max"),
    ).reset_index()

    # 基金持仓市值占比 (需外部传入 circ_mv, 此处仅计算绝对值)
    agg["fund_holding_mv_ratio"] = np.nan

    # 季度环比变化
    agg = agg.sort_values(["symbol", "end_date"]).reset_index(drop=True)
    agg["fund_holding_change"] = agg.groupby("symbol")[
        "fund_holding_total_mv"
    ].pct_change()

    return agg
