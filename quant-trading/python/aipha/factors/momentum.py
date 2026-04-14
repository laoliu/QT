"""动量因子 — 价格 / 收益率动量"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aipha.factors.base import Factor as FactorBase


class ReturnMomentumFactor(FactorBase):
    """N日收益率动量: close / close_lag - 1"""

    name = "return_momentum"
    category = "momentum"

    def __init__(self, lookback: int = 20, skip: int = 1) -> None:
        self.lookback = lookback
        self.skip = skip

    def compute(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        lagged = c.shift(self.lookback + self.skip)
        skipped = c.shift(self.skip)
        return (skipped / lagged) - 1.0


class VolAdjMomentumFactor(FactorBase):
    """波动调整动量: ret / std"""

    name = "vol_adj_momentum"
    category = "momentum"

    def __init__(self, lookback: int = 20, vol_window: int = 60) -> None:
        self.lookback = lookback
        self.vol_window = vol_window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        ret = c / c.shift(self.lookback) - 1.0
        vol = c.pct_change().rolling(self.vol_window).std()
        return ret / vol.replace(0, np.nan)


class ReversalFactor(FactorBase):
    """短期反转: -5日收益"""

    name = "short_reversal"
    category = "momentum"

    def __init__(self, lookback: int = 5) -> None:
        self.lookback = lookback

    def compute(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        return -(c / c.shift(self.lookback) - 1.0)


class IndustryRelMomentumFactor(FactorBase):
    """行业相对动量: 个股动量 - 行业均值 (需 industry 列)"""

    name = "industry_rel_momentum"
    category = "momentum"

    def __init__(self, lookback: int = 20) -> None:
        self.lookback = lookback

    def compute(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        ret = c / c.shift(self.lookback) - 1.0
        if "industry" in df.columns:
            ind_mean = df.groupby("industry")[ret.name].transform("mean") if ret.name else ret
            # fallback: directly use groupby
            ind_mean = ret.groupby(df["industry"]).transform("mean")
            return ret - ind_mean
        return ret
