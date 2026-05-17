"""质押因子 — 基于股权质押统计 (pledge_stat).

Phase C / C-2d: A 股基本面因子.

经济学解释 (原则 A):
    高质押比例 + 股价下跌 → 接近平仓线 → 强制平仓风险.
    质押比例变化反映大股东融资需求和杠杆水平.
    学术支持: 质押风险溢价 (Li, Liao 2021), 中国 A 股特有因子.

因子列表:
    - pledge_ratio: 质押比例 (原始值)
    - pledge_ratio_change: 质押比例变化 (环比)
    - pledge_risk_score: 质押风险评分 (高质押=高风险, 取反用于选股)

数据源: TuSharePledgeProvider.load_pledge_stat()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PledgeFactorRow:
    """单只股票单期质押因子."""
    ts_code: str
    end_date: str
    pledge_ratio: float = 0.0
    pledge_ratio_change: Optional[float] = None
    pledge_risk_score: float = 0.0


def compute_pledge_factors(
    pledge_df: pd.DataFrame,
) -> pd.DataFrame:
    """计算单只股票的质押因子时间序列.

    Args:
        pledge_df: pledge_stat 原始数据
            必须包含 [ts_code, end_date, pledge_ratio].
            按 end_date 升序排列.

    Returns:
        DataFrame 增加因子列:
            pledge_ratio_change, pledge_risk_score.
    """
    if pledge_df.empty:
        return pledge_df
    df = pledge_df.copy()
    df = df.sort_values("end_date").reset_index(drop=True)
    # 质押比例变化
    df["pledge_ratio_change"] = df["pledge_ratio"].diff()
    # 质押风险评分: 质押比例越高, 风险越大
    # 归一化到 [0, 1], 50% 以上高风险
    max_ratio = df["pledge_ratio"].max()
    if max_ratio and max_ratio > 0:
        df["pledge_risk_score"] = df["pledge_ratio"] / 100.0
    else:
        df["pledge_risk_score"] = 0.0
    return df


def build_pledge_factors_panel(
    pledge_panel: pd.DataFrame,
) -> pd.DataFrame:
    """批量计算多只股票的质押因子.

    Args:
        pledge_panel: 多只股票的 pledge_stat 合并数据.

    Returns:
        全市场因子面板 DataFrame.
    """
    if pledge_panel.empty:
        return pledge_panel
    required = {"ts_code", "end_date", "pledge_ratio"}
    missing = required - set(pledge_panel.columns)
    if missing:
        raise ValueError(f"缺少必需列: {missing}")
    frames: list[pd.DataFrame] = []
    for ts_code, group in pledge_panel.groupby("ts_code"):
        factors = compute_pledge_factors(group)
        frames.append(factors)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
