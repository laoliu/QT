"""拥挤度监控 — 检测因子/策略拥挤度

指标:
  - 持仓集中度 (HHI)
  - 因子估值分位 (valuation quantile)
  - 收益相关性 (策略间)
  - 换手异常 (市场层面)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class CrowdingLevel(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


@dataclass
class CrowdingReport:
    factor_name: str
    hhi: float                      # 持仓集中度
    valuation_quantile: float       # 拥挤因子的估值分位
    return_correlation: float       # 收益相关性
    level: CrowdingLevel
    detail: dict


class CrowdingMonitor:
    """因子拥挤度监控器。"""

    def __init__(
        self,
        hhi_high: float = 0.10,
        hhi_extreme: float = 0.20,
        corr_high: float = 0.70,
    ) -> None:
        self.hhi_high = hhi_high
        self.hhi_extreme = hhi_extreme
        self.corr_high = corr_high

    def compute_hhi(self, weights: pd.Series) -> float:
        """Herfindahl-Hirschman Index: sum(w_i^2)"""
        w = weights / weights.sum() if weights.sum() > 0 else weights
        return float((w ** 2).sum())

    def compute_valuation_quantile(
        self, factor_values: pd.Series, historical_values: pd.DataFrame
    ) -> float:
        """当前因子均值在历史中的分位数。"""
        current_mean = factor_values.mean()
        hist_means = historical_values.mean(axis=1)
        return float((hist_means < current_mean).mean())

    def assess(
        self,
        factor_name: str,
        weights: pd.Series,
        factor_values: pd.Series | None = None,
        historical_values: pd.DataFrame | None = None,
        strategy_returns: pd.DataFrame | None = None,
    ) -> CrowdingReport:
        hhi = self.compute_hhi(weights)

        val_q = 0.5
        if factor_values is not None and historical_values is not None:
            val_q = self.compute_valuation_quantile(factor_values, historical_values)

        ret_corr = 0.0
        if strategy_returns is not None and strategy_returns.shape[1] >= 2:
            corr_matrix = strategy_returns.corr()
            mask = np.triu(np.ones(corr_matrix.shape, dtype=bool), k=1)
            ret_corr = float(corr_matrix.values[mask].mean())

        # 分级
        if hhi >= self.hhi_extreme or ret_corr >= self.corr_high:
            level = CrowdingLevel.EXTREME
        elif hhi >= self.hhi_high or val_q >= 0.90:
            level = CrowdingLevel.HIGH
        elif val_q >= 0.75:
            level = CrowdingLevel.MODERATE
        else:
            level = CrowdingLevel.LOW

        return CrowdingReport(
            factor_name=factor_name,
            hhi=hhi,
            valuation_quantile=val_q,
            return_correlation=ret_corr,
            level=level,
            detail={"hhi": hhi, "val_percentile": val_q, "avg_corr": ret_corr},
        )
