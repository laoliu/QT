"""Barra 风格因子暴露监控"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

STYLE_FACTORS = ["SIZE", "BM", "MOM", "VOL", "LIQUIDITY"]


def _zscore(series: pd.Series) -> pd.Series:
    """MAD-robust z-score, clipped ±3"""
    median = series.median()
    mad = (series - median).abs().median()
    if mad < 1e-10:
        return pd.Series(0.0, index=series.index)
    z = (series - median) / (1.4826 * mad)
    return z.clip(-3, 3)


@dataclass
class StyleExposure:
    trade_date: date
    exposures: dict[str, float]
    n_holdings: int = 0
    total_weight: float = 0.0


@dataclass
class ExposureAlert:
    factor: str
    exposure: float
    threshold: float
    direction: str  # "high" | "low"


@dataclass
class StyleMonitorConfig:
    alert_thresholds: dict[str, float] = field(default_factory=lambda: {
        "SIZE": 1.0, "BM": 1.0, "MOM": 1.0, "VOL": 1.0, "LIQUIDITY": 1.0,
    })


class BarraStyleMonitor:
    """Barra 风格因子暴露监控

    计算持仓组合在 SIZE/BM/MOM/VOL/LIQUIDITY 上的因子暴露,
    超阈值时告警。
    """

    def __init__(self, config: StyleMonitorConfig | None = None):
        self._config = config or StyleMonitorConfig()

    def compute_style_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化因子得分

        df 应包含: symbol, circ_mv, pb, mom_20d, volatility_20d, turnover_rate
        """
        result = df.copy()
        mapping = {
            "SIZE": "circ_mv",
            "BM": "pb",
            "MOM": "mom_20d",
            "VOL": "volatility_20d",
            "LIQUIDITY": "turnover_rate",
        }
        for factor, col in mapping.items():
            if col in result.columns:
                vals = result[col].astype(float)
                if factor == "SIZE" and (vals > 0).all():
                    vals = np.log(vals)
                if factor == "BM":
                    vals = 1.0 / vals.replace(0, np.nan)
                result[factor] = _zscore(vals)
            else:
                result[factor] = 0.0
        return result

    def compute_portfolio_exposure(
        self,
        weights: dict[str, float],
        style_scores: pd.DataFrame,
        trade_date: date | None = None,
    ) -> StyleExposure:
        """计算组合风格暴露"""
        symbols = list(weights.keys())
        if "symbol" in style_scores.columns:
            scores = style_scores.set_index("symbol")
        else:
            scores = style_scores
        common = [s for s in symbols if s in scores.index]
        exposures: dict[str, float] = {}
        total_w = sum(weights[s] for s in common)
        for factor in STYLE_FACTORS:
            if factor not in scores.columns:
                exposures[factor] = 0.0
                continue
            exposure = sum(weights[s] * scores.loc[s, factor] for s in common)
            exposures[factor] = exposure / total_w if total_w > 0 else 0.0

        return StyleExposure(
            trade_date=trade_date or date.today(),
            exposures=exposures,
            n_holdings=len(common),
            total_weight=total_w,
        )

    def check_alerts(self, exposure: StyleExposure) -> list[ExposureAlert]:
        alerts: list[ExposureAlert] = []
        thresholds = self._config.alert_thresholds
        for factor, exp in exposure.exposures.items():
            thr = thresholds.get(factor, 1.0)
            if exp > thr:
                alerts.append(ExposureAlert(factor, exp, thr, "high"))
            elif exp < -thr:
                alerts.append(ExposureAlert(factor, exp, thr, "low"))
        return alerts
