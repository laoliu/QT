"""市值因子"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aipha.factors.base import Factor as FactorBase


class MarketCapFactor(FactorBase):
    """对数总市值"""

    name = "ln_market_cap"
    category = "size"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "total_mv" in df.columns:
            return np.log(df["total_mv"].replace(0, np.nan))
        if "close" in df.columns and "total_share" in df.columns:
            return np.log((df["close"] * df["total_share"]).replace(0, np.nan))
        return pd.Series(np.nan, index=df.index)


class FloatCapFactor(FactorBase):
    """对数流通市值"""

    name = "ln_float_cap"
    category = "size"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "circ_mv" in df.columns:
            return np.log(df["circ_mv"].replace(0, np.nan))
        if "close" in df.columns and "float_share" in df.columns:
            return np.log((df["close"] * df["float_share"]).replace(0, np.nan))
        return pd.Series(np.nan, index=df.index)


class NonLinearSizeFactor(FactorBase):
    """非线性市值: 正交化后的 ln(mktcap)^3"""

    name = "nlsize"
    category = "size"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "total_mv" in df.columns:
            lnmc = np.log(df["total_mv"].replace(0, np.nan))
        elif "close" in df.columns and "total_share" in df.columns:
            lnmc = np.log((df["close"] * df["total_share"]).replace(0, np.nan))
        else:
            return pd.Series(np.nan, index=df.index)
        cubed = lnmc ** 3
        # 简单正交化: residual = cubed - beta * lnmc
        mask = lnmc.notna() & cubed.notna()
        if mask.sum() < 2:
            return cubed
        beta = np.polyfit(lnmc[mask], cubed[mask], 1)[0]
        return cubed - beta * lnmc
