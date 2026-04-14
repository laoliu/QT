"""统计稳健性框架单元测试。

覆盖：
- BootstrapAnalyzer: CI 估计、显著性判断、空输入、多指标
- RollingOriginReport: 指标提取、汇总统计
- ScaleTestReport: 规模退化检测
- RobustnessReport: 综合评分与结论生成
"""

from __future__ import annotations

import math
import pytest

from aipha.backtest.robustness import (
    BootstrapAnalyzer,
    BootstrapCI,
    RobustnessReport,
    RollingOriginReport,
    RollingOriginResult,
    ScaleTestPoint,
    ScaleTestReport,
)


# ============================================================================
# BootstrapCI
# ============================================================================


class TestBootstrapCI:
    """BootstrapCI 数据类测试。"""

    def test_contains_zero_positive_ci(self):
        ci = BootstrapCI("sharpe", 1.5, 0.5, 2.5, 0.95, 1000, 0.1, 0.0)
        assert not ci.contains_zero()

    def test_contains_zero_negative_ci(self):
        ci = BootstrapCI("sharpe", -0.5, -1.5, -0.1, 0.95, 1000, 0.1, 0.0)
        assert not ci.contains_zero()

    def test_contains_zero_straddle(self):
        ci = BootstrapCI("sharpe", 0.2, -0.3, 0.7, 0.95, 1000, 0.1, 0.0)
        assert ci.contains_zero()

    def test_contains_zero_at_boundary(self):
        ci = BootstrapCI("sharpe", 0.5, 0.0, 1.0, 0.95, 1000, 0.1, 0.0)
        assert ci.contains_zero()  # 0 在边界上 → 包含

    def test_to_dict(self):
        ci = BootstrapCI("ret", 0.05, 0.01, 0.09, 0.95, 5000, 0.02, 0.001)
        d = ci.to_dict()
        assert d["metric_name"] == "ret"
        assert d["observed"] == 0.05
        assert d["n_bootstrap"] == 5000


# ============================================================================
# BootstrapAnalyzer
# ============================================================================


class TestBootstrapAnalyzer:
    """BootstrapAnalyzer 测试。"""

    def test_init_validation_n_bootstrap(self):
        with pytest.raises(ValueError, match="至少 100"):
            BootstrapAnalyzer(n_bootstrap=10)

    def test_init_validation_confidence(self):
        with pytest.raises(ValueError, match="confidence_level"):
            BootstrapAnalyzer(confidence_level=1.5)

    def test_empty_values(self):
        ba = BootstrapAnalyzer(n_bootstrap=100)
        ci = ba.estimate_ci([], "empty")
        assert ci.observed == 0.0
        assert ci.n_bootstrap == 0

    def test_single_value(self):
        ba = BootstrapAnalyzer(n_bootstrap=100)
        ci = ba.estimate_ci([2.0], "single")
        # 只有一个值，CI 应该退化为点
        assert ci.ci_lower == pytest.approx(2.0)
        assert ci.ci_upper == pytest.approx(2.0)
        assert ci.observed == pytest.approx(2.0)

    def test_positive_sharpes_ci_excludes_zero(self):
        """多个正 Sharpe 值，CI 应不含 0。"""
        ba = BootstrapAnalyzer(n_bootstrap=5000, random_seed=42)
        values = [1.2, 1.5, 0.8, 1.1, 1.3, 0.9, 1.4, 1.0]
        ci = ba.estimate_ci(values, "sharpe")
        assert ci.observed > 0
        assert ci.ci_lower > 0  # 全正值的 CI 下界应 > 0
        assert not ci.contains_zero()

    def test_mixed_sharpes_ci_may_contain_zero(self):
        """正负混合的 Sharpe 值，CI 可能含 0。"""
        ba = BootstrapAnalyzer(n_bootstrap=5000, random_seed=42)
        values = [0.5, -0.3, 0.2, -0.1, 0.1, -0.4, 0.3, -0.2]
        ci = ba.estimate_ci(values, "sharpe")
        # 均值约 0.0125，CI 应跨 0
        assert ci.contains_zero()

    def test_confidence_level_affects_width(self):
        """更高的置信水平 → 更宽的 CI。"""
        values = [1.0, 1.2, 0.8, 1.1, 0.9, 1.3, 0.7, 1.4]
        ci_90 = BootstrapAnalyzer(n_bootstrap=5000, confidence_level=0.90, random_seed=42).estimate_ci(values)
        ci_99 = BootstrapAnalyzer(n_bootstrap=5000, confidence_level=0.99, random_seed=42).estimate_ci(values)
        width_90 = ci_90.ci_upper - ci_90.ci_lower
        width_99 = ci_99.ci_upper - ci_99.ci_lower
        assert width_99 > width_90

    def test_reproducibility_with_seed(self):
        """相同种子应产出完全相同的结果。"""
        values = [0.5, 1.0, 1.5, 0.8, 1.2]
        ci1 = BootstrapAnalyzer(n_bootstrap=1000, random_seed=123).estimate_ci(values)
        ci2 = BootstrapAnalyzer(n_bootstrap=1000, random_seed=123).estimate_ci(values)
        assert ci1.ci_lower == ci2.ci_lower
        assert ci1.ci_upper == ci2.ci_upper

    def test_different_seeds_differ(self):
        """不同种子应产出不同 std_error（偏差更敏感）。"""
        values = [0.5, 1.0, 1.5, 0.8, 1.2, 0.3, 0.9, 2.1, 0.1, 1.7]
        ci1 = BootstrapAnalyzer(n_bootstrap=5000, random_seed=1).estimate_ci(values)
        ci2 = BootstrapAnalyzer(n_bootstrap=5000, random_seed=999).estimate_ci(values)
        # 偏差（bias）和 std_error 即使 CI 碰巧相同也应有细微差异
        assert ci1.bias != ci2.bias or ci1.std_error != ci2.std_error

    def test_bias_is_small(self):
        """对大样本，Bootstrap 偏差应很小。"""
        values = list(range(100))
        ba = BootstrapAnalyzer(n_bootstrap=5000, random_seed=42)
        ci = ba.estimate_ci(values, "test")
        assert abs(ci.bias) < 1.0  # 偏差远小于均值 49.5

    def test_estimate_multiple(self):
        """多指标批量估计。"""
        ba = BootstrapAnalyzer(n_bootstrap=500, random_seed=42)
        metrics = {
            "sharpe": [1.0, 1.1, 0.9, 1.2, 0.8],
            "return": [0.05, 0.03, 0.04, 0.06, 0.02],
        }
        results = ba.estimate_multiple(metrics)
        assert "sharpe" in results
        assert "return" in results
        assert results["sharpe"].metric_name == "sharpe"
        assert results["return"].metric_name == "return"

    def test_std_error_positive(self):
        """标准误应为正数。"""
        ba = BootstrapAnalyzer(n_bootstrap=500, random_seed=42)
        ci = ba.estimate_ci([1.0, 2.0, 3.0, 4.0, 5.0])
        assert ci.std_error > 0


# ============================================================================
# RollingOriginReport
# ============================================================================


class TestRollingOriginReport:
    """RollingOriginReport 测试。"""

    def _make_report(self) -> RollingOriginReport:
        results = [
            RollingOriginResult(i, train_size=100 + i * 50, test_size=50,
                                metrics={"sharpe": 0.5 + i * 0.3, "return": 0.02 + i * 0.01})
            for i in range(5)
        ]
        return RollingOriginReport(
            strategy_name="test_strat",
            results=results,
            n_origins=5,
            expanding=True,
        )

    def test_metric_series(self):
        report = self._make_report()
        sharpes = report.metric_series("sharpe")
        assert len(sharpes) == 5
        assert sharpes[0] == pytest.approx(0.5)
        assert sharpes[4] == pytest.approx(1.7)

    def test_metric_series_missing(self):
        report = self._make_report()
        missing = report.metric_series("nonexistent")
        assert len(missing) == 0

    def test_summary_stats(self):
        report = self._make_report()
        stats = report.summary_stats("sharpe")
        assert stats["count"] == 5
        assert stats["min"] == pytest.approx(0.5)
        assert stats["max"] == pytest.approx(1.7)
        expected_mean = (0.5 + 0.8 + 1.1 + 1.4 + 1.7) / 5
        assert stats["mean"] == pytest.approx(expected_mean)
        assert stats["median"] == pytest.approx(1.1)

    def test_summary_stats_empty(self):
        report = RollingOriginReport(strategy_name="empty")
        stats = report.summary_stats("sharpe")
        assert stats["count"] == 0
        assert stats["mean"] == 0

    def test_to_dict(self):
        report = self._make_report()
        d = report.to_dict()
        assert d["strategy_name"] == "test_strat"
        assert d["n_origins"] == 5
        assert len(d["results"]) == 5


# ============================================================================
# ScaleTestReport
# ============================================================================


class TestScaleTestReport:
    """ScaleTestReport 测试。"""

    def test_metric_vs_scale(self):
        report = ScaleTestReport(
            strategy_name="test",
            points=[
                ScaleTestPoint(5, {"sharpe": 2.0}),
                ScaleTestPoint(20, {"sharpe": 1.8}),
                ScaleTestPoint(50, {"sharpe": 1.5}),
            ],
        )
        pairs = report.metric_vs_scale("sharpe")
        assert len(pairs) == 3
        assert pairs[0] == (5, 2.0)

    def test_no_monotonic_degradation(self):
        """指标随规模波动但未单调恶化。"""
        report = ScaleTestReport(
            strategy_name="test",
            points=[
                ScaleTestPoint(5, {"sharpe": 1.5}),
                ScaleTestPoint(20, {"sharpe": 1.6}),
                ScaleTestPoint(50, {"sharpe": 1.4}),
            ],
        )
        # 1.5 → 1.4，衰减 6.7% < 10% tolerance
        assert not report.is_monotonic_degradation("sharpe", tolerance=0.1)

    def test_monotonic_degradation(self):
        """指标随规模显著恶化。"""
        report = ScaleTestReport(
            strategy_name="test",
            points=[
                ScaleTestPoint(5, {"sharpe": 2.0}),
                ScaleTestPoint(20, {"sharpe": 1.2}),
                ScaleTestPoint(50, {"sharpe": 0.5}),
            ],
        )
        # 2.0 → 0.5，衰减 75% > 10%
        assert report.is_monotonic_degradation("sharpe", tolerance=0.1)

    def test_monotonic_degradation_insufficient_points(self):
        report = ScaleTestReport(
            strategy_name="test",
            points=[ScaleTestPoint(5, {"sharpe": 2.0})],
        )
        assert not report.is_monotonic_degradation("sharpe")

    def test_to_dict(self):
        report = ScaleTestReport(
            strategy_name="test",
            points=[ScaleTestPoint(10, {"sharpe": 1.0, "return": 0.05})],
        )
        d = report.to_dict()
        assert len(d["points"]) == 1
        assert d["points"][0]["n_instruments"] == 10


# ============================================================================
# RobustnessReport
# ============================================================================


class TestRobustnessReport:
    """RobustnessReport 测试。"""

    def _make_full_report(self) -> RobustnessReport:
        ba = BootstrapAnalyzer(n_bootstrap=1000, random_seed=42)
        sharpe_ci = ba.estimate_ci([1.2, 1.5, 0.8, 1.1, 1.3], "sharpe")
        return_ci = ba.estimate_ci([0.05, 0.03, 0.04, 0.06, 0.02], "return")

        rolling = RollingOriginReport(
            strategy_name="test",
            results=[
                RollingOriginResult(i, 100, 50, {"sharpe": 0.5 + i * 0.2})
                for i in range(5)
            ],
            n_origins=5,
        )

        scale = ScaleTestReport(
            strategy_name="test",
            points=[
                ScaleTestPoint(5, {"sharpe": 1.5}),
                ScaleTestPoint(20, {"sharpe": 1.3}),
                ScaleTestPoint(50, {"sharpe": 1.2}),
            ],
        )

        return RobustnessReport(
            strategy_name="test",
            bootstrap_results={"sharpe": sharpe_ci, "return": return_ci},
            rolling_origin=rolling,
            scale_test=scale,
        )

    def test_compute_score_full(self):
        report = self._make_full_report()
        score = report.compute_score()
        assert 0 <= score <= 10
        assert score > 5  # 全正指标，应有较高分

    def test_compute_score_empty(self):
        report = RobustnessReport(strategy_name="empty")
        assert report.compute_score() == 0.0

    def test_compute_score_only_bootstrap(self):
        ba = BootstrapAnalyzer(n_bootstrap=500, random_seed=42)
        sharpe_ci = ba.estimate_ci([1.0, 1.2, 0.9, 1.1, 0.8], "sharpe")
        report = RobustnessReport(
            strategy_name="partial",
            bootstrap_results={"sharpe": sharpe_ci},
        )
        score = report.compute_score()
        assert 0 < score <= 10

    def test_generate_conclusion(self):
        report = self._make_full_report()
        conclusion = report.generate_conclusion()
        assert "test" in conclusion
        assert "稳健性" in conclusion
        assert "Sharpe" in conclusion
        assert "/10" in conclusion

    def test_generate_conclusion_with_neg_sharpe(self):
        ba = BootstrapAnalyzer(n_bootstrap=500, random_seed=42)
        sharpe_ci = ba.estimate_ci([-0.5, -0.3, -0.1, -0.4, -0.2], "sharpe")
        report = RobustnessReport(
            strategy_name="bad",
            bootstrap_results={"sharpe": sharpe_ci},
        )
        conclusion = report.generate_conclusion()
        assert "不显著" in conclusion or "✗" in conclusion

    def test_to_dict(self):
        report = self._make_full_report()
        report.generate_conclusion()
        d = report.to_dict()
        assert d["strategy_name"] == "test"
        assert "bootstrap" in d
        assert "rolling_origin" in d
        assert "scale_test" in d

    def test_degraded_scale_lowers_score(self):
        """组合规模恶化应拉低评分。"""
        ba = BootstrapAnalyzer(n_bootstrap=500, random_seed=42)
        sharpe_ci = ba.estimate_ci([1.0, 1.2, 0.9], "sharpe")

        # 不恶化
        scale_ok = ScaleTestReport("ok", [
            ScaleTestPoint(5, {"sharpe": 1.5}),
            ScaleTestPoint(50, {"sharpe": 1.4}),
        ])
        report_ok = RobustnessReport("ok", {"sharpe": sharpe_ci}, scale_test=scale_ok)
        score_ok = report_ok.compute_score()

        # 恶化
        scale_bad = ScaleTestReport("bad", [
            ScaleTestPoint(5, {"sharpe": 1.5}),
            ScaleTestPoint(50, {"sharpe": 0.1}),
        ])
        report_bad = RobustnessReport("bad", {"sharpe": sharpe_ci}, scale_test=scale_bad)
        score_bad = report_bad.compute_score()

        assert score_ok > score_bad
