"""股息因子"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aipha.factors.base import Factor as FactorBase


class DividendYieldFactor(FactorBase):
    """股息率 = dps / close"""

    name = "dividend_yield"
    category = "dividend"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "dv_ratio" in df.columns:
            return df["dv_ratio"]
        if "dps" in df.columns:
            return df["dps"] / df["close"].replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)


class DividendGrowthFactor(FactorBase):
    """股息增长率: dps / dps_lag - 1 (年度)"""

    name = "dividend_growth"
    category = "dividend"

    def __init__(self, lag: int = 252) -> None:
        self.lag = lag

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "dps" in df.columns:
            lagged = df["dps"].shift(self.lag)
            return df["dps"] / lagged.replace(0, np.nan) - 1.0
        return pd.Series(np.nan, index=df.index)


class PayoutRatioFactor(FactorBase):
    """分红率: dividends / earnings"""

    name = "payout_ratio"
    category = "dividend"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "dps" in df.columns and "eps_ttm" in df.columns:
            return df["dps"] / df["eps_ttm"].replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)


class DividendStabilityFactor(FactorBase):
    """股息稳定性: 连续分红年数 (简化: dps > 0 rolling sum)"""

    name = "dividend_stability"
    category = "dividend"

    def __init__(self, window: int = 5 * 252) -> None:
        self.window = window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "dps" in df.columns:
            positive = (df["dps"] > 0).astype(float)
            return positive.rolling(self.window, min_periods=1).sum()
        return pd.Series(np.nan, index=df.index)
