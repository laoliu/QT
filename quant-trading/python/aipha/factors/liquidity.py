"""流动性因子"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aipha.factors.base import Factor as FactorBase


class AmihudIlliquidityFactor(FactorBase):
    """Amihud 非流动性: avg(|ret| / turnover_value)"""

    name = "amihud_illiq"
    category = "liquidity"

    def __init__(self, window: int = 20) -> None:
        self.window = window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        ret = df["close"].pct_change().abs()
        if "amount" in df.columns:
            amt = df["amount"].replace(0, np.nan)
        else:
            amt = (df["close"] * df["volume"]).replace(0, np.nan)
        illiq = ret / amt
        return illiq.rolling(self.window).mean()


class TurnoverMomentumFactor(FactorBase):
    """换手率动量: 近期换手率 / 长期换手率"""

    name = "turnover_momentum"
    category = "liquidity"

    def __init__(self, short_window: int = 5, long_window: int = 60) -> None:
        self.short_window = short_window
        self.long_window = long_window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "turnover_rate" in df.columns:
            to = df["turnover_rate"]
        elif "volume" in df.columns and "float_share" in df.columns:
            to = df["volume"] / df["float_share"].replace(0, np.nan)
        else:
            return pd.Series(np.nan, index=df.index)
        short = to.rolling(self.short_window).mean()
        long_ = to.rolling(self.long_window).mean()
        return short / long_.replace(0, np.nan) - 1.0


class SpreadFactor(FactorBase):
    """买卖价差估计: (high - low) / ((high + low) / 2)"""

    name = "bid_ask_spread"
    category = "liquidity"

    def __init__(self, window: int = 20) -> None:
        self.window = window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        spread = (df["high"] - df["low"]) / ((df["high"] + df["low"]) / 2).replace(0, np.nan)
        return spread.rolling(self.window).mean()


class ZeroTradingDaysFactor(FactorBase):
    """零成交天数占比"""

    name = "zero_trading_days"
    category = "liquidity"

    def __init__(self, window: int = 60) -> None:
        self.window = window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        zero = (df["volume"] == 0).astype(float)
        return zero.rolling(self.window).mean()
