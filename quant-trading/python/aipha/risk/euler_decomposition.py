"""Euler 风险分解 — 组合风险贡献归因"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class RiskDecomposition:
    """风险分解结果"""
    risk_type: str                   # volatility / VaR / CVaR
    total_risk: float                # 组合总风险
    marginal: pd.Series              # 边际风险贡献
    component: pd.Series             # 成分风险贡献 (= weight * marginal)
    pct_contribution: pd.Series      # 百分比贡献

    def top_contributors(self, n: int = 5) -> pd.Series:
        return self.pct_contribution.nlargest(n)

    def to_dict(self) -> dict:
        return {
            "risk_type": self.risk_type,
            "total_risk": round(self.total_risk, 6),
            "contributions": self.pct_contribution.round(4).to_dict(),
        }


def _covariance_matrix(returns: pd.DataFrame, method: str = "sample") -> np.ndarray:
    """计算协方差矩阵"""
    if method == "ewm":
        cov = returns.ewm(halflife=63).cov().iloc[-len(returns.columns):]
        return cov.values
    return returns.cov().values


class EulerDecomposer:
    """基于 Euler 定理的风险分解器。

    支持三种风险度量：
    - volatility: σ_p = sqrt(w' Σ w)
    - VaR: 参数法 VaR
    - CVaR: 历史模拟法 CVaR
    """

    def __init__(
        self,
        returns: pd.DataFrame,
        cov_method: str = "sample",
    ) -> None:
        self._returns = returns
        self._assets = list(returns.columns)
        self._cov = _covariance_matrix(returns, cov_method)

    def decompose_volatility(self, weights: np.ndarray | list[float]) -> RiskDecomposition:
        """波动率 Euler 分解: MCR_i = (Σw)_i / σ_p"""
        w = np.asarray(weights, dtype=np.float64)
        sigma_w = self._cov @ w
        port_vol = float(np.sqrt(w @ sigma_w))
        if port_vol < 1e-12:
            n = len(w)
            return RiskDecomposition(
                risk_type="volatility",
                total_risk=0.0,
                marginal=pd.Series(np.zeros(n), index=self._assets),
                component=pd.Series(np.zeros(n), index=self._assets),
                pct_contribution=pd.Series(np.zeros(n), index=self._assets),
            )
        marginal = sigma_w / port_vol
        component = w * marginal
        pct = component / port_vol
        return RiskDecomposition(
            risk_type="volatility",
            total_risk=port_vol,
            marginal=pd.Series(marginal, index=self._assets),
            component=pd.Series(component, index=self._assets),
            pct_contribution=pd.Series(pct, index=self._assets),
        )

    def decompose_var(
        self,
        weights: np.ndarray | list[float],
        confidence: float = 0.95,
    ) -> RiskDecomposition:
        """参数法 VaR 分解"""
        from scipy.stats import norm  # type: ignore

        w = np.asarray(weights, dtype=np.float64)
        port_ret = self._returns.values @ w
        mu = float(np.mean(port_ret))
        sigma = float(np.std(port_ret, ddof=1))
        z = norm.ppf(1 - confidence)
        port_var = -(mu + z * sigma)

        # 边际VaR ≈ -(mu_i + z * (Σw)_i / σ_p)
        sigma_w = self._cov @ w
        port_vol = float(np.sqrt(w @ sigma_w))
        asset_mu = self._returns.mean().values
        if port_vol < 1e-12:
            n = len(w)
            return RiskDecomposition(
                risk_type="VaR",
                total_risk=port_var,
                marginal=pd.Series(np.zeros(n), index=self._assets),
                component=pd.Series(np.zeros(n), index=self._assets),
                pct_contribution=pd.Series(np.zeros(n), index=self._assets),
            )
        marginal = -(asset_mu + z * sigma_w / port_vol)
        component = w * marginal
        pct = component / port_var if abs(port_var) > 1e-12 else component * 0
        return RiskDecomposition(
            risk_type="VaR",
            total_risk=port_var,
            marginal=pd.Series(marginal, index=self._assets),
            component=pd.Series(component, index=self._assets),
            pct_contribution=pd.Series(pct, index=self._assets),
        )

    def decompose_cvar(
        self,
        weights: np.ndarray | list[float],
        confidence: float = 0.95,
    ) -> RiskDecomposition:
        """历史模拟法 CVaR 分解 (component方法)"""
        w = np.asarray(weights, dtype=np.float64)
        port_ret = self._returns.values @ w
        var_threshold = np.percentile(port_ret, (1 - confidence) * 100)
        tail_mask = port_ret <= var_threshold
        if tail_mask.sum() == 0:
            n = len(w)
            return RiskDecomposition(
                risk_type="CVaR",
                total_risk=0.0,
                marginal=pd.Series(np.zeros(n), index=self._assets),
                component=pd.Series(np.zeros(n), index=self._assets),
                pct_contribution=pd.Series(np.zeros(n), index=self._assets),
            )

        tail_returns = self._returns.values[tail_mask]  # (K, N)
        port_cvar = -float(np.mean(port_ret[tail_mask]))

        # 边际 CVaR_i = -E[r_i | r_p <= VaR]
        marginal = -tail_returns.mean(axis=0)
        component = w * marginal
        pct = component / port_cvar if abs(port_cvar) > 1e-12 else component * 0
        return RiskDecomposition(
            risk_type="CVaR",
            total_risk=port_cvar,
            marginal=pd.Series(marginal, index=self._assets),
            component=pd.Series(component, index=self._assets),
            pct_contribution=pd.Series(pct, index=self._assets),
        )

    def decompose(
        self,
        weights: np.ndarray | list[float],
        risk_type: Literal["volatility", "VaR", "CVaR"] = "volatility",
        confidence: float = 0.95,
    ) -> RiskDecomposition:
        """统一入口"""
        if risk_type == "volatility":
            return self.decompose_volatility(weights)
        elif risk_type == "VaR":
            return self.decompose_var(weights, confidence)
        elif risk_type == "CVaR":
            return self.decompose_cvar(weights, confidence)
        else:
            raise ValueError(f"Unknown risk_type: {risk_type}")
