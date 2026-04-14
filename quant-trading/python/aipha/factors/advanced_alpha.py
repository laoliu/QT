"""高级 Alpha 因子: 残差动量 + 价值盈利复合

知识库优先级:
  #5 残差动量 — Gao, Hu, Kelly et al. (2023)
    剥离市场/行业 beta 后的残差收益动量, +0.5-1.5% AR
  #6 价值盈利复合 — Novy-Marx (2013), Fama-French 5 因子
    EP + BP + ROE 等多指标平均排名, +0.5-1% AR

面向 ML 训练管道 (scripts/train_ml_factor.py) 的 DataFrame 接口。
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def add_residual_momentum(
    df: pd.DataFrame,
    window: int = 20,
    market_ret_col: str | None = None,
    date_col: str = "datetime",
    symbol_col: str = "symbol",
    return_col: str = "pct_chg",
) -> int:
    """计算残差动量因子 (截面残差 → 滚动均值)。

    Gao et al. (2023): res_mom = rolling_mean(stock_ret - beta * mkt_ret)
    简化实现: 使用截面去均值后的残差收益。

    Returns:
        新增列数
    """
    if return_col not in df.columns:
        logger.warning("add_residual_momentum: 缺少 '%s' 列, 跳过", return_col)
        return 0

    added = 0
    ret = df[return_col].astype(np.float64)

    # Step 1: 截面去均值 → 残差收益
    cs_mean = df.groupby(date_col)[return_col].transform("mean")
    residual_ret = ret - cs_mean
    df["residual_ret"] = residual_ret

    # Step 2: 残差动量 = 滚动 20 日残差收益均值 (按个股)
    df["res_momentum_20d"] = (
        df.groupby(symbol_col)["residual_ret"]
        .transform(lambda x: x.rolling(window, min_periods=window // 2).mean())
    )
    added += 1

    # Step 3: 残差波动 = 滚动 20 日残差收益标准差
    df["res_volatility_20d"] = (
        df.groupby(symbol_col)["residual_ret"]
        .transform(lambda x: x.rolling(window, min_periods=window // 2).std())
    )
    added += 1

    # Step 4: 截面排名
    df["cs_rank_res_mom"] = df.groupby(date_col)["res_momentum_20d"].rank(pct=True)
    df["cs_rank_res_vol"] = df.groupby(date_col)["res_volatility_20d"].rank(pct=True)
    added += 2

    # 清理中间列
    df.drop(columns=["residual_ret"], inplace=True, errors="ignore")
    logger.info("残差动量因子: 新增 %d 列", added)
    return added


def add_value_profitability_composite(
    df: pd.DataFrame,
    date_col: str = "datetime",
) -> int:
    """价值+盈利复合因子: 多指标平均排名。

    指标: EP, BP, ROE (若可用), dv_ttm
    排名平均后做截面排名。

    Returns:
        新增列数
    """
    added = 0
    rank_cols: list[str] = []

    # EP = 1/PE (盈利收益率)
    if "pe_ttm" in df.columns:
        ep = 1.0 / df["pe_ttm"].clip(lower=0.1)
        df["_ep_rank"] = df.assign(_ep=ep).groupby(date_col)["_ep"].rank(pct=True)
        df.drop(columns=["_ep"], inplace=True, errors="ignore")
        rank_cols.append("_ep_rank")

    # BP = 1/PB (账面价值收益率)
    if "pb" in df.columns:
        bp = 1.0 / df["pb"].clip(lower=0.1)
        df["_bp_rank"] = df.assign(_bp=bp).groupby(date_col)["_bp"].rank(pct=True)
        df.drop(columns=["_bp"], inplace=True, errors="ignore")
        rank_cols.append("_bp_rank")

    # 股息率
    if "dv_ttm" in df.columns:
        dv = df["dv_ttm"].fillna(0).clip(0, 30)
        df["_dv_rank"] = df.assign(_dv=dv).groupby(date_col)["_dv"].rank(pct=True)
        df.drop(columns=["_dv"], inplace=True, errors="ignore")
        rank_cols.append("_dv_rank")

    # ROE (深度基本面, 如果有)
    if "roe_dt" in df.columns:
        df["_roe_rank"] = df.groupby(date_col)["roe_dt"].rank(pct=True)
        rank_cols.append("_roe_rank")

    if len(rank_cols) < 2:
        logger.warning("价值盈利复合: 可用指标 < 2, 跳过")
        df.drop(columns=rank_cols, inplace=True, errors="ignore")
        return 0

    # 复合 = 多指标平均排名
    df["value_profit_composite"] = df[rank_cols].mean(axis=1)
    df["cs_rank_val_prof"] = (
        df.groupby(date_col)["value_profit_composite"].rank(pct=True)
    )
    added += 2

    # 清理中间排名列
    df.drop(columns=rank_cols, inplace=True, errors="ignore")
    logger.info("价值盈利复合因子: 新增 %d 列 (基于 %d 指标)", added, len(rank_cols))
    return added


def add_advanced_alpha_factors(
    df: pd.DataFrame,
    date_col: str = "datetime",
) -> int:
    """统一入口: 添加所有高级 alpha 因子。

    Returns:
        新增因子总数
    """
    n = 0
    n += add_residual_momentum(df, date_col=date_col)
    n += add_value_profitability_composite(df, date_col=date_col)
    return n
