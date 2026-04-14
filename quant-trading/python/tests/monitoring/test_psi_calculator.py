"""PSI 计算器单元测试

覆盖：
- compute_single: 正常/无漂移/轻度漂移/显著漂移/数据不足/常数值
- compute: 多特征批量/空输入/混合状态
- _psi_from_proportions: 基础公式验证
- DriftReport: 序列化/兼容 RetrainTrigger 接口
"""

from __future__ import annotations

import math
import random

import pytest

from aipha.monitoring.psi_calculator import (
    DriftReport,
    FeaturePSI,
    PSICalculator,
)


class TestPSIFromProportions:

    def test_identical_distributions(self):
        """相同分布 PSI=0。"""
        p = [0.2, 0.3, 0.5]
        psi = PSICalculator._psi_from_proportions(p, p)
        assert abs(psi) < 1e-6

    def test_different_distributions(self):
        """不同分布 PSI>0。"""
        p = [0.5, 0.3, 0.2]
        q = [0.2, 0.3, 0.5]
        psi = PSICalculator._psi_from_proportions(p, q)
        assert psi > 0

    def test_symmetry(self):
        """PSI(p,q) == PSI(q,p)（对称性）。"""
        p = [0.4, 0.3, 0.3]
        q = [0.1, 0.2, 0.7]
        psi_pq = PSICalculator._psi_from_proportions(p, q)
        psi_qp = PSICalculator._psi_from_proportions(q, p)
        assert abs(psi_pq - psi_qp) < 1e-6

    def test_zero_handling(self):
        """含零比例时 epsilon 保护。"""
        p = [0.0, 0.5, 0.5]
        q = [0.5, 0.5, 0.0]
        psi = PSICalculator._psi_from_proportions(p, q)
        assert math.isfinite(psi) and psi > 0


class TestComputeSingle:

    def test_stable_distribution(self):
        """相同分布 → stable。"""
        random.seed(42)
        data = [random.gauss(0, 1) for _ in range(500)]
        calc = PSICalculator(n_bins=10)
        r = calc.compute_single(data, data, "f1")
        assert r.status == "stable"
        assert r.psi < 0.10

    def test_shifted_distribution(self):
        """均值偏移 → drifted。"""
        random.seed(42)
        bl = [random.gauss(0, 1) for _ in range(500)]
        cr = [random.gauss(3, 1) for _ in range(500)]
        calc = PSICalculator(n_bins=10)
        r = calc.compute_single(bl, cr, "f1")
        assert r.status == "drifted"
        assert r.psi >= 0.20

    def test_slight_shift(self):
        """小幅偏移可能是 warning。"""
        random.seed(42)
        bl = [random.gauss(0, 1) for _ in range(1000)]
        cr = [random.gauss(0.3, 1) for _ in range(1000)]
        calc = PSICalculator(n_bins=10)
        r = calc.compute_single(bl, cr, "f1")
        assert r.status in ("stable", "warning")

    def test_insufficient_data(self):
        """数据不足返回 insufficient_data。"""
        calc = PSICalculator(n_bins=10)
        r = calc.compute_single([1.0, 2.0], [3.0], "f1")
        assert r.status == "insufficient_data"

    def test_constant_baseline(self):
        """常数基线 → PSI=0, stable。"""
        calc = PSICalculator(n_bins=10)
        r = calc.compute_single(
            [5.0] * 100, [5.0] * 100, "f1"
        )
        assert r.status in ("stable", "constant")
        assert r.psi < 0.01

    def test_bin_details_populated(self):
        """bin_details 非空。"""
        random.seed(42)
        bl = [random.gauss(0, 1) for _ in range(200)]
        cr = [random.gauss(0, 1) for _ in range(200)]
        calc = PSICalculator(n_bins=5)
        r = calc.compute_single(bl, cr, "f1")
        assert len(r.bin_details) > 0


class TestComputeBatch:

    def test_multi_feature(self):
        """多特征批量计算。"""
        random.seed(42)
        bl = {
            "f1": [random.gauss(0, 1) for _ in range(300)],
            "f2": [random.gauss(5, 2) for _ in range(300)],
        }
        cr = {
            "f1": [random.gauss(0, 1) for _ in range(300)],
            "f2": [random.gauss(5, 2) for _ in range(300)],
        }
        calc = PSICalculator()
        report = calc.compute(bl, cr)
        assert report.n_features == 2
        assert isinstance(report.overall_psi, float)

    def test_one_drifted(self):
        """一个漂移一个稳定。"""
        random.seed(42)
        bl = {
            "stable": [random.gauss(0, 1) for _ in range(500)],
            "drift": [random.gauss(0, 1) for _ in range(500)],
        }
        cr = {
            "stable": [random.gauss(0, 1) for _ in range(500)],
            "drift": [random.gauss(5, 1) for _ in range(500)],
        }
        calc = PSICalculator()
        report = calc.compute(bl, cr)
        assert report.n_drifted >= 1
        assert "drift" in report.drifted_features
        assert report.has_significant_drift

    def test_empty_input(self):
        """空输入返回空报告。"""
        calc = PSICalculator()
        report = calc.compute({}, {})
        assert report.n_features == 0
        assert not report.has_significant_drift

    def test_to_dict(self):
        """DriftReport.to_dict 输出完整。"""
        random.seed(42)
        bl = {"f": [random.gauss(0, 1) for _ in range(200)]}
        cr = {"f": [random.gauss(0, 1) for _ in range(200)]}
        calc = PSICalculator()
        report = calc.compute(bl, cr)
        d = report.to_dict()
        assert "overall_psi" in d
        assert "feature_details" in d


class TestRetrainTriggerCompat:
    """验证 DriftReport 兼容 RetrainTrigger 接口。"""

    def test_attributes_exist(self):
        """RetrainTrigger 所需属性存在。"""
        report = DriftReport(
            overall_psi=0.25,
            has_significant_drift=True,
            drifted_features=["f1"],
        )
        assert hasattr(report, "overall_psi")
        assert hasattr(report, "drifted_features")
        assert hasattr(report, "has_significant_drift")

    def test_integration_with_retrain_trigger(self):
        """与 RetrainTrigger.evaluate 集成。"""
        from aipha.monitoring.retrain_trigger import RetrainTrigger

        report = DriftReport(
            overall_psi=0.30,
            has_significant_drift=True,
            drifted_features=["f1", "f2"],
        )
        trigger = RetrainTrigger(psi_threshold=0.20)
        decision = trigger.evaluate(drift_report=report)
        assert decision.should_retrain
