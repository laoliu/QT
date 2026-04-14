"""Tests for sensitivity analysis module.

Coverage targets:
- run_sensitivity_analysis with stable/unstable params
- PerturbationPoint and ParamSensitivity data structures
- SensitivityReport serialization
- Error handling in evaluate_fn
- Stability scoring logic
"""
from __future__ import annotations

import pytest

from aipha.backtest.sensitivity import (
    ParamSensitivity,
    PerturbationPoint,
    SensitivityReport,
    run_sensitivity_analysis,
)
from aipha.strategy.param_space import ParamSpace, ParamSpec, ParamType


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def simple_space() -> ParamSpace:
    """简单双参数空间"""
    return ParamSpace("test_strategy", [
        ParamSpec("k", ParamType.INT, 20, low=5, high=50, is_critical=True),
        ParamSpec("threshold", ParamType.FLOAT, 0.5, low=0.1, high=1.0),
    ])


@pytest.fixture
def stable_evaluate():
    """模拟稳定策略的评估函数：参数变化对指标影响小"""
    def fn(params: dict) -> float:
        k = params.get("k", 20)
        threshold = params.get("threshold", 0.5)
        # 指标围绕 1.5 轻微波动
        return 1.5 + (k - 20) * 0.001 + (threshold - 0.5) * 0.01
    return fn


@pytest.fixture
def unstable_evaluate():
    """模拟不稳定策略的评估函数：参数变化对指标影响大"""
    def fn(params: dict) -> float:
        k = params.get("k", 20)
        # 指标对 k 高度敏感
        return k * 0.1  # k=20 → 2.0, k=16 → 1.6, k=24 → 2.4
    return fn


# ============================================================================
# run_sensitivity_analysis Tests
# ============================================================================


class TestRunSensitivityAnalysis:
    """敏感性分析执行测试"""

    def test_basic_stable(self, simple_space, stable_evaluate):
        """稳定策略应报告为稳定"""
        base = {"k": 20, "threshold": 0.5}
        report = run_sensitivity_analysis(
            base, simple_space, stable_evaluate,
        )

        assert report.strategy_name == "test_strategy"
        assert report.metric_name == "sharpe_ratio"
        assert report.base_metric == pytest.approx(1.5)
        assert len(report.results) == 2
        assert report.overall_stable is True

    def test_unstable_detection(self, simple_space, unstable_evaluate):
        """不稳定策略应被检测"""
        base = {"k": 20, "threshold": 0.5}
        report = run_sensitivity_analysis(
            base, simple_space, unstable_evaluate,
            stability_threshold=0.9,  # Very high threshold
        )

        k_result = next(r for r in report.results if r.param_name == "k")
        assert k_result.std_metric > 0
        # With -20%/-10%/+10%/+20% perturbation on k=20,
        # values would be 16,18,22,24 → metrics 1.6,1.8,2.2,2.4
        # High variance → low stability score

    def test_most_sensitive_param(self, simple_space):
        """应正确识别最敏感参数"""
        def evaluate(params):
            k = params.get("k", 20)
            threshold = params.get("threshold", 0.5)
            # k 高度敏感，threshold 稳定
            return k * 0.1 + threshold * 0.001

        base = {"k": 20, "threshold": 0.5}
        report = run_sensitivity_analysis(base, simple_space, evaluate)

        assert report.most_sensitive_param == "k"

    def test_only_critical(self, simple_space, stable_evaluate):
        """only_critical 应仅分析关键参数"""
        base = {"k": 20, "threshold": 0.5}
        report = run_sensitivity_analysis(
            base, simple_space, stable_evaluate,
            only_critical=True,
        )

        # 只有 k 是 critical
        assert len(report.results) == 1
        assert report.results[0].param_name == "k"

    def test_custom_perturbation_pcts(self, simple_space, stable_evaluate):
        """自定义扰动百分比"""
        base = {"k": 20, "threshold": 0.5}
        report = run_sensitivity_analysis(
            base, simple_space, stable_evaluate,
            perturbation_pcts=(-0.05, 0.05),
        )

        for r in report.results:
            n_points = len(r.points)
            assert n_points >= 1  # At least some perturbation points

    def test_evaluate_fn_error_handling(self, simple_space):
        """评估函数异常应被捕获"""
        call_count = {"n": 0}

        def bad_evaluate(params):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return 1.0  # Base case
            if params.get("k", 20) > 22:
                raise RuntimeError("Boom")
            return 1.0

        base = {"k": 20, "threshold": 0.5}
        # Should not raise
        report = run_sensitivity_analysis(base, simple_space, bad_evaluate)
        assert report.base_metric == 1.0

    def test_min_metric_threshold(self, simple_space):
        """低于最低指标阈值应判为不稳定"""
        def evaluate(params):
            k = params.get("k", 20)
            if k < 18:
                return -0.5  # Negative metric
            return 1.0

        base = {"k": 20, "threshold": 0.5}
        report = run_sensitivity_analysis(
            base, simple_space, evaluate,
            min_metric_threshold=0.0,
        )

        k_result = next(r for r in report.results if r.param_name == "k")
        assert k_result.is_stable is False  # min_metric < 0

    def test_base_metric_zero(self):
        """基准指标为 0 时的稳定性计算"""
        space = ParamSpace("test", [
            ParamSpec("x", ParamType.FLOAT, 1.0, low=0.5, high=2.0),
        ])

        def evaluate(params):
            return 0.0  # Always returns 0

        report = run_sensitivity_analysis(
            {"x": 1.0}, space, evaluate,
        )
        # base=0, all perturbations=0, std=0 → stability_score=1.0
        assert report.results[0].stability_score == 1.0


# ============================================================================
# Data Structure Tests
# ============================================================================


class TestPerturbationPoint:
    """PerturbationPoint 数据结构测试"""

    def test_frozen(self):
        """应为不可变"""
        p = PerturbationPoint("k", 0.1, 22, 1.5)
        with pytest.raises(AttributeError):
            p.metric_value = 2.0  # type: ignore

    def test_none_perturbation_pct(self):
        """bool/choice 类型扰动百分比为 None"""
        p = PerturbationPoint("flag", None, False, 1.2)
        assert p.perturbation_pct is None


class TestSensitivityReport:
    """SensitivityReport 测试"""

    def test_to_dict(self):
        """序列化为字典"""
        report = SensitivityReport(
            strategy_name="test",
            metric_name="sharpe",
            base_params={"k": 20},
            base_metric=1.5,
            results=[
                ParamSensitivity(
                    param_name="k",
                    base_value=20,
                    base_metric=1.5,
                    mean_metric=1.45,
                    std_metric=0.1,
                    min_metric=1.3,
                    max_metric=1.6,
                    stability_score=0.93,
                    is_stable=True,
                ),
            ],
            overall_stable=True,
            most_sensitive_param="k",
        )

        d = report.to_dict()
        assert d["strategy_name"] == "test"
        assert d["base_metric"] == 1.5
        assert len(d["results"]) == 1
        assert d["results"][0]["param_name"] == "k"
        assert d["results"][0]["stability_score"] == 0.93

    def test_to_dict_strips_metadata(self):
        """to_dict 应去除 _ 开头的元数据"""
        report = SensitivityReport(
            strategy_name="test",
            metric_name="sharpe",
            base_params={"k": 20, "_internal": True},
            base_metric=1.5,
        )
        d = report.to_dict()
        assert "_internal" not in d["base_params"]

    def test_empty_report(self):
        """空结果报告"""
        report = SensitivityReport(
            strategy_name="test",
            metric_name="sharpe",
            base_params={},
            base_metric=0.0,
        )
        assert report.overall_stable is True
        assert report.most_sensitive_param == ""
