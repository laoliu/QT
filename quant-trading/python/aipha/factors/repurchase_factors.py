"""回购因子 — 基于股票回购公告数据.

Phase F / F-7: A股基本面 L-4 P2 因子.

经济学解释 (原则 A):
    管理层回购 → 认为股价被低估 → 内部人信号.
    学术支持: Ikenberry et al. (1995/2000) 回购公告后
    长期超额收益 +12%, 尤其 value stock 效应更强.

因子列表:
    - repurchase_amount_ratio: 回购金额 / 流通市值
    - repurchase_premium: 回购价格上限 / 当前股价 - 1

数据源: TuShareRepurchaseProvider.load_repurchase()
PIT: 使用 ann_date (公告日) 做时点对齐.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_repurchase_factors(
    repurchase_df: pd.DataFrame,
    market_cap_df: Optional[pd.DataFrame] = None,
    price_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """计算回购因子.

    Args:
        repurchase_df: repurchase API 原始数据.
            必须包含 [ts_code, ann_date, amount, high_limit].
        market_cap_df: 流通市值数据 (可选).
            包含 [ts_code, trade_date, circ_mv].
        price_df: 价格数据 (可选).
            包含 [ts_code, trade_date, close].

    Returns:
        DataFrame 增加因子列:
            repurchase_amount_ratio, repurchase_premium.
    """
    if repurchase_df.empty:
        return repurchase_df

    df = repurchase_df.copy()

    # 回购金额占比 (需要 market_cap)
    if market_cap_df is not None and not market_cap_df.empty:
        cap = market_cap_df.copy()
        cap["trade_date"] = cap["trade_date"].astype(str)
        df["ann_date"] = df["ann_date"].astype(str)
        merged = df.merge(
            cap[["ts_code", "trade_date", "circ_mv"]],
            left_on=["ts_code", "ann_date"],
            right_on=["ts_code", "trade_date"],
            how="left",
        )
        merged["repurchase_amount_ratio"] = np.where(
            merged["circ_mv"] > 0,
            merged["amount"] / (merged["circ_mv"] * 1e4),
            np.nan,
        )
        df["repurchase_amount_ratio"] = merged["repurchase_amount_ratio"].values
    else:
        df["repurchase_amount_ratio"] = np.nan

    # 回购溢价 (需要 price)
    if price_df is not None and not price_df.empty:
        px = price_df.copy()
        px["trade_date"] = px["trade_date"].astype(str)
        merged = df.merge(
            px[["ts_code", "trade_date", "close"]],
            left_on=["ts_code", "ann_date"],
            right_on=["ts_code", "trade_date"],
            how="left",
        )
        merged["repurchase_premium"] = np.where(
            merged["close"] > 0,
            merged["high_limit"] / merged["close"] - 1,
            np.nan,
        )
        df["repurchase_premium"] = merged["repurchase_premium"].values
    else:
        df["repurchase_premium"] = np.nan

    return df
