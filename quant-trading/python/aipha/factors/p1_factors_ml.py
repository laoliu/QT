"""P0+P1 验证通过因子 — ML 训练管线兼容版本.

R125 因子 IC 验证通过的 6 个因子，为 LightGBM 训练管线提供 DataFrame 格式计算。
所有因子以面板数据 (datetime × symbol) 格式输出，与 Alpha158 / behavioral_ml 接口兼容。

因子清单 (R125 IC 验证通过):
  P0 因子:
    - amihud: Amihud 非流动性 (IC=8.57%, ICIR=1.213) ⭐⭐⭐
    - reversal_3m: 3个月反转 (IC=5.21%, ICIR=0.596) ⭐
    - value_prof: 价值+盈利组合 (IC=2.13%, ICIR=0.485) ⭐
  P1 因子:
    - ivol: 特异波动率 (IC=8.64%, ICIR=1.505) ⭐⭐⭐⭐⭐
    - skewness: 收益偏度 (IC=4.53%, ICIR=0.564) ⭐⭐
    - wk52_high_rev: 52周新高比率(反转) (IC=7.80%, ICIR=0.867) ⭐⭐⭐

偏相关分析 (R125): Amihud vs IVOL | log_mv = -0.039, IC保留率 83.2%,
两者捕获独立信息维度 → 同时纳入 ML 模型。
"""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "add_p1_factors",
    "P1_FACTOR_NAMES",
]

P1_FACTOR_NAMES: list[str] = [
    "amihud", "cs_rank_amihud",
    "reversal_3m", "cs_rank_reversal_3m",
    "value_prof", "cs_rank_value_prof",
    "ivol", "cs_rank_ivol",
    "skewness", "cs_rank_skewness",
    "wk52_high_rev", "cs_rank_wk52_high_rev",
]


def add_p1_factors(df: pd.DataFrame) -> int:
    """向面板 DataFrame 添加 6 个 P0+P1 验证通过因子。

    Args:
        df: 面板数据，需含 datetime/symbol/close/open/high/volume/amount 列。
            **就地修改** (in-place)。

    Returns:
        新增因子列数。
    """
    added = 0
    added += _add_amihud(df)
    added += _add_reversal_3m(df)
    added += _add_value_prof(df)
    added += _add_ivol(df)
    added += _add_skewness(df)
    added += _add_wk52_high_rev(df)
    logger.info("P1 因子: 新增 %d 列", added)
    return added


def _add_amihud(df: pd.DataFrame) -> int:
    """Amihud ILLIQ = mean(|ret|/amount, 20d). 高→低流动性→高预期收益."""
    if "amount" not in df.columns:
        logger.warning("Amihud: 无 amount 列，跳过")
        return 0
    daily_ret = df.groupby("symbol")["close"].transform(lambda s: s.pct_change())
    amt = df["amount"].replace(0, np.nan)
    ratio = daily_ret.abs() / amt
    illiq = ratio.groupby(df["symbol"]).transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    # clip 极端值 (>99.5%ile → winsorize)
    upper = illiq.quantile(0.995)
    df["amihud"] = illiq.clip(upper=upper)
    df["cs_rank_amihud"] = df.groupby("datetime")["amihud"].rank(pct=True)
    return 2


def _add_reversal_3m(df: pd.DataFrame) -> int:
    """3M Reversal = -R_{t-60:t-5}. 跳过近5日避免微观结构噪声."""
    g = df.groupby("symbol")
    r60 = g["close"].transform(lambda s: s / s.shift(60) - 1)
    r5 = g["close"].transform(lambda s: s / s.shift(5) - 1)
    mid_ret = (1 + r60) / (1 + r5) - 1
    df["reversal_3m"] = -mid_ret  # 反转: 负号
    df["cs_rank_reversal_3m"] = df.groupby("datetime")["reversal_3m"].rank(pct=True)
    return 2


def _add_value_prof(df: pd.DataFrame) -> int:
    """Value+Profitability 6-metric composite. 截面排名后等权."""
    metrics: dict[str, pd.Series] = {}
    if "grossprofit_margin" in df.columns:
        metrics["gp_margin"] = df["grossprofit_margin"]
    if "netprofit_margin" in df.columns:
        metrics["np_margin"] = df["netprofit_margin"]
    if "roe" in df.columns:
        metrics["roe"] = df["roe"]
    if "pe_ttm" in df.columns:
        pe = df["pe_ttm"].replace(0, np.nan)
        metrics["ep"] = 1.0 / pe
    if "pb" in df.columns:
        pb = df["pb"].replace(0, np.nan)
        metrics["bp"] = 1.0 / pb
    if "eps" in df.columns and "close" in df.columns:
        close = df["close"].replace(0, np.nan)
        metrics["eps_price"] = df["eps"] / close
    if not metrics:
        logger.warning("Value+Prof: 无可用指标，跳过")
        return 0
    ranks = pd.DataFrame(index=df.index)
    for name, vals in metrics.items():
        ranks[name] = vals.groupby(df["datetime"]).rank(pct=True)
    df["value_prof"] = ranks.mean(axis=1)
    df["cs_rank_value_prof"] = df.groupby("datetime")["value_prof"].rank(pct=True)
    return 2


def _add_ivol(df: pd.DataFrame) -> int:
    """IVOL = -std(residual, 20d). 低特异波动率→高未来收益."""
    ret = df.groupby("symbol")["close"].transform(lambda s: s.pct_change())
    mkt_ret = ret.groupby(df["datetime"]).transform("mean")
    resid = ret - mkt_ret  # 简化: beta≈1 近似
    ivol_raw = resid.groupby(df["symbol"]).transform(
        lambda s: s.rolling(20, min_periods=10).std()
    )
    df["ivol"] = -ivol_raw  # 低IVOL=好, 取负使高分=好
    df["cs_rank_ivol"] = df.groupby("datetime")["ivol"].rank(pct=True)
    return 2


def _add_skewness(df: pd.DataFrame) -> int:
    """Skewness = -skew(ret, 20d). 低偏度→高未来收益."""
    ret = df.groupby("symbol")["close"].transform(lambda s: s.pct_change())
    skew = ret.groupby(df["symbol"]).transform(
        lambda s: s.rolling(20, min_periods=15).skew()
    )
    df["skewness"] = -skew  # 低偏度=好, 取负
    df["cs_rank_skewness"] = df.groupby("datetime")["skewness"].rank(pct=True)
    return 2


def _add_wk52_high_rev(df: pd.DataFrame) -> int:
    """52WkHigh reversed = -(close/max(high,252d)). A股反转效应."""
    if "high" not in df.columns:
        logger.warning("52WkHigh: 无 high 列，跳过")
        return 0
    high_252 = df.groupby("symbol")["high"].transform(
        lambda s: s.rolling(252, min_periods=120).max()
    )
    ratio = df["close"] / high_252.replace(0, np.nan)
    # A股中接近52周新高负向预测 → 取负号 (IC验证已确认反转)
    df["wk52_high_rev"] = -ratio
    df["cs_rank_wk52_high_rev"] = df.groupby("datetime")["wk52_high_rev"].rank(pct=True)
    return 2
