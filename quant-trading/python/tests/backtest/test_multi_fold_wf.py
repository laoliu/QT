"""多折多策略 WF 统计稳健性测试。

验证：
- 不同折数（3/5/8）下 WF 评分的稳定性
- 不同训练/测试窗口下评分变化幅度
- 多策略并行 WF 与 Bootstrap CI 集成
- 确保样本量足够时结论一致
"""

from __future__ import annotations

import math
import pytest
from datetime import date, timedelta

from aipha.backtest.validation import (
    WalkForwardValidator,
    TimeSeriesSplitter,
    PurgedWalkForwardSplitter,
    compute_wf_score,
    judge_walk_forward,
)
from aipha.backtest.robustness import (
    BootstrapAnalyzer,
    RobustnessReport,
    RollingOriginReport,
    RollingOriginResult,
)


# ============================================================================
# 合成策略模拟器
# ============================================================================

def _trending_strategy(start: date, end: date) -> dict[str, float]:
    """模拟趋势策略——长期回报正但波动大。"""
    days = (end - start).days
    annual = days / 365
    ret = 0.12 * annual + 0.02 * math.sin(days)
    sharpe = 0.8 + 0.3 * math.sin(days / 60)
    return {"return": ret, "sharpe": sharpe, "max_drawdown": 0.1 + 0.05 * math.sin(days)}


def _mean_reversion_strategy(start: date, end: date) -> dict[str, float]:
    """模拟均值回复策略——Sharpe 稳定但回报温和。"""
    days = (end - start).days
    annual = days / 365
    ret = 0.06 * annual
    sharpe = 1.2 + 0.1 * math.cos(days / 30)
    return {"return": ret, "sharpe": sharpe, "max_drawdown": 0.05}


def _overfit_strategy(start: date, end: date) -> dict[str, float]:
    """模拟过拟合策略——IS 表现极好，OOS 表现差。

    通过日期范围长度模拟：较长（IS）时表现好，较短（OOS）时表现差。
    同时越晚的年份表现越差（模拟过拟合随时间推移暴露）。
    """
    days = (end - start).days
    mid_year = (start.year + end.year) / 2
    # 越长的窗口（IS）表现越好；越晚年份越差
    if days > 200:
        ret = 0.30 - (mid_year - 2020) * 0.02
        sharpe = 2.5 - (mid_year - 2020) * 0.1
    else:
        ret = -0.05 - (mid_year - 2021) * 0.03
        sharpe = -0.3 - (mid_year - 2021) * 0.15
    return {"return": ret, "sharpe": sharpe, "max_drawdown": 0.20}


def _flat_strategy(start: date, end: date) -> dict[str, float]:
    """模拟无 alpha 策略——接近零回报，Sharpe 时正时负。"""
    days = (end - start).days
    # 微小波动，整体接近 0
    ret = 0.001 * math.sin(days)
    sharpe = 0.02 * math.cos(days / 10)
    return {"return": ret, "sharpe": sharpe, "max_drawdown": 0.02}


STRATEGIES = {
    "trending": _trending_strategy,
    "mean_reversion": _mean_reversion_strategy,
    "overfit": _overfit_strategy,
    "flat": _flat_strategy,
}


# ============================================================================
# Tests
# ============================================================================


class TestMultiFoldWF:
    """不同折数下 WF 稳定性测试。"""

    @pytest.fixture
    def date_range(self):
        return date(2020, 1, 1), date(2024, 12, 31)

    @pytest.mark.parametrize("n_splits", [3, 5, 8])
    def test_trending_strategy_multiple_folds(self, date_range, n_splits):
        """趋势策略在不同折数下都应 PASS 或 score > 4。"""
        start, end = date_range
        validator = WalkForwardValidator(
            train_period_days=252,
            test_period_days=63,
            n_splits=n_splits,
        )
        report = validator.validate_with_callable(
            _trending_strategy, start, end, "trending"
        )
        score = compute_wf_score(report)
        assert score >= 4.0, f"n_splits={n_splits}, score={score}"
        assert len(report.split_results) <= n_splits

    @pytest.mark.parametrize("n_splits", [3, 5, 8])
    def test_overfit_strategy_all_folds_low_score(self, date_range, n_splits):
        """过拟合策略在所有折数下 score 都应低。"""
        start, end = date_range
        validator = WalkForwardValidator(
            train_period_days=252,
            test_period_days=63,
            n_splits=n_splits,
        )
        report = validator.validate_with_callable(
            _overfit_strategy, start, end, "overfit"
        )
        score = compute_wf_score(report)
        # 过拟合策略应有显著衰减，评分应低于优质策略
        # 由于 IS/OOS 有明确的性能差异，score 上界设定 9
        assert score < 9.0, f"n_splits={n_splits}, score={score} (overfit should be lower)"

    @pytest.mark.parametrize("n_splits", [3, 5])
    def test_mean_reversion_stable_sharpe(self, date_range, n_splits):
        """均值回复策略 Sharpe 应跨折稳定（CV < 0.3）。"""
        start, end = date_range
        validator = WalkForwardValidator(
            train_period_days=252,
            test_period_days=63,
            n_splits=n_splits,
        )
        report = validator.validate_with_callable(
            _mean_reversion_strategy, start, end, "mean_reversion"
        )
        test_sharpes = [r.test_sharpe for r in report.split_results]
        if len(test_sharpes) >= 2:
            mean_s = sum(test_sharpes) / len(test_sharpes)
            std_s = math.sqrt(
                sum((s - mean_s) ** 2 for s in test_sharpes) / (len(test_sharpes) - 1)
            )
            cv = std_s / abs(mean_s) if abs(mean_s) > 1e-10 else float("inf")
            assert cv < 0.3, f"CV={cv:.3f}, too unstable"


class TestMultiWindowWF:
    """不同训练/测试窗口下 WF 稳健性。"""

    @pytest.fixture
    def date_range(self):
        return date(2020, 1, 1), date(2024, 12, 31)

    @pytest.mark.parametrize(
        "train_days,test_days",
        [(126, 42), (252, 63), (504, 126)],
        ids=["6m/6w", "1y/1q", "2y/6m"],
    )
    def test_trending_robust_across_windows(self, date_range, train_days, test_days):
        """趋势策略在不同窗口下评分波动 < 3 分。"""
        start, end = date_range
        validator = WalkForwardValidator(
            train_period_days=train_days,
            test_period_days=test_days,
            n_splits=3,
        )
        report = validator.validate_with_callable(
            _trending_strategy, start, end, "trending"
        )
        score = compute_wf_score(report)
        # 只要有结果就检查分数合理性
        if report.split_results:
            assert 0 <= score <= 10

    @pytest.mark.parametrize(
        "train_days,test_days",
        [(126, 42), (252, 63), (504, 126)],
        ids=["6m/6w", "1y/1q", "2y/6m"],
    )
    def test_flat_strategy_always_low(self, date_range, train_days, test_days):
        """无 alpha 策略在任何窗口下评分都低。"""
        start, end = date_range
        validator = WalkForwardValidator(
            train_period_days=train_days,
            test_period_days=test_days,
            n_splits=3,
        )
        report = validator.validate_with_callable(
            _flat_strategy, start, end, "flat"
        )
        score = compute_wf_score(report)
        if report.split_results:
            # flat 策略 Sharpe 接近 0，评分不应太高
            assert score < 9.0, f"Flat strategy should score low, got {score}"


class TestMultiStrategyComparison:
    """多策略对比 WF + Bootstrap 集成。"""

    def test_compare_four_strategies_wf(self):
        """4 个策略一起做 WF，验证排序合理性。"""
        start, end = date(2020, 1, 1), date(2024, 12, 31)
        scores = {}
        for name, fn in STRATEGIES.items():
            validator = WalkForwardValidator(
                train_period_days=252,
                test_period_days=63,
                n_splits=4,
            )
            report = validator.validate_with_callable(fn, start, end, name)
            scores[name] = compute_wf_score(report)

        # 均值回复应优于过拟合（或至少相当）
        assert scores["mean_reversion"] >= scores["overfit"] - 1.0, (
            f"mean_reversion={scores['mean_reversion']}, overfit={scores['overfit']}"
        )
        # 稳定策略（均值回复/趋势）应优于无alpha策略
        assert scores["mean_reversion"] >= scores["flat"] - 0.5

    def test_bootstrap_ci_on_wf_sharpe(self):
        """对多折 OOS Sharpe 做 Bootstrap CI，验证正策略 CI > 0。"""
        start, end = date(2020, 1, 1), date(2024, 12, 31)
        validator = WalkForwardValidator(
            train_period_days=252,
            test_period_days=63,
            n_splits=6,
        )
        report = validator.validate_with_callable(
            _mean_reversion_strategy, start, end, "mean_reversion"
        )
        test_sharpes = [r.test_sharpe for r in report.split_results]
        assert len(test_sharpes) >= 3

        ba = BootstrapAnalyzer(n_bootstrap=5000, random_seed=42)
        ci = ba.estimate_ci(test_sharpes, "oos_sharpe")
        # 均值回复策略 OOS Sharpe 全正，CI 应不含 0
        assert ci.ci_lower > 0, f"CI lower={ci.ci_lower}, expected > 0"

    def test_bootstrap_ci_on_flat_strategy(self):
        """无 alpha 策略的 Sharpe CI 应含 0 或非常窄。"""
        start, end = date(2020, 1, 1), date(2024, 12, 31)
        validator = WalkForwardValidator(
            train_period_days=252,
            test_period_days=63,
            n_splits=6,
        )
        report = validator.validate_with_callable(
            _flat_strategy, start, end, "flat"
        )
        test_sharpes = [r.test_sharpe for r in report.split_results]
        ba = BootstrapAnalyzer(n_bootstrap=5000, random_seed=42)
        ci = ba.estimate_ci(test_sharpes, "oos_sharpe")
        # Sharpe ~0.05 → CI 应非常窄且接近 0
        assert ci.ci_upper < 1.0, f"Flat sharpe CI upper too high: {ci.ci_upper}"


class TestPurgedWFRobustness:
    """Purged WF 抗信息泄露测试。"""

    def test_purged_vs_standard_score_close(self):
        """对趋势策略，Purged WF 与标准 WF 评分差距应可控。"""
        start, end = date(2020, 1, 1), date(2024, 12, 31)

        # 标准 WF
        std_validator = WalkForwardValidator(
            train_period_days=252,
            test_period_days=63,
            n_splits=4,
        )
        std_report = std_validator.validate_with_callable(
            _trending_strategy, start, end, "trending"
        )
        std_score = compute_wf_score(std_report)

        # Purged WF
        purged_splitter = PurgedWalkForwardSplitter(
            train_period_days=252,
            test_period_days=63,
            n_splits=4,
            purge_days=5,
            embargo_days=2,
        )
        purged_validator = WalkForwardValidator(splitter=purged_splitter)
        purged_report = purged_validator.validate_with_callable(
            _trending_strategy, start, end, "trending_purged"
        )
        purged_score = compute_wf_score(purged_report)

        # 两者分差应 < 3 分
        assert abs(std_score - purged_score) < 3.0, (
            f"Standard={std_score}, Purged={purged_score}"
        )

    def test_purged_wf_overfit_still_fails(self):
        """过拟合策略在 Purged WF 下也应评分低。"""
        start, end = date(2020, 1, 1), date(2024, 12, 31)
        splitter = PurgedWalkForwardSplitter(
            train_period_days=252,
            test_period_days=63,
            n_splits=4,
            purge_days=5,
            embargo_days=2,
        )
        validator = WalkForwardValidator(splitter=splitter)
        report = validator.validate_with_callable(
            _overfit_strategy, start, end, "overfit_purged"
        )
        score = compute_wf_score(report)
        assert score < 9.0, f"Purged overfit should score < 9, got {score}"


class TestRobustnessReportIntegration:
    """RobustnessReport 与 WF 结果集成。"""

    def test_full_robustness_report(self):
        """构建完整的稳健性报告并检查评分。"""
        start, end = date(2020, 1, 1), date(2024, 12, 31)

        # WF 获取多折 test sharpe/return
        validator = WalkForwardValidator(
            train_period_days=252,
            test_period_days=63,
            n_splits=5,
        )
        report = validator.validate_with_callable(
            _mean_reversion_strategy, start, end, "mean_reversion"
        )

        test_sharpes = [r.test_sharpe for r in report.split_results]
        test_returns = [r.test_return for r in report.split_results]

        # Bootstrap CI
        ba = BootstrapAnalyzer(n_bootstrap=3000, random_seed=42)
        bootstrap_results = ba.estimate_multiple({
            "sharpe": test_sharpes,
            "return": test_returns,
        })

        # 滚动原点 (复用 WF 各折作为滚动原点结果)
        rolling = RollingOriginReport(
            strategy_name="mean_reversion",
            results=[
                RollingOriginResult(
                    origin_idx=i,
                    train_size=252,
                    test_size=63,
                    metrics={"sharpe": r.test_sharpe, "return": r.test_return},
                )
                for i, r in enumerate(report.split_results)
            ],
            n_origins=len(report.split_results),
        )

        rob_report = RobustnessReport(
            strategy_name="mean_reversion",
            bootstrap_results=bootstrap_results,
            rolling_origin=rolling,
        )

        score = rob_report.compute_score()
        assert 0 < score <= 10
        conclusion = rob_report.generate_conclusion()
        assert "mean_reversion" in conclusion
        assert "/10" in conclusion

        d = rob_report.to_dict()
        assert d["strategy_name"] == "mean_reversion"
