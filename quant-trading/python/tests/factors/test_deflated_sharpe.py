"""Deflated Sharpe Ratio (DSR) 单元测试."""
from __future__ import annotations

import numpy as np
import pytest

from aipha.factors.deflated_sharpe import (
    compute_dsr_for_factor,
    deflated_sharpe_ratio,
    expected_max_sr,
    sr_std_corrected,
)


class TestExpectedMaxSR:
    """预期最大夏普比率测试."""

    def test_single_trial(self):
        assert expected_max_sr(1) == 0.0

    def test_monotone_increasing(self):
        """更多试验 → 更高的 E[max(SR)]."""
        vals = [expected_max_sr(n) for n in [2, 10, 50, 130, 500]]
        for i in range(1, len(vals)):
            assert vals[i] > vals[i - 1]

    def test_known_value_approx(self):
        """N=100, sr_std=1 → E[max] ≈ 2.5-3.0."""
        e = expected_max_sr(100, sr_std=1.0)
        assert 2.0 < e < 3.5

    def test_sr_std_scaling(self):
        """sr_std 是线性缩放."""
        e1 = expected_max_sr(50, sr_std=1.0)
        e2 = expected_max_sr(50, sr_std=2.0)
        assert abs(e2 / e1 - 2.0) < 0.01


class TestSRStdCorrected:
    """SR 标准差 (非正态校正) 测试."""

    def test_normal_case(self):
        """正态分布: skew=0, kurtosis=3 → 标准公式."""
        se = sr_std_corrected(1.0, 0.0, 3.0, 60)
        # Var ≈ (1 + 0 + (3-1)/4*1) / 60 = (1+0.5)/60 = 0.025
        expected = (1.5 / 60) ** 0.5
        assert abs(se - expected) < 0.01

    def test_negative_skew_increases_std(self):
        """负偏度 + 正SR → 更大的标准差."""
        se_normal = sr_std_corrected(1.0, 0.0, 3.0, 60)
        se_neg_skew = sr_std_corrected(1.0, -1.0, 3.0, 60)
        assert se_neg_skew > se_normal

    def test_single_obs(self):
        assert sr_std_corrected(1.0, 0.0, 3.0, 1) == 1.0


class TestDeflatedSharpeRatio:
    """DSR 完整计算测试."""

    def test_output_structure(self):
        r = deflated_sharpe_ratio(
            observed_sr=1.5, n_trials=100,
            t_observations=60)
        assert "dsr" in r
        assert "p_value" in r
        assert "e_max_sr" in r
        assert "is_significant" in r
        assert 0 <= r["p_value"] <= 1

    def test_high_sr_significant(self):
        """极高 SR + 少量试验 → 显著."""
        r = deflated_sharpe_ratio(
            observed_sr=3.0, n_trials=5,
            t_observations=120)
        assert r["is_significant"]

    def test_low_sr_not_significant(self):
        """低 SR + 大量试验 → 不显著."""
        r = deflated_sharpe_ratio(
            observed_sr=0.5, n_trials=500,
            t_observations=60)
        assert not r["is_significant"]

    def test_more_trials_harder_to_pass(self):
        """更多试验 → 更难通过 (更高的 e_max)."""
        r10 = deflated_sharpe_ratio(
            observed_sr=2.0, n_trials=10,
            t_observations=60)
        r500 = deflated_sharpe_ratio(
            observed_sr=2.0, n_trials=500,
            t_observations=60)
        assert r500["p_value"] > r10["p_value"]


class TestComputeDSRForFactor:
    """从 IC 序列计算 DSR."""

    def test_strong_factor(self):
        """强因子 (ICIR≈1.6) + 少量试验 → DSR computed, p<0.5."""
        rng = np.random.RandomState(42)
        ics = list(rng.normal(0.05, 0.03, 84))  # ICIR ≈ 1.6
        r = compute_dsr_for_factor(ics, n_trials=5)  # 少量试验
        assert r["observed_sr"] > 1.0
        assert r["p_value"] < 0.5  # 5 trials → E[max]≈1.5 < ICIR=1.6

    def test_weak_factor(self):
        rng = np.random.RandomState(7)
        ics = list(rng.normal(0.001, 0.05, 60))
        r = compute_dsr_for_factor(ics, n_trials=200)
        assert r["is_significant"] is False

    def test_insufficient_data(self):
        r = compute_dsr_for_factor([0.01, 0.02], n_trials=10)
        assert r["p_value"] == 1.0
        assert r["is_significant"] is False
