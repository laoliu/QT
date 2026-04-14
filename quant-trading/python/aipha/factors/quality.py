"""质量因子 — ROE / ROA / 毛利率 / 应计 / 杜邦分解等"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aipha.factors.base import Factor as FactorBase


class PITAwareFactorMixin:
    """Point-in-time 适配: 使用 ann_date (公告日) 而非报告期"""

    @staticmethod
    def pit_shift(s: pd.Series, ann_dates: pd.Series | None = None) -> pd.Series:
        if ann_dates is not None:
            return s  # 已按公告日排列则无需处理
        return s.shift(1)  # 保守前移一期


class ROEFactor(PITAwareFactorMixin, FactorBase):
    """净资产收益率 TTM"""

    name = "roe_ttm"
    category = "quality"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "roe_ttm" in df.columns:
            return df["roe_ttm"]
        if "net_profit_ttm" in df.columns and "equity" in df.columns:
            return df["net_profit_ttm"] / df["equity"].replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)


class ROAFactor(PITAwareFactorMixin, FactorBase):
    """总资产收益率 TTM"""

    name = "roa_ttm"
    category = "quality"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "roa_ttm" in df.columns:
            return df["roa_ttm"]
        if "net_profit_ttm" in df.columns and "total_assets" in df.columns:
            return df["net_profit_ttm"] / df["total_assets"].replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)


class GrossMarginFactor(PITAwareFactorMixin, FactorBase):
    """毛利率"""

    name = "gross_margin"
    category = "quality"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "gross_margin" in df.columns:
            return df["gross_margin"]
        if "revenue" in df.columns and "cogs" in df.columns:
            return (df["revenue"] - df["cogs"]) / df["revenue"].replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)


class AccrualFactor(PITAwareFactorMixin, FactorBase):
    """应计项目 = (net_profit - operating_cashflow) / total_assets"""

    name = "accrual"
    category = "quality"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if all(c in df.columns for c in ["net_profit_ttm", "ocf_ttm", "total_assets"]):
            return (df["net_profit_ttm"] - df["ocf_ttm"]) / df["total_assets"].replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)


class AssetTurnoverFactor(PITAwareFactorMixin, FactorBase):
    """总资产周转率"""

    name = "asset_turnover"
    category = "quality"

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if "revenue" in df.columns and "total_assets" in df.columns:
            return df["revenue"] / df["total_assets"].replace(0, np.nan)
        return pd.Series(np.nan, index=df.index)
