"""Edge tests for backtest/sensitivity.py — 边界与极端场景。

覆盖:
- 空 param_space（0 个 ParamSpec）
- 全部扰动评估失败
- 负 base_metric
- stability_score 计算边界
- SensitivityReport.to_dict 结构完整性
- ParamSensitivity 默认值
"""

from __future__ import annotations

from typing import Any

import pytest

from aipha.backtest.sensitivity import (
    ParamSensitivity,
    PerturbationPoint,
    SensitivityReport,
    run_sensitivity_analysis,
)
from aipha.strategy.param_space import ParamSpace, ParamSpec, ParamType


def _make_space(name: str = "test_strategy", specs: list | None = None) -> ParamSpace:
    return ParamSpace(strategy_name=name, specs=specs or [])


class TestEmptyParamSpace:
    """ParamSpace 无任何参数声明。"""

    def test_empty_space_returns_stable(self) -> None:
        space = _make_space(specs=[])
        report = run_sensitivity_analysis(
            base_params={},
            param_space=space,
            evaluate_fn=lambda p: 1.0,
        )
        assert report.overall_stable is True
        assert len(report.results) == 0
        assert report.most_sensitive_param == ""

    def test_empty_space_to_dict(self) -> None:
        space = _make_space(specs=[])
        report = run_sensitivity_analysis(
            base_params={},
            param_space=space,
            evaluate_fn=lambda p: 2.0,
        )
        d = report.to_dict()
        assert d["base_metric"] == 2.0
        assert d["results"] == []


class TestAllPerturbationsFail:
    """所有扰动调用 evaluate_fn 均抛异常。"""

    def test_all_fail_gives_empty_points(self) -> None:
        space = _make_space(specs=[
            ParamSpec(name="top_k", param_type=ParamType.INT, default=10, low=5, high=30),
        ])
        call_count = {"n": 0}

        def evaluate_fn(params: dict[str, Any]) -> float:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return 1.5  # base call succeeds
            raise ValueError("boom")

        report = run_sensitivity_analysis(
            base_params={"top_k": 10},
            param_space=space,
            evaluate_fn=evaluate_fn,
        )
        # 基准成功，扰动全失败 → 该参数 points 为空 → 默认 ParamSensitivity
        assert len(report.results) == 1
        r = report.results[0]
        assert len(r.points) == 0
        assert r.stability_score == 0.0  # default
        assert r.is_stable is True  # default


class TestNegativeBaseMetric:
    """base_metric 为负值。"""

    def test_negative_base_metric_stability(self) -> None:
        space = _make_space(specs=[
            ParamSpec(name="x", param_type=ParamType.FLOAT, default=1.0, low=0.5, high=2.0),
        ])

        def evaluate_fn(params: dict[str, Any]) -> float:
            return -1.5  # 全返回相同值

        report = run_sensitivity_analysis(
            base_params={"x": 1.0},
            param_space=space,
            evaluate_fn=evaluate_fn,
        )
        # std=0, abs(base_metric)=1.5 → score=max(0, 1-0/1.5)=1.0
        for r in report.results:
            assert r.stability_score == pytest.approx(1.0)
            assert r.is_stable is False  # min_val=-1.5 < min_metric_threshold(0.0)


class TestStabilityScoreEdge:
    """stability_score 计算极端场景。"""

    def test_base_metric_near_zero(self) -> None:
        """base_metric ≈ 0 但不为 0 → 正常除法。"""
        space = _make_space(specs=[
            ParamSpec(name="y", param_type=ParamType.FLOAT, default=1.0, low=0.5, high=2.0),
        ])

        values = iter([1e-11, 1e-11, 1e-11, 1e-11, 1e-11])

        def evaluate_fn(params: dict[str, Any]) -> float:
            return next(values, 1e-11)

        report = run_sensitivity_analysis(
            base_params={"y": 1.0},
            param_space=space,
            evaluate_fn=evaluate_fn,
        )
        # std~0, base_metric~0 → 走 base_metric ≈ 0 分支
        for r in report.results:
            assert 0.0 <= r.stability_score <= 1.0


class TestParamSensitivityDefaults:
    """ParamSensitivity 默认字段值。"""

    def test_defaults(self) -> None:
        ps = ParamSensitivity(param_name="x", base_value=10, base_metric=1.0)
        assert ps.points == []
        assert ps.mean_metric == 0.0
        assert ps.std_metric == 0.0
        assert ps.stability_score == 0.0
        assert ps.is_stable is True


class TestPerturbationPoint:
    """PerturbationPoint frozen dataclass。"""

    def test_frozen(self) -> None:
        pp = PerturbationPoint("x", 0.1, 11, 1.5)
        with pytest.raises((AttributeError, TypeError)):
            pp.metric_value = 2.0  # type: ignore[misc]

    def test_slots(self) -> None:
        pp = PerturbationPoint("x", 0.1, 11, 1.5)
        with pytest.raises((AttributeError, TypeError)):
            pp.extra = 1  # type: ignore[attr-defined]

    def test_none_perturbation_pct(self) -> None:
        pp = PerturbationPoint("flag", None, True, 1.0)
        assert pp.perturbation_pct is None
        assert pp.param_value is True


class TestReportToDict:
    """SensitivityReport.to_dict 结构验证。"""

    def test_to_dict_with_results(self) -> None:
        ps = ParamSensitivity(
            param_name="k", base_value=10, base_metric=1.5,
            points=[PerturbationPoint("k", 0.1, 11, 1.4)],
            mean_metric=1.45, std_metric=0.05, min_metric=1.4,
            max_metric=1.5, stability_score=0.97, is_stable=True,
        )
        report = SensitivityReport(
            strategy_name="s1", metric_name="sharpe",
            base_params={"k": 10}, base_metric=1.5,
            results=[ps], overall_stable=True,
            most_sensitive_param="k",
        )
        d = report.to_dict()
        assert d["strategy_name"] == "s1"
        assert len(d["results"]) == 1
        assert d["results"][0]["n_points"] == 1
        assert d["results"][0]["stability_score"] == pytest.approx(0.97)

    def test_to_dict_strips_underscore_params(self) -> None:
        report = SensitivityReport(
            strategy_name="s1", metric_name="sharpe",
            base_params={"k": 10, "_internal": True},
            base_metric=1.0,
        )
        d = report.to_dict()
        assert "_internal" not in d["base_params"]
        assert "k" in d["base_params"]
