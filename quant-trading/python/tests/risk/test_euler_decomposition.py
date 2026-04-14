"""tests/risk/test_euler_decomposition.py — Euler 风险分解单元测试."""
from __future__ import annotations

import math
import pytest

from aipha.risk.euler_decomposition import (
    EulerDecomposer,
    EulerDecomposition,
    _norm_ppf,
    _norm_pdf,
)


@pytest.fixture
def decomposer():
    return EulerDecomposer()


@pytest.fixture
def two_asset_case():
    """两资产简单案例: 方差=0.04, 相关系数=0.5."""
    w = [0.6, 0.4]
    vol1, vol2, rho = 0.20, 0.15, 0.5
    cov = [
        [vol1**2, rho * vol1 * vol2],
        [rho * vol1 * vol2, vol2**2],
    ]
    return w, cov, ["A", "B"]


class TestVolatilityDecomposition:
    """波动率 Euler 分解测试."""

    def test_sum_rc_equals_total_risk(self, decomposer, two_asset_case):
        """齐次性: sum(RC_i) == σ_p."""
        w, cov, names = two_asset_case
        r = decomposer.decompose_volatility(w, cov, names)
        rc_sum = sum(r.risk_contributions.values())
        assert abs(rc_sum - r.total_risk) < 1e-10

    def test_pct_sums_to_one(self, decomposer, two_asset_case):
        """百分比贡献之和 == 1."""
        w, cov, names = two_asset_case
        r = decomposer.decompose_volatility(w, cov, names)
        pct_sum = sum(r.pct_contributions.values())
        assert abs(pct_sum - 1.0) < 1e-10

    def test_equal_weight_equal_vol_zero_corr(self, decomposer):
        """等权等波零相关 → 等风险贡献."""
        w = [0.5, 0.5]
        cov = [[0.04, 0.0], [0.0, 0.04]]
        r = decomposer.decompose_volatility(w, cov, ["X", "Y"])
        assert abs(r.pct_contributions["X"] - 0.5) < 1e-10
        assert abs(r.pct_contributions["Y"] - 0.5) < 1e-10

    def test_single_asset(self, decomposer):
        """单资产分解: RC == 总风险."""
        r = decomposer.decompose_volatility([1.0], [[0.04]], ["only"])
        assert abs(r.total_risk - 0.2) < 1e-10
        assert abs(r.risk_contributions["only"] - 0.2) < 1e-10

    def test_zero_weight_zero_contribution(self, decomposer):
        """零权重资产风险贡献为零."""
        w = [1.0, 0.0]
        cov = [[0.04, 0.01], [0.01, 0.09]]
        r = decomposer.decompose_volatility(w, cov, ["A", "B"])
        assert abs(r.risk_contributions["B"]) < 1e-10

    def test_dimension_mismatch_raises(self, decomposer):
        with pytest.raises(ValueError, match="不匹配"):
            decomposer.decompose_volatility([0.5, 0.5], [[0.04]], ["A", "B"])

    def test_zero_variance(self, decomposer):
        r = decomposer.decompose_volatility([1.0], [[0.0]], ["cash"])
        assert r.total_risk == 0.0


class TestVarDecomposition:
    def test_var_decomposition_homogeneity(self, decomposer, two_asset_case):
        w, cov, names = two_asset_case
        r = decomposer.decompose_var(w, cov, 0.95, names)
        rc_sum = sum(r.risk_contributions.values())
        assert abs(rc_sum - r.total_risk) < 1e-10
        assert "VaR" in r.risk_measure

    def test_var_larger_than_vol(self, decomposer, two_asset_case):
        w, cov, names = two_asset_case
        vol_r = decomposer.decompose_volatility(w, cov, names)
        var_r = decomposer.decompose_var(w, cov, 0.95, names)
        assert var_r.total_risk > vol_r.total_risk


class TestCvarDecomposition:
    def test_cvar_decomposition_homogeneity(self, decomposer, two_asset_case):
        w, cov, names = two_asset_case
        r = decomposer.decompose_cvar(w, cov, 0.95, names)
        rc_sum = sum(r.risk_contributions.values())
        assert abs(rc_sum - r.total_risk) < 1e-10

    def test_cvar_larger_than_var(self, decomposer, two_asset_case):
        w, cov, names = two_asset_case
        var_r = decomposer.decompose_var(w, cov, 0.95, names)
        cvar_r = decomposer.decompose_cvar(w, cov, 0.95, names)
        assert cvar_r.total_risk > var_r.total_risk


class TestExceedsLimit:
    def test_exceeds_limit(self):
        d = EulerDecomposition(
            total_risk=0.1,
            risk_contributions={"A": 0.06, "B": 0.04},
            pct_contributions={"A": 0.6, "B": 0.4},
            marginal_risk={"A": 0.1, "B": 0.08},
            risk_measure="volatility",
        )
        assert d.exceeds_limit(0.50) == ["A"]
        assert d.exceeds_limit(0.70) == []


class TestNormHelpers:
    def test_norm_ppf_50(self):
        assert abs(_norm_ppf(0.5)) < 0.01

    def test_norm_ppf_95(self):
        assert abs(_norm_ppf(0.95) - 1.645) < 0.01

    def test_norm_pdf_at_zero(self):
        expected = 1.0 / math.sqrt(2 * math.pi)
        assert abs(_norm_pdf(0.0) - expected) < 1e-10
