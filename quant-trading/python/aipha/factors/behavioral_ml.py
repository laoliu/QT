"""行为偏差因子 — ML 训练管线兼容版本。

为 LightGBM 训练管线提供 DataFrame 格式的行为因子计算。
所有因子以面板数据 (datetime × symbol) 格式输出，与 Alpha158 接口兼容。

因子清单 (R120 IC 验证通过):
  - abt: 注意力偏差 (Abnormal Turnover) — IC=0.081, ICIR=0.631
  - max_ret: 彩票效应 (MAX Daily Return) — IC=0.086, ICIR=0.580
  - overreact: 过度反应组合 (Overreaction Composite) — IC=0.091, ICIR=0.614

注意: str (短期反转) IC 不显著 (0.028)，不独立输出但被 overreact 内部使用。
"""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "add_behavioral_factors",
    "BEHAVIORAL_FACTOR_NAMES",
]

# 因子名称常量
BEHAVIORAL_FACTOR_NAMES: list[str] = [
    "abt",           # Abnormal Turnover (注意力偏差)
    "max_ret",       # MAX daily return (彩票效应)
    "overreact",     # Overreaction Composite (过度反应组合)
    "cs_rank_abt",   # 截面排名
    "cs_rank_max_ret",
    "cs_rank_overreact",
]


def add_behavioral_factors(
    df: pd.DataFrame,
    abt_window: int = 20,
    abt_baseline: int = 60,
    max_window: int = 20,
    str_window: int = 5,
) -> int:
    """向面板 DataFrame 添加行为偏差因子列。

    Args:
        df: 面板数据，需含 datetime/symbol/close/volume/turnover_rate 列。
            **就地修改** (in-place)。
        abt_window: ABT 近期窗口 (默认 20 日)。
        abt_baseline: ABT 基线窗口 (默认 60 日)。
        max_window: MAX 回看窗口 (默认 20 日)。
        str_window: STR 回看窗口 (默认 5 日)。

    Returns:
        新增因子列数。
    """
    added = 0

    added += _add_abt(df, abt_window, abt_baseline)
    added += _add_max_ret(df, max_window)
    added += _add_overreact(df, str_window)

    logger.info("行为偏差因子: 新增 %d 列", added)
    return added


def _add_abt(df: pd.DataFrame, window: int, baseline: int) -> int:
    """ABT: 异常换手率 = 近期均值 / 长期均值 - 1.

    使用 turnover_rate 字段（优先），fallback 到 volume/circ_mv 估算。
    高 ABT → 近期异常放量 → 注意力聚焦 → 未来负收益 (反向因子)。
    """
    added = 0
    g = df.groupby("symbol")

    # 选择流动性指标: turnover_rate > volume
    if "turnover_rate" in df.columns:
        liq = df["turnover_rate"]
    elif "volume" in df.columns:
        liq = df["volume"].astype(float)
    else:
        logger.warning("ABT: 无 turnover_rate/volume 列，跳过")
        return 0

    recent = g[liq.name].transform(
        lambda x: x.rolling(window, min_periods=max(1, window // 2)).mean()
    )
    base = g[liq.name].transform(
        lambda x: x.rolling(baseline, min_periods=max(1, baseline // 2)).mean()
    )
    # ABT = recent / baseline - 1 (注意力异常度)
    abt = (recent / base.replace(0, np.nan)) - 1.0
    df["abt"] = abt
    added += 1

    # 截面排名
    df["cs_rank_abt"] = df.groupby("datetime")["abt"].rank(pct=True)
    added += 1

    return added


def _add_max_ret(df: pd.DataFrame, window: int) -> int:
    """MAX: 过去N日最高日收益率.

    Bali, Cakici & Whitelaw (2011): 高 MAX 股票被散户偏好 → 长期负 alpha。
    高 MAX → 彩票属性强 → 未来负收益 (反向因子)。
    """
    if "close" not in df.columns:
        logger.warning("MAX: 无 close 列，跳过")
        return 0

    added = 0
    g = df.groupby("symbol")

    # 日收益率
    daily_ret = g["close"].pct_change()
    # 过去 window 天的最高日收益
    df["max_ret"] = g[daily_ret.name if hasattr(daily_ret, "name") and daily_ret.name else "close"].transform(
        lambda x: x  # placeholder
    )
    # 直接计算: rolling max of daily returns
    df["max_ret"] = daily_ret.groupby(df["symbol"]).transform(
        lambda x: x.rolling(window, min_periods=max(1, window // 2)).max()
    )
    added += 1

    # 截面排名
    df["cs_rank_max_ret"] = df.groupby("datetime")["max_ret"].rank(pct=True)
    added += 1

    return added


def _add_overreact(df: pd.DataFrame, str_window: int) -> int:
    """Overreaction Composite: rank(ABT) + rank(MAX) + rank(STR).

    De Bondt & Thaler (1985): 融合注意力/彩票/反转三个行为偏差维度。
    使用截面排名聚合避免量纲问题。
    """
    added = 0

    # STR (短期反转): 过去 N 日累计收益率的负值
    if "close" in df.columns:
        daily_ret = df.groupby("symbol")["close"].pct_change()
        str_val = daily_ret.groupby(df["symbol"]).transform(
            lambda x: x.rolling(str_window, min_periods=2).sum()
        )
        # STR 是反转因子: 近期涨的未来跌 → 取负值使"未来好"得分高
        df["_str_raw"] = -str_val
    else:
        df["_str_raw"] = np.nan

    # 截面排名 (rank within each datetime)
    r_abt = df.groupby("datetime")["abt"].rank(pct=True) if "abt" in df.columns else 0.0
    r_max = df.groupby("datetime")["max_ret"].rank(pct=True) if "max_ret" in df.columns else 0.0
    r_str = df.groupby("datetime")["_str_raw"].rank(pct=True)

    # 等权组合 (1/3 each)
    df["overreact"] = (r_abt + r_max + r_str) / 3.0
    added += 1

    # 截面排名
    df["cs_rank_overreact"] = df.groupby("datetime")["overreact"].rank(pct=True)
    added += 1

    # 清理临时列
    df.drop(columns=["_str_raw"], inplace=True, errors="ignore")

    return added
