"""指数增强策略框架单元测试。"""

from __future__ import annotations

import pytest
import numpy as np
import pandas as pd

from aipha.strategy.index_enhance import (
    IndexEnhanceConfig,
    BarraConstraint,
    IndexEnhanceOptimizer,
    EnhanceResult,
)


# ── 配置测试 ──


class TestIndexEnhanceConfig:
    def test_defaults(self):
        c = IndexEnhanceConfig()
        assert c.benchmark == "csi1000"
        assert c.tracking_error_target == 0.05
        assert c.top_k == 100

    def test_custom(self):
        c = IndexEnhanceConfig(benchmark="csi300", top_k=50)
        assert c.benchmark == "csi300"


# ── Barra 约束测试 ──


class TestBarraConstraint:
    def test_check_tracking_error_zero(self):
        config = IndexEnhanceConfig()
        bc = BarraConstraint(config)
        te = bc.check_tracking_error({}, pd.DataFrame())
        assert te == 0.0

    def test_check_tracking_error_small(self):
        config = IndexEnhanceConfig()
        bc = BarraConstraint(config)
        codes = ["A", "B"]
        cov = pd.DataFrame(
            np.eye(2) * 0.0004,  # daily vol ~2%
            index=codes, columns=codes,
        )
        active_w = {"A": 0.01, "B": -0.01}
        te = bc.check_tracking_error(active_w, cov)
        assert te > 0.0
        assert te < 1.0  # 合理范围

    def test_feasible_no_violations(self):
        config = IndexEnhanceConfig(
            industry_deviation_max=0.10,
            tracking_error_max=0.10,
        )
        bc = BarraConstraint(config)
        ok, v = bc.is_feasible({}, {})
        assert ok
        assert len(v) == 0

    def test_tracking_error_violation(self):
        config = IndexEnhanceConfig(tracking_error_max=0.001)
        bc = BarraConstraint(config)
        codes = ["A", "B"]
        cov = pd.DataFrame(
            np.eye(2) * 0.01,
            index=codes, columns=codes,
        )
        active_w = {"A": 0.5, "B": -0.5}
        ok, v = bc.is_feasible(active_w, {}, cov_matrix=cov)
        assert not ok
        assert any("TE" in x for x in v)


# ── 优化器测试 ──


class TestIndexEnhanceOptimizer:
    def test_empty_alpha(self):
        config = IndexEnhanceConfig()
        opt = IndexEnhanceOptimizer(config)
        result = opt.optimize({}, {})
        assert len(result.target_weights) == 0

    def test_basic_optimize(self):
        config = IndexEnhanceConfig(top_k=3)
        opt = IndexEnhanceOptimizer(config)
        alphas = {"A": 2.0, "B": 1.5, "C": 1.0, "D": 0.5}
        bench = {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
        result = opt.optimize(alphas, bench)
        # top_k=3, so only A, B, C
        assert len(result.target_weights) == 3
        assert "D" not in result.target_weights
        # 权重和 ≈ 1.0
        total = sum(result.target_weights.values())
        assert abs(total - 1.0) < 1e-6

    def test_active_weights_computed(self):
        config = IndexEnhanceConfig(top_k=2)
        opt = IndexEnhanceOptimizer(config)
        alphas = {"A": 3.0, "B": 1.0}
        bench = {"A": 0.5, "B": 0.5}
        result = opt.optimize(alphas, bench)
        assert "A" in result.active_weights

    def test_weight_bounds(self):
        config = IndexEnhanceConfig(
            top_k=5, min_weight=0.01, max_weight=0.03,
        )
        opt = IndexEnhanceOptimizer(config)
        alphas = {f"S{i}": float(i) for i in range(10)}
        bench = {f"S{i}": 0.1 for i in range(10)}
        result = opt.optimize(alphas, bench)
        for w in result.target_weights.values():
            assert w > 0  # 归一化后都 > 0

    def test_no_benchmark(self):
        """无基准权重时也能运行"""
        config = IndexEnhanceConfig(top_k=3)
        opt = IndexEnhanceOptimizer(config)
        alphas = {"A": 2.0, "B": 1.0, "C": 0.5}
        result = opt.optimize(alphas, {})
        assert len(result.target_weights) == 3


# ── EnhanceResult 测试 ──


class TestEnhanceResult:
    def test_empty(self):
        r = EnhanceResult()
        assert r.predicted_te == 0.0
        assert len(r.target_weights) == 0
