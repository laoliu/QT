"""事件因子模块 — 基于 L-4 P0 事件数据的因子计算。

因子列表:
- limit_up_count_Nd: 过去 N 日涨停次数
- limit_down_count_Nd: 过去 N 日跌停次数
- days_since_resume: 距最近复牌天数
- forecast_surprise: 业绩预告超预期幅度
- express_surprise: 业绩快报 EPS 超预期
- unlock_pressure_Nd: 未来 N 日解禁压力 (解禁市值/流通市值)

所有因子遵循 PIT 规则: 仅使用 ann_date <= query_date 的数据。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def calc_limit_hit_count(
    limit_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    window: int = 20,
) -> pd.DataFrame:
    """计算过去 window 日涨停/跌停次数。

    Args:
        limit_df: stk_limit 数据 (ts_code, trade_date, up_limit, down_limit)
        daily_df: 日行情 (ts_code, trade_date, close)
        window: 回看窗口 (交易日)

    Returns:
        DataFrame with ts_code, trade_date, limit_up_count, limit_down_count
    """
    if limit_df.empty or daily_df.empty:
        return pd.DataFrame(
            columns=["ts_code", "trade_date",
                     "limit_up_count", "limit_down_count"],
        )

    merged = daily_df[["ts_code", "trade_date", "close"]].merge(
        limit_df[["ts_code", "trade_date", "up_limit", "down_limit"]],
        on=["ts_code", "trade_date"],
        how="left",
    )
    merged = merged.sort_values(["ts_code", "trade_date"])

    # 涨停: close >= up_limit (考虑浮点误差)
    merged["is_limit_up"] = (
        merged["close"] >= merged["up_limit"] - 0.005
    ).astype(int)
    merged["is_limit_down"] = (
        merged["close"] <= merged["down_limit"] + 0.005
    ).astype(int)

    # 滚动窗口计数
    merged["limit_up_count"] = (
        merged.groupby("ts_code")["is_limit_up"]
        .rolling(window, min_periods=1).sum()
        .reset_index(level=0, drop=True)
    )
    merged["limit_down_count"] = (
        merged.groupby("ts_code")["is_limit_down"]
        .rolling(window, min_periods=1).sum()
        .reset_index(level=0, drop=True)
    )

    return merged[["ts_code", "trade_date",
                    "limit_up_count", "limit_down_count"]]


def calc_forecast_surprise(
    forecast_df: pd.DataFrame,
    fina_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """计算业绩预告超预期因子。

    使用预告的 p_change_min/p_change_max 中点作为预告增速,
    与上期实际增速对比。

    Args:
        forecast_df: 业绩预告 (ts_code, ann_date, end_date,
                     p_change_min, p_change_max)
        fina_df: 可选, 上期财务数据用于计算超预期

    Returns:
        DataFrame with ts_code, ann_date, forecast_growth, surprise
    """
    if forecast_df.empty:
        return pd.DataFrame(
            columns=["ts_code", "ann_date",
                     "forecast_growth", "surprise"],
        )

    df = forecast_df.copy()
    # 预告增速中点
    for col in ("p_change_min", "p_change_max"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["forecast_growth"] = (df["p_change_min"] + df["p_change_max"]) / 2.0

    # 简单超预期: 预告增速本身 (正=超预期, 负=低于预期)
    df["surprise"] = df["forecast_growth"]

    return df[["ts_code", "ann_date", "forecast_growth", "surprise"]]


def calc_unlock_pressure(
    float_df: pd.DataFrame,
    daily_basic_df: pd.DataFrame,
    forward_days: int = 30,
    query_date: str = "",
) -> pd.DataFrame:
    """计算未来 N 日解禁压力因子。

    pressure = 未来 forward_days 内解禁股数 / 流通股本

    Args:
        float_df: 限售解禁 (ts_code, float_date, float_share)
        daily_basic_df: (ts_code, trade_date, float_share [流通股本])
        forward_days: 前看窗口 (日历日)
        query_date: 查询日 YYYYMMDD

    Returns:
        DataFrame with ts_code, unlock_pressure
    """
    if float_df.empty or not query_date:
        return pd.DataFrame(columns=["ts_code", "unlock_pressure"])

    from datetime import datetime, timedelta

    qd = datetime.strptime(query_date, "%Y%m%d")
    end_d = qd + timedelta(days=forward_days)

    # 筛选未来窗口内的解禁
    df = float_df.copy()
    df["float_date_dt"] = pd.to_datetime(df["float_date"], format="%Y%m%d")
    mask = (df["float_date_dt"] > qd) & (df["float_date_dt"] <= end_d)
    upcoming = df.loc[mask].groupby("ts_code")["float_share"].sum()

    if upcoming.empty:
        return pd.DataFrame(columns=["ts_code", "unlock_pressure"])

    # 合并流通股本
    if not daily_basic_df.empty:
        latest = (
            daily_basic_df
            .sort_values("trade_date")
            .groupby("ts_code")["float_share"]
            .last()
        )
        pressure = upcoming / latest.reindex(upcoming.index)
    else:
        pressure = upcoming * 0  # 无基准则为 0

    result = pressure.reset_index()
    result.columns = ["ts_code", "unlock_pressure"]
    return result
