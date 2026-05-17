"""P7 换手率增强因子 — ML 训练管线兼容版本.

来源: 石川《因子投资》— 换手率是 A 股被定价因子之一。
参考: docs/reports/directional_research/library_study_shichuan_kuanggong_20260503.md

因子清单:
  - turnover_zscore: 标准化换手率 (截面 z-score 的 20 日均值)
  - turnover_vol: 换手率波动率 (20 日滚动标准差)
  - turnover_mom: 换手率动量 (5 日均值 / 60 日均值 - 1)
  - turnover_ivol_adj: 异质波动率调整换手率 (turnover / ivol)

所有因子以面板数据 (datetime × symbol) 格式输出，含原始值 + 截面排名。
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "add_p7_turnover_factors",
    "P7_FACTOR_NAMES",
]

P7_FACTOR_NAMES: list[str] = [
    "turnover_zscore",
    "cs_rank_turnover_zscore",
    "turnover_vol",
    "cs_rank_turnover_vol",
    "turnover_mom",
    "cs_rank_turnover_mom",
    "turnover_ivol_adj",
    "cs_rank_turnover_ivol_adj",
]


def add_p7_turnover_factors(df: pd.DataFrame) -> int:
    """向面板 DataFrame 添加 P7 换手率增强因子。

    Args:
        df: 面板数据，需含 datetime/symbol/close/turnover_rate 列。
            **就地修改** (in-place)。

    Returns:
        新增因子列数。
    """
    if "turnover_rate" not in df.columns:
        logger.warning("P7: 无 turnover_rate 列，跳过全部换手率因子")
        return 0

    added = 0
    added += _add_turnover_zscore(df)
    added += _add_turnover_vol(df)
    added += _add_turnover_mom(df)
    added += _add_turnover_ivol_adj(df)
    logger.info("P7 换手率增强因子: 新增 %d 列", added)
    return added


def _add_turnover_zscore(df: pd.DataFrame) -> int:
    """标准化换手率: 截面 z-score 的 20 日滚动均值.

    石川: 换手率截面标准化后取时序均值，消除市场整体换手率水位差异。
    高标准化换手率 → 过度关注 → 预期收益低（负向因子）。
    """
    # Step 1: 截面 z-score（每日截面标准化）
    grp_dt = df.groupby("datetime")["turnover_rate"]
    cs_mean = grp_dt.transform("mean")
    cs_std = grp_dt.transform("std")
    cs_std = cs_std.replace(0, np.nan)
    zscore = (df["turnover_rate"] - cs_mean) / cs_std

    # Step 2: 时序滚动均值（20 日）
    df["turnover_zscore"] = zscore.groupby(df["symbol"]).transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    # Winsorize 极端值
    upper = df["turnover_zscore"].quantile(0.995)
    lower = df["turnover_zscore"].quantile(0.005)
    df["turnover_zscore"] = df["turnover_zscore"].clip(lower=lower, upper=upper)
    df["cs_rank_turnover_zscore"] = df.groupby("datetime")["turnover_zscore"].rank(
        pct=True
    )
    return 2


def _add_turnover_vol(df: pd.DataFrame) -> int:
    """换手率波动率: 20 日滚动标准差.

    高换手率波动 → 交易行为不稳定 → 通常伴随不确定性溢价。
    """
    df["turnover_vol"] = df.groupby("symbol")["turnover_rate"].transform(
        lambda s: s.rolling(20, min_periods=10).std()
    )
    upper = df["turnover_vol"].quantile(0.995)
    df["turnover_vol"] = df["turnover_vol"].clip(upper=upper)
    df["cs_rank_turnover_vol"] = df.groupby("datetime")["turnover_vol"].rank(pct=True)
    return 2


def _add_turnover_mom(df: pd.DataFrame) -> int:
    """换手率动量: MA5 / MA60 - 1.

    短期换手率相对长期基线的偏离。正值→近期交易活跃度异常升高。
    类似 ABT 但用比率形式，与 ABT 互补。
    """
    grp = df.groupby("symbol")["turnover_rate"]
    ma5 = grp.transform(lambda s: s.rolling(5, min_periods=3).mean())
    ma60 = grp.transform(lambda s: s.rolling(60, min_periods=30).mean())
    ma60_safe = ma60.replace(0, np.nan)
    df["turnover_mom"] = ma5 / ma60_safe - 1.0
    upper = df["turnover_mom"].quantile(0.995)
    lower = df["turnover_mom"].quantile(0.005)
    df["turnover_mom"] = df["turnover_mom"].clip(lower=lower, upper=upper)
    df["cs_rank_turnover_mom"] = df.groupby("datetime")["turnover_mom"].rank(pct=True)
    return 2


def _add_turnover_ivol_adj(df: pd.DataFrame) -> int:
    """异质波动率调整换手率: turnover / ivol.

    控制波动率后的换手率——剥离因价格大幅波动带来的被动换手，
    更纯粹地衡量投资者主动交易意愿。
    需要 close 列计算 ivol。
    """
    if "close" not in df.columns:
        logger.warning("P7: 无 close 列，跳过 turnover_ivol_adj")
        return 0

    # 计算 IVOL（20 日收益率标准差）
    daily_ret = df.groupby("symbol")["close"].transform(lambda s: s.pct_change())
    ivol = daily_ret.groupby(df["symbol"]).transform(
        lambda s: s.rolling(20, min_periods=10).std()
    )
    ivol_safe = ivol.replace(0, np.nan)

    ratio = df["turnover_rate"] / ivol_safe
    # 取 20 日均值平滑
    df["turnover_ivol_adj"] = ratio.groupby(df["symbol"]).transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    upper = df["turnover_ivol_adj"].quantile(0.995)
    df["turnover_ivol_adj"] = df["turnover_ivol_adj"].clip(upper=upper)
    df["cs_rank_turnover_ivol_adj"] = df.groupby("datetime")[
        "turnover_ivol_adj"
    ].rank(pct=True)
    return 2
