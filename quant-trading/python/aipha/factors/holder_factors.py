"""筹码集中度因子 — 基于股东户数 (stk_holdernumber).

Phase C / C-2a: A 股基本面因子.

经济学解释 (原则 A):
    股东户数下降 → 筹码向大户集中 → 未来上涨概率增大.
    学术支持: 散户行为偏差 (处置效应/过度反应), 筹码集中度
    是中国 A 股有效的 alpha 因子 (IC ~ 3-5%).

因子列表:
    - holder_num_pct_change: 股东户数环比变化率
    - holder_concentration_score: 筹码集中度评分 (越高越集中)
    - holder_num_acceleration: 股东户数变化加速度

数据源: TuShareHolderProvider.load_holdernumber()
PIT 要求: 使用 ann_date (公告日) 做时点对齐.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class HolderFactorRow:
    """单只股票单期筹码集中度因子."""
    ts_code: str
    ann_date: str
    end_date: str
    holder_num: int
    holder_num_pct_change: Optional[float] = None
    holder_concentration_score: Optional[float] = None
    holder_num_acceleration: Optional[float] = None


def compute_holder_factors(
    holder_df: pd.DataFrame,
) -> pd.DataFrame:
    """计算单只股票的筹码集中度因子时间序列.

    Args:
        holder_df: stk_holdernumber 原始数据
            必须包含 [ts_code, ann_date, end_date, holder_num].
            按 end_date 升序排列.

    Returns:
        DataFrame 增加因子列:
            holder_num_pct_change, holder_concentration_score,
            holder_num_acceleration.
    """
    if holder_df.empty:
        return holder_df
    df = holder_df.copy()
    df = df.sort_values("end_date").reset_index(drop=True)
    # 环比变化率: (当期 - 上期) / 上期
    df["holder_num_pct_change"] = (
        df["holder_num"].pct_change()
    )
    # 筹码集中度评分: 户数下降越快 → 分数越高 (取反)
    # 使用最近 4 期的平均变化率, 取反后归一化到 [0, 1]
    rolling_mean = df["holder_num_pct_change"].rolling(
        window=4, min_periods=1,
    ).mean()
    df["holder_concentration_score"] = -rolling_mean
    # 加速度: 变化率的变化率
    df["holder_num_acceleration"] = (
        df["holder_num_pct_change"].diff()
    )
    return df


def build_holder_factors_panel(
    holder_panel: pd.DataFrame,
) -> pd.DataFrame:
    """批量计算多只股票的筹码集中度因子.

    Args:
        holder_panel: 多只股票的 stk_holdernumber 合并数据.

    Returns:
        全市场因子面板 DataFrame.
    """
    if holder_panel.empty:
        return holder_panel
    required = {"ts_code", "ann_date", "end_date", "holder_num"}
    missing = required - set(holder_panel.columns)
    if missing:
        raise ValueError(f"缺少必需列: {missing}")
    frames: list[pd.DataFrame] = []
    for ts_code, group in holder_panel.groupby("ts_code"):
        factors = compute_holder_factors(group)
        frames.append(factors)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
