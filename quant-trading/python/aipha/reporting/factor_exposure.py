"""因子暴露报告 — 计算组合在各风格因子上的暴露"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class ExposureSnapshot:
    exposures: pd.Series        # 各因子暴露值
    active_exposures: pd.Series  # 相对基准的主动暴露
    factor_returns: pd.Series   # 当期因子收益
    attribution: pd.Series      # 各因子贡献


class FactorExposureReport:
    """组合因子暴露分析。

    Parameters
    ----------
    weights : Series
        股票权重 (index=symbol)
    factor_data : DataFrame
        index=symbol, columns=factor_names, values=因子值
    benchmark_weights : Series, optional
        基准权重
    """

    def __init__(
        self,
        weights: pd.Series,
        factor_data: pd.DataFrame,
        benchmark_weights: pd.Series | None = None,
    ) -> None:
        self.weights = weights
        self.factor_data = factor_data
        self.bm_weights = benchmark_weights

    def compute_exposure(self) -> pd.Series:
        """加权因子暴露 = sum(w_i * f_i)"""
        aligned = self.factor_data.loc[self.weights.index].fillna(0)
        return aligned.T.dot(self.weights)

    def compute_active_exposure(self) -> pd.Series:
        """主动暴露 = 组合暴露 - 基准暴露"""
        port_exp = self.compute_exposure()
        if self.bm_weights is None:
            return port_exp
        bm_aligned = self.factor_data.loc[self.bm_weights.index].fillna(0)
        bm_exp = bm_aligned.T.dot(self.bm_weights)
        return port_exp - bm_exp

    def snapshot(self, factor_returns: pd.Series | None = None) -> ExposureSnapshot:
        exp = self.compute_exposure()
        active = self.compute_active_exposure()
        fr = factor_returns if factor_returns is not None else pd.Series(0.0, index=exp.index)
        attribution = active * fr
        return ExposureSnapshot(
            exposures=exp,
            active_exposures=active,
            factor_returns=fr,
            attribution=attribution,
        )

    def time_series(
        self,
        weights_ts: dict[str, pd.Series],
        factor_data_ts: dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        """多期暴露追踪。

        Parameters
        ----------
        weights_ts : dict[date_str, weights Series]
        factor_data_ts : dict[date_str, factor DataFrame]

        Returns
        -------
        DataFrame: index=date, columns=factor_name
        """
        rows = {}
        for dt in sorted(weights_ts.keys()):
            w = weights_ts[dt]
            fd = factor_data_ts.get(dt)
            if fd is None:
                continue
            aligned = fd.loc[fd.index.intersection(w.index)].fillna(0)
            ww = w.loc[aligned.index]
            rows[dt] = aligned.T.dot(ww)
        return pd.DataFrame(rows).T
