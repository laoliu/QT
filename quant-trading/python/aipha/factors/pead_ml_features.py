"""PEAD 事件因子 — ML 训练管线适配版 (P9 因子批次).

将 sue_factor.py 和 analyst_factors.py 中的批量 ML 函数桥接到
标准 add_pX_factors(df) 接口，供 train_ml_factor.py --pead-factors 调用。

与 P2 SUE 的区别:
  - P2 SUE 使用面板中已有的 dt_netprofit_yoy（财报字段代理）
  - P9 SUE 使用 TuShare 业绩快报实际 EPS（更及时，PIT 合规）

因子清单 (6 个):
  - sue_express: 业绩快报 SUE 标准化意外盈利
  - analyst_revision_3m: 分析师预期修正率
  - forecast_surprise: 业绩预告超预期分数
  - analyst_coverage_count: 分析师覆盖数（置信度权重）
  - insider_net_buy_90d: 内幕人净增持金额(log)
  - insider_txn_count: 内幕人交易笔数

数据来源: data/cache/advanced (express), data/cache/alternative (analyst),
           data/cache/holder (holdertrade)
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "add_pead_factors",
    "PEAD_FACTOR_NAMES",
]

PEAD_FACTOR_NAMES: list[str] = [
    "sue_express", "cs_rank_sue_express",
    "analyst_revision_3m", "cs_rank_analyst_revision_3m",
    "forecast_surprise", "cs_rank_forecast_surprise",
    "analyst_coverage_count", "cs_rank_analyst_coverage_count",
    "insider_net_buy_90d", "cs_rank_insider_net_buy_90d",
    "insider_txn_count", "cs_rank_insider_txn_count",
]


def add_pead_factors(
    df: pd.DataFrame,
    express_cache_dir: str | Path = "data/cache/advanced",
    analyst_cache_dir: str | Path = "data/cache/alternative",
    forecast_cache_dir: str | Path = "data/cache/advanced",
    holdertrade_cache_dir: str | Path = "data/cache/holder",
) -> int:
    """向面板 DataFrame 添加 6 个 PEAD 事件因子（就地修改）。

    Args:
        df: 面板数据，需含 datetime, symbol 列。就地修改。
        express_cache_dir: 业绩快报缓存目录
        analyst_cache_dir: 分析师预测缓存目录
        forecast_cache_dir: 业绩预告缓存目录
        holdertrade_cache_dir: 增减持缓存目录

    Returns:
        新增因子列数。
    """
    if "datetime" not in df.columns or "symbol" not in df.columns:
        logger.warning("PEAD: 面板缺少 datetime/symbol 列, 跳过")
        return 0

    symbols = sorted(df["symbol"].unique().tolist())
    trade_dates = sorted(df["datetime"].astype(str).unique().tolist())
    # 统一日期格式为 YYYYMMDD (去除 - 分隔符)
    trade_dates = [d.replace("-", "") for d in trade_dates]

    if not symbols or not trade_dates:
        logger.warning("PEAD: 无标的或无日期, 跳过")
        return 0

    logger.info(
        "PEAD 因子计算: %d 标的 × %d 日期",
        len(symbols), len(trade_dates),
    )

    added = 0

    # --- 1. SUE 业绩快报因子 ---
    added += _add_sue_express(df, symbols, trade_dates, express_cache_dir,
                              analyst_cache_dir)

    # --- 2. 分析师修正 + 预告超预期因子 ---
    added += _add_analyst_factors(df, symbols, trade_dates, analyst_cache_dir,
                                  forecast_cache_dir)

    # --- 3. 内幕人交易因子 ---
    added += _add_insider_factors(df, symbols, trade_dates,
                                  holdertrade_cache_dir)

    logger.info("PEAD 事件因子: 共新增 %d 列", added)
    return added


def _merge_batch_result(
    df: pd.DataFrame,
    batch_df: pd.DataFrame,
    value_col: str,
    target_name: str,
) -> int:
    """将批量计算结果合并到面板并添加截面排名。

    使用 dict 查找 + map() 替代 merge()，避免复制整个 DataFrame
    导致 OOM（3M 行 × 264 列 merge 会消耗 6+ GB）。

    Args:
        df: 面板 DataFrame（就地修改）
        batch_df: 批量计算结果 [ts_code, trade_date, value_col]
        value_col: 批量结果中的值列名
        target_name: 目标因子名（如 sue_express）

    Returns:
        新增列数 (0 或 2)
    """
    if batch_df.empty or value_col not in batch_df.columns:
        df[target_name] = np.nan
        df[f"cs_rank_{target_name}"] = np.nan
        return 2

    # 构建 (symbol, trade_date) → value 查找字典（内存高效）
    valid = batch_df[["ts_code", "trade_date", value_col]].dropna(
        subset=[value_col]
    )
    lookup = dict(
        zip(
            zip(valid["ts_code"].astype(str), valid["trade_date"].astype(str)),
            valid[value_col].values,
        )
    )

    # 构建面板的 key 用于 map（仅生成一列临时 Series）
    dt_str = df["datetime"].astype(str).str.replace("-", "")
    keys = list(zip(df["symbol"].astype(str), dt_str))
    df[target_name] = pd.array(
        [lookup.get(k, np.nan) for k in keys], dtype="Float64"
    )

    # 截面排名 (0~1)
    rank_col = f"cs_rank_{target_name}"
    df[rank_col] = df.groupby("datetime")[target_name].rank(pct=True)

    n_valid = df[target_name].notna().sum()
    logger.debug("%s: %d 有效值 (%.1f%%)",
                 target_name, n_valid,
                 100.0 * n_valid / len(df) if len(df) else 0)
    return 2


def _add_sue_express(
    df: pd.DataFrame,
    symbols: list[str],
    trade_dates: list[str],
    express_cache_dir: str | Path,
    analyst_cache_dir: str | Path,
) -> int:
    """添加 SUE 业绩快报因子。"""
    try:
        from aipha.factors.sue_factor import compute_sue_feature_batch

        batch = compute_sue_feature_batch(
            symbols=symbols,
            trade_dates=trade_dates,
            express_cache_dir=express_cache_dir,
            analyst_cache_dir=analyst_cache_dir,
        )
        return _merge_batch_result(df, batch, "sue_score", "sue_express")
    except Exception as e:
        logger.warning("SUE express 因子计算失败: %s", e)
        df["sue_express"] = np.nan
        df["cs_rank_sue_express"] = np.nan
        return 2


def _add_analyst_factors(
    df: pd.DataFrame,
    symbols: list[str],
    trade_dates: list[str],
    analyst_cache_dir: str | Path,
    forecast_cache_dir: str | Path,
) -> int:
    """添加分析师修正 + 预告超预期 + 覆盖数因子。"""
    try:
        from aipha.factors.analyst_factors import compute_analyst_feature_batch

        batch = compute_analyst_feature_batch(
            symbols=symbols,
            trade_dates=trade_dates,
            analyst_cache_dir=analyst_cache_dir,
            forecast_cache_dir=forecast_cache_dir,
        )
        added = 0
        added += _merge_batch_result(
            df, batch, "analyst_revision_3m", "analyst_revision_3m",
        )
        added += _merge_batch_result(
            df, batch, "forecast_surprise", "forecast_surprise",
        )
        added += _merge_batch_result(
            df, batch, "analyst_coverage_count", "analyst_coverage_count",
        )
        return added
    except Exception as e:
        logger.warning("分析师因子计算失败: %s", e)
        for col in ("analyst_revision_3m", "forecast_surprise",
                     "analyst_coverage_count"):
            df[col] = np.nan
            df[f"cs_rank_{col}"] = np.nan
        return 6


def _add_insider_factors(
    df: pd.DataFrame,
    symbols: list[str],
    trade_dates: list[str],
    holdertrade_cache_dir: str | Path,
) -> int:
    """添加内幕人交易因子。"""
    try:
        from aipha.factors.sue_factor import compute_insider_feature_batch

        batch = compute_insider_feature_batch(
            symbols=symbols,
            trade_dates=trade_dates,
            holdertrade_cache_dir=holdertrade_cache_dir,
        )
        added = 0
        added += _merge_batch_result(
            df, batch, "insider_net_buy_90d", "insider_net_buy_90d",
        )
        added += _merge_batch_result(
            df, batch, "insider_txn_count", "insider_txn_count",
        )
        return added
    except Exception as e:
        logger.warning("内幕人交易因子计算失败: %s", e)
        for col in ("insider_net_buy_90d", "insider_txn_count"):
            df[col] = np.nan
            df[f"cs_rank_{col}"] = np.nan
        return 4
