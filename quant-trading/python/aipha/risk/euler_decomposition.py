"""Euler 风险分解模块.

实现组合风险的 Euler 分解（Riskmetrics / Tasche 2008），确保：
    sum(RC_i) == total_risk   (齐次性)

支持的风险度量：
- 波动率 (volatility)
- VaR (参数法)
- CVaR / Expected Shortfall

理论基础:
    RC_i = w_i * ∂ρ/∂w_i
    对于波动率: RC_i = w_i * (Σw)_i / σ_p

参考:
    - §11.2 理论约束: 风险分解必须使用 Euler 分解
    - Artzner et al. (1999), Tasche (2008)

用法:
    from aipha.risk.euler_decomposition import EulerDecomposer
    decomposer = EulerDecomposer()
    result = decomposer.decompose(weights, cov_matrix, asset_names)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EulerDecomposition:
    """Euler 风险分解结果.

    Attributes:
        total_risk: 组合总风险
        risk_contributions: 各资产绝对风险贡献 {name: RC_i}
        pct_contributions: 各资产百分比风险贡献 {name: RC_i / total}
        marginal_risk: 各资产边际风险 {name: ∂ρ/∂w_i}
        risk_measure: 使用的风险度量名称
    """
    total_risk: float
    risk_contributions: dict[str, float]
    pct_contributions: dict[str, float]
    marginal_risk: dict[str, float]
    risk_measure: str

    def max_contribution(self) -> tuple[str, float]:
        """返回风险贡献最大的资产."""
        if not self.pct_contributions:
            return ("", 0.0)
        name = max(self.pct_contributions, key=self.pct_contributions.get)
        return (name, self.pct_contributions[name])

    def exceeds_limit(self, limit: float = 0.50) -> list[str]:
        """返回风险贡献超过 limit 的资产列表 (原则E: 单策略 ≤50%)."""
        return [n for n, p in self.pct_contributions.items() if p > limit]

    def to_dict(self) -> dict:
        return {
            "total_risk": self.total_risk,
            "risk_contributions": self.risk_contributions,
            "pct_contributions": self.pct_contributions,
            "marginal_risk": self.marginal_risk,
            "risk_measure": self.risk_measure,
        }


class EulerDecomposer:
    """Euler 风险分解器.

    对协方差矩阵 Σ 和权重向量 w 执行以下计算:
        σ_p   = sqrt(w^T Σ w)           (组合波动率)
        MR_i  = (Σw)_i / σ_p            (边际风险)
        RC_i  = w_i * MR_i              (绝对风险贡献)
        %RC_i = RC_i / σ_p              (百分比风险贡献)

    齐次性保证: sum(RC_i) == σ_p
    """

    def decompose_volatility(
        self,
        weights: list[float],
        cov_matrix: list[list[float]],
        asset_names: list[str] | None = None,
    ) -> EulerDecomposition:
        """基于波动率的 Euler 分解.

        Args:
            weights: 权重向量 [w_1, ..., w_n]
            cov_matrix: n×n 协方差矩阵
            asset_names: 资产名称（可选）
        """
        n = len(weights)
        if asset_names is None:
            asset_names = [f"asset_{i}" for i in range(n)]
        if len(cov_matrix) != n or any(len(row) != n for row in cov_matrix):
            raise ValueError(f"协方差矩阵维度 ({len(cov_matrix)}) 与权重维度 ({n}) 不匹配")

        # Σw 向量
        sigma_w = [
            sum(cov_matrix[i][j] * weights[j] for j in range(n))
            for i in range(n)
        ]

        # 组合方差和波动率
        port_var = sum(weights[i] * sigma_w[i] for i in range(n))
        port_vol = math.sqrt(max(port_var, 0.0))

        if port_vol < 1e-15:
            return EulerDecomposition(
                total_risk=0.0,
                risk_contributions={name: 0.0 for name in asset_names},
                pct_contributions={name: 0.0 for name in asset_names},
                marginal_risk={name: 0.0 for name in asset_names},
                risk_measure="volatility",
            )

        # 边际风险: MR_i = (Σw)_i / σ_p
        mr = {asset_names[i]: sigma_w[i] / port_vol for i in range(n)}

        # 绝对风险贡献: RC_i = w_i * MR_i
        rc = {asset_names[i]: weights[i] * sigma_w[i] / port_vol for i in range(n)}

        # 百分比贡献: %RC_i = RC_i / σ_p
        pct = {name: rc[name] / port_vol for name in asset_names}

        return EulerDecomposition(
            total_risk=port_vol,
            risk_contributions=rc,
            pct_contributions=pct,
            marginal_risk=mr,
            risk_measure="volatility",
        )

    def decompose_var(
        self,
        weights: list[float],
        cov_matrix: list[list[float]],
        confidence: float = 0.95,
        asset_names: list[str] | None = None,
    ) -> EulerDecomposition:
        """基于参数法 VaR 的 Euler 分解.

        Component VaR_i = w_i * (Σw)_i / σ_p * z_α * σ_p
                        = w_i * (Σw)_i / σ_p * VaR_p / σ_p * σ_p
        简化: CVaR_i = z_α * w_i * (Σw)_i / σ_p
        """
        vol_decomp = self.decompose_volatility(weights, cov_matrix, asset_names)
        if vol_decomp.total_risk < 1e-15:
            return EulerDecomposition(
                total_risk=0.0,
                risk_contributions=vol_decomp.risk_contributions,
                pct_contributions=vol_decomp.pct_contributions,
                marginal_risk=vol_decomp.marginal_risk,
                risk_measure=f"VaR({confidence:.0%})",
            )
        z = _norm_ppf(confidence)
        total_var = z * vol_decomp.total_risk
        names = list(vol_decomp.risk_contributions.keys())
        rc = {n: z * vol_decomp.risk_contributions[n] for n in names}
        mr = {n: z * vol_decomp.marginal_risk[n] for n in names}
        pct = vol_decomp.pct_contributions  # 百分比不变
        return EulerDecomposition(
            total_risk=total_var,
            risk_contributions=rc,
            pct_contributions=pct,
            marginal_risk=mr,
            risk_measure=f"VaR({confidence:.0%})",
        )

    def decompose_cvar(
        self,
        weights: list[float],
        cov_matrix: list[list[float]],
        confidence: float = 0.95,
        asset_names: list[str] | None = None,
    ) -> EulerDecomposition:
        """基于参数法 CVaR/ES 的 Euler 分解 (正态假设).

        CVaR_p = σ_p * φ(z_α) / (1-α)
        Component CVaR_i = w_i * (Σw)_i / σ_p * φ(z_α)/(1-α)
        """
        vol_decomp = self.decompose_volatility(weights, cov_matrix, asset_names)
        if vol_decomp.total_risk < 1e-15:
            return EulerDecomposition(
                total_risk=0.0,
                risk_contributions=vol_decomp.risk_contributions,
                pct_contributions=vol_decomp.pct_contributions,
                marginal_risk=vol_decomp.marginal_risk,
                risk_measure=f"CVaR({confidence:.0%})",
            )
        z = _norm_ppf(confidence)
        phi_z = _norm_pdf(z)
        alpha_tail = 1.0 - confidence
        scale = phi_z / alpha_tail if alpha_tail > 1e-10 else z
        total_cvar = scale * vol_decomp.total_risk
        names = list(vol_decomp.risk_contributions.keys())
        rc = {n: scale * vol_decomp.risk_contributions[n] for n in names}
        mr = {n: scale * vol_decomp.marginal_risk[n] for n in names}
        return EulerDecomposition(
            total_risk=total_cvar,
            risk_contributions=rc,
            pct_contributions=vol_decomp.pct_contributions,
            marginal_risk=mr,
            risk_measure=f"CVaR({confidence:.0%})",
        )


def _norm_ppf(p: float) -> float:
    """标准正态分布的分位点 (Beasley-Springer-Moro 近似)."""
    if p <= 0 or p >= 1:
        raise ValueError(f"p 必须在 (0, 1) 之间, 得到 {p}")
    if p < 0.5:
        return -_norm_ppf(1 - p)
    t = math.sqrt(-2 * math.log(1 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t ** 3)


def _norm_pdf(x: float) -> float:
    """标准正态分布概率密度."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)
