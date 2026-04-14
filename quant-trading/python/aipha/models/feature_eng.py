"""特征工程管线 — 标准化/中性化/Winsorize/排名缩放。"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def winsorize(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    """Winsorize 极端值。"""
    lo = series.quantile(lower)
    hi = series.quantile(upper)
    return series.clip(lo, hi)


def zscore_normalize(series: pd.Series) -> pd.Series:
    """Z-Score 标准化。"""
    mean = series.mean()
    std = series.std()
    if std == 0 or pd.isna(std):
        return pd.Series(0.0, index=series.index)
    return (series - mean) / std


def rank_normalize(series: pd.Series) -> pd.Series:
    """排名归一化到 [0, 1]。"""
    return series.rank(pct=True)


def mad_winsorize(series: pd.Series, n_mad: float = 5.0) -> pd.Series:
    """MAD (Median Absolute Deviation) Winsorize。"""
    median = series.median()
    mad = (series - median).abs().median()
    if mad == 0:
        return series
    lower = median - n_mad * mad
    upper = median + n_mad * mad
    return series.clip(lower, upper)


def neutralize(
    factor: pd.Series,
    industry: pd.Series,
    market_cap: Optional[pd.Series] = None,
) -> pd.Series:
    """因子中性化 — 去除行业和市值影响。

    通过 OLS 回归 factor ~ industry_dummies + log(market_cap) 的残差。
    """
    df = pd.DataFrame({"factor": factor})
    dummies = pd.get_dummies(industry, prefix="ind", drop_first=True)
    X = dummies.copy()
    if market_cap is not None:
        X["log_mcap"] = np.log(market_cap.clip(lower=1e6))
    X = X.fillna(0)

    common = factor.dropna().index.intersection(X.index)
    if len(common) < 10:
        return factor

    y = factor.loc[common].values
    X_val = X.loc[common].values
    X_val = np.column_stack([np.ones(len(common)), X_val])

    try:
        beta = np.linalg.lstsq(X_val, y, rcond=None)[0]
        residual = y - X_val @ beta
        return pd.Series(residual, index=common)
    except Exception:
        return factor


class FeaturePipeline:
    """特征工程管线 — 按顺序应用转换。"""

    def __init__(
        self,
        winsorize_method: str = "mad",
        normalize_method: str = "zscore",
        neutralize_industry: bool = False,
    ):
        self._winsorize = winsorize_method
        self._normalize = normalize_method
        self._neutralize = neutralize_industry

    def transform(
        self,
        df: pd.DataFrame,
        feature_cols: list[str],
        industry_col: Optional[str] = None,
        mcap_col: Optional[str] = None,
    ) -> pd.DataFrame:
        """转换特征列。"""
        result = df.copy()
        for col in feature_cols:
            if col not in result.columns:
                continue
            s = result[col].copy()

            # Winsorize
            if self._winsorize == "mad":
                s = mad_winsorize(s)
            elif self._winsorize == "percentile":
                s = winsorize(s)

            # 中性化
            if self._neutralize and industry_col and industry_col in result.columns:
                mcap = result[mcap_col] if mcap_col and mcap_col in result.columns else None
                s = neutralize(s, result[industry_col], mcap)

            # 标准化
            if self._normalize == "zscore":
                s = zscore_normalize(s)
            elif self._normalize == "rank":
                s = rank_normalize(s)

            result[col] = s
        return result
