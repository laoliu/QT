"""价值因子 — PE / PB / EP / BP / 股息率等"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aipha.factors.base import Factor as FactorBase


class PEFactor(FactorBase):
    """市盈率倒数 (EP = 1/PE)"""

    name = "ep"
    category = "value"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "pe_ttm" in df.columns:
            return 1.0 / df["pe_ttm"].replace(0, np.nan)
        if "eps_ttm" in df.columns and "close" in df.columns:
            return df["eps_ttm"] / df["close"].replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)


class PBFactor(FactorBase):
    """市净率倒数 (BP = 1/PB)"""

    name = "bp"
    category = "value"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "pb" in df.columns:
            return 1.0 / df["pb"].replace(0, np.nan)
        if "bps" in df.columns and "close" in df.columns:
            return df["bps"] / df["close"].replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)


class EPFactor(FactorBase):
    """Earnings-to-Price"""

    name = "earnings_price"
    category = "value"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "eps_ttm" in df.columns:
            return df["eps_ttm"] / df["close"].replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)


class CFPFactor(FactorBase):
    """现金流收益率: operating_cashflow / market_cap"""

    name = "cashflow_price"
    category = "value"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "ocf_ttm" in df.columns and "total_mv" in df.columns:
            return df["ocf_ttm"] / df["total_mv"].replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)


class EVEBITDAFactor(FactorBase):
    """EV/EBITDA 倒数"""

    name = "ebitda_ev"
    category = "value"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "ev" in df.columns and "ebitda" in df.columns:
            return df["ebitda"] / df["ev"].replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)
