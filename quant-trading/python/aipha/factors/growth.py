"""成长因子"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aipha.factors.base import Factor as FactorBase


class RevenueGrowthFactor(FactorBase):
    """营收同比增长率"""

    name = "revenue_growth_yoy"
    category = "growth"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "revenue_yoy" in df.columns:
            return df["revenue_yoy"]
        if "revenue" in df.columns:
            lag = df["revenue"].shift(252)
            return df["revenue"] / lag.replace(0, np.nan) - 1.0
        return pd.Series(np.nan, index=df.index)


class EarningsGrowthFactor(FactorBase):
    """净利润同比增长率"""

    name = "earnings_growth_yoy"
    category = "growth"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "profit_yoy" in df.columns:
            return df["profit_yoy"]
        if "net_profit" in df.columns:
            lag = df["net_profit"].shift(252)
            return df["net_profit"] / lag.replace(0, np.nan) - 1.0
        return pd.Series(np.nan, index=df.index)


class SUEFactor(FactorBase):
    """标准化意外盈利: (eps_actual - eps_expected) / std(surprise)"""

    name = "sue"
    category = "growth"

    def __init__(self, window: int = 8 * 63) -> None:
        # ~8 quarters of daily data
        self.window = window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "eps_ttm" not in df.columns:
            return pd.Series(np.nan, index=df.index)
        eps = df["eps_ttm"]
        surprise = eps - eps.shift(252)
        std = surprise.rolling(self.window, min_periods=63).std()
        return surprise / std.replace(0, np.nan)


class ROEChangeFactor(FactorBase):
    """ROE 环比变化"""

    name = "roe_change"
    category = "growth"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "roe_ttm" in df.columns:
            return df["roe_ttm"] - df["roe_ttm"].shift(63)
        return pd.Series(np.nan, index=df.index)


class SalesAccelerationFactor(FactorBase):
    """营收加速度: yoy_growth - yoy_growth_lag"""

    name = "sales_acceleration"
    category = "growth"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "revenue_yoy" in df.columns:
            return df["revenue_yoy"] - df["revenue_yoy"].shift(63)
        return pd.Series(np.nan, index=df.index)
