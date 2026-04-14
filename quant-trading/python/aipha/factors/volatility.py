"""波动率因子"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aipha.factors.base import Factor as FactorBase


class VolatilityFactor(FactorBase):
    """N日收益率标准差"""

    name = "realized_vol"
    category = "volatility"

    def __init__(self, window: int = 20) -> None:
        self.window = window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].pct_change().rolling(self.window).std() * np.sqrt(252)


class DownsideVolFactor(FactorBase):
    """下行波动率"""

    name = "downside_vol"
    category = "volatility"

    def __init__(self, window: int = 60) -> None:
        self.window = window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        ret = df["close"].pct_change()
        neg = ret.clip(upper=0)
        return neg.rolling(self.window).std() * np.sqrt(252)


class IdioVolFactor(FactorBase):
    """特质波动率 (简化版: 回归残差波动)

    完整版需要 Fama-French 因子，此处用 rolling std of residual vs market.
    """

    name = "idio_vol"
    category = "volatility"

    def __init__(self, window: int = 60) -> None:
        self.window = window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        ret = df["close"].pct_change()
        if "market_ret" in df.columns:
            residual = ret - df["market_ret"]
        else:
            residual = ret - ret.rolling(self.window).mean()
        return residual.rolling(self.window).std() * np.sqrt(252)


class RangeVolFactor(FactorBase):
    """Parkinson 波动率 (基于 High-Low)"""

    name = "range_vol"
    category = "volatility"

    def __init__(self, window: int = 20) -> None:
        self.window = window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        log_hl = np.log(df["high"] / df["low"].replace(0, np.nan))
        return log_hl.rolling(self.window).mean() / (2.0 * np.sqrt(np.log(2)))
