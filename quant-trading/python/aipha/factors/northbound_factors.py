"""北向资金流因子模块 — 基于沪深股通 top10 数据。

O10 特征空间扩展: 利用 TuShare hsgt_top10 API 构建个股级别北向资金因子。

**北向资金因子 (5个)**:
- nb_net_buy_ratio: 北向净买入 / 总成交额
- nb_buy_intensity: 北向买入力度 (买入额 / 日均成交额)
- nb_concentration: 北向持续买入集中度 (近 N 日上榜次数)
- nb_momentum_5d: 5 日北向净买入动量 (方向持续性)
- nb_rank_score: 加权排名分 (排名越高, 买入越活跃)

理论基础:
- 北向资金 (沪深股通) 被认为是 "聪明钱" (smart money)
- 北向资金持续买入的标的, 后续超额收益显著 (多项实证研究)
- 仅 top10 标的有个股数据 → 天然筛选高关注度股票
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_northbound_factors(
    nb_df: pd.DataFrame,
    daily_df: Optional[pd.DataFrame] = None,
    lookback: int = 20,
) -> pd.DataFrame:
    """从 hsgt_top10 数据计算北向资金因子。

    Args:
        nb_df: hsgt_top10 数据, 必须包含列:
            ts_code, trade_date, amount, net_amount, buy, sell, rank
        daily_df: 日线行情数据 (可选), 包含 ts_code, trade_date, amount
            若提供则用于计算 nb_buy_intensity (买入额/日均成交额)
        lookback: 滚动窗口天数 (默认 20 个交易日)

    Returns:
        DataFrame: symbol, datetime, + 5 个北向因子列
    """
    if nb_df.empty:
        logger.warning("北向数据为空，跳过北向因子计算")
        return pd.DataFrame()

    df = _standardize_columns(nb_df)

    # 必须列检查
    required = {"symbol", "datetime", "amount", "net_amount", "rank"}
    missing = required - set(df.columns)
    if missing:
        logger.warning("北向数据缺少列: %s", missing)
        return pd.DataFrame()

    # 若同一 symbol×datetime 有多条 (沪+深), 聚合
    agg_cols = {"amount": "sum", "net_amount": "sum",
                "buy": "sum", "sell": "sum", "rank": "min"}
    agg_cols = {k: v for k, v in agg_cols.items() if k in df.columns}
    df = df.groupby(["symbol", "datetime"], as_index=False).agg(agg_cols)

    # 各子因子
    ratio = _compute_net_buy_ratio(df)
    conc = _compute_concentration(df, lookback)
    mom = _compute_momentum(df, window=5)
    rank = _compute_rank_score(df)

    # 合并
    result = ratio.copy()
    for sub in [conc, mom, rank]:
        result = result.merge(sub, on=["symbol", "datetime"], how="outer")

    # nb_buy_intensity: 买入额 / 日均成交额 (需要 daily_df)
    if daily_df is not None and not daily_df.empty:
        daily = _standardize_columns(daily_df)
        if "amount" in daily.columns:
            avg = (
                daily.sort_values(["symbol", "datetime"])
                .groupby("symbol")["amount"]
                .rolling(lookback, min_periods=5)
                .mean()
                .reset_index(level=0, drop=True)
            )
            daily["avg_amount"] = avg.values
            buy_map = df.set_index(["symbol", "datetime"])["buy"]
            tmp = daily[["symbol", "datetime", "avg_amount"]].copy()
            tmp = tmp.merge(
                buy_map.reset_index(), on=["symbol", "datetime"], how="inner",
            )
            tmp["nb_buy_intensity"] = (
                tmp["buy"] / tmp["avg_amount"].replace(0, np.nan)
            ).clip(0, 10)
            result = result.merge(
                tmp[["symbol", "datetime", "nb_buy_intensity"]],
                on=["symbol", "datetime"],
                how="left",
            )
    if "nb_buy_intensity" not in result.columns:
        result["nb_buy_intensity"] = np.nan

    factor_cols = [
        "nb_net_buy_ratio", "nb_buy_intensity",
        "nb_concentration", "nb_momentum_5d", "nb_rank_score",
    ]
    logger.info(
        "北向因子计算完成: %d 条, %d 只标的",
        len(result), result["symbol"].nunique(),
    )
    return result[["symbol", "datetime"] + factor_cols]


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """标准化列名: ts_code→symbol, trade_date→datetime。"""
    out = df.copy()
    if "ts_code" in out.columns:
        out = out.rename(columns={"ts_code": "symbol"})
    if "trade_date" in out.columns:
        out["trade_date"] = pd.to_datetime(out["trade_date"].astype(str))
        out = out.rename(columns={"trade_date": "datetime"})
    return out


def _compute_net_buy_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """计算 nb_net_buy_ratio: 北向净买入 / 总成交额。

    net_buy_ratio = net_amount / amount, clip to [-1, 1]
    """
    out = df[["symbol", "datetime"]].copy()
    amount = df["amount"].replace(0, np.nan)
    out["nb_net_buy_ratio"] = (df["net_amount"] / amount).clip(-1, 1)
    return out


def _compute_concentration(
    df: pd.DataFrame, lookback: int,
) -> pd.DataFrame:
    """计算 nb_concentration: 近 N 日上榜次数 / lookback。

    上榜次数越多, 表示北向资金对该标的兴趣越持续。
    """
    # 每个symbol×日期只出现一次(已聚合), 标记出现=1
    presence = df[["symbol", "datetime"]].copy()
    presence["appeared"] = 1

    # 按 symbol 滚动计算最近 lookback 天出现次数
    presence = presence.sort_values(["symbol", "datetime"])
    presence["nb_concentration"] = (
        presence.groupby("symbol")["appeared"]
        .rolling(lookback, min_periods=1)
        .sum()
        .reset_index(level=0, drop=True)
    ) / lookback
    return presence[["symbol", "datetime", "nb_concentration"]]


def _compute_momentum(
    df: pd.DataFrame, window: int = 5,
) -> pd.DataFrame:
    """计算 nb_momentum_5d: 近 window 天 net_amount 符号一致性。

    momentum = sum(sign(net_amount_i)) / window, 范围 [-1, 1]
    +1 表示连续 window 天净买入, -1 表示连续净卖出。
    """
    sub = df[["symbol", "datetime", "net_amount"]].copy()
    sub = sub.sort_values(["symbol", "datetime"])
    sub["sign"] = np.sign(sub["net_amount"])
    sub["nb_momentum_5d"] = (
        sub.groupby("symbol")["sign"]
        .rolling(window, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )
    return sub[["symbol", "datetime", "nb_momentum_5d"]]


def _compute_rank_score(df: pd.DataFrame) -> pd.DataFrame:
    """计算 nb_rank_score: 加权排名分 (1/rank 归一化)。

    rank=1 得分最高 (1.0), rank=10 得分最低 (0.1)。
    沪市 top10 + 深市 top10 合计, 按日期截面归一化。
    """
    out = df[["symbol", "datetime"]].copy()
    rank = df["rank"].clip(1, 20)
    out["nb_rank_score"] = 1.0 / rank
    return out
