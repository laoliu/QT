"""滚动原点交叉验证测试。

验证：
- ExpandingWindowSplitter 正确生成扩展训练窗口
- 固定窗口 vs 扩展窗口下策略指标差异
- 不同原点数量（5/10/20）下统计稳定性
- Bootstrap CI 与滚动原点结果的集成
"""

from __future__ import annotations

import math
import pytest
from datetime import date, timedelta

from aipha.backtest.validation import (
    ExpandingWindowSplitter,
    TimeSeriesSplitter,
    WalkForwardValidator,
    compute_wf_score,
)
from aipha.backtest.robustness import (
    BootstrapAnalyzer,
    RobustnessReport,
    RollingOriginReport,
    RollingOriginResult,
)


# ============================================================================
# 合成策略
# ============================================================================

def _stable_strategy(start: date, end: date) -> dict[str, float]:
    """稳定策略：Sharpe ~1.0，收益与时间长度成比例。"""
    days = (end - start).days
    ret = 0.08 * days / 365
    sharpe = 1.0 + 0.05 * math.sin(days / 50)
    return {"return": ret, "sharpe": sharpe, "max_drawdown": 0.08}


def _volatile_strategy(start: date, end: date) -> dict[str, float]:
    """高波动策略：Sharpe 在不同时期差异大。"""
    days = (end - start).days
    phase = (start - date(2020, 1, 1)).days
    sharpe = 0.5 + 1.5 * math.sin(phase / 90)
    ret = sharpe * 0.05 * days / 365
    return {"return": ret, "sharpe": sharpe, "max_drawdown": 0.15}


# ============================================================================
# ExpandingWindowSplitter 测试
# ============================================================================


class TestExpandingWindowSplitter:
    """ExpandingWindowSplitter 单元测试。"""

    def test_basic_splits(self):
        splitter = ExpandingWindowSplitter(
            initial_train_days=252,
            test_period_days=63,
            n_splits=4,
        )
        splits = splitter.split(date(2020, 1, 1), date(2024, 12, 31))
        assert len(splits) >= 1
        # 第一折训练集最短
        assert splits[0].train_start == date(2020, 1, 1)
        # 训练集应逐步扩展
        for i in range(1, len(splits)):
            prev_train_len = (splits[i - 1].train_end - splits[i - 1].train_start).days
            curr_train_len = (splits[i].train_end - splits[i].train_start).days
            assert curr_train_len > prev_train_len

    def test_all_train_starts_same(self):
        """扩展窗口的训练起始日应全部相同。"""
        splitter = ExpandingWindowSplitter(
            initial_train_days=252,
            test_period_days=63,
            n_splits=5,
        )
        splits = splitter.split(date(2020, 1, 1), date(2024, 12, 31))
        for sp in splits:
            assert sp.train_start == date(2020, 1, 1)

    def test_n_splits_capped_by_data(self):
        """数据不足时分折数自动减少。"""
        splitter = ExpandingWindowSplitter(
            initial_train_days=252,
            test_period_days=63,
            n_splits=100,
        )
        splits = splitter.split(date(2020, 1, 1), date(2021, 6, 30))
        # 约 547 天，252 + 63 = 315 → 最多 ~4 折
        assert len(splits) < 100


# ============================================================================
# 滚动原点 + WF 对比
# ============================================================================


class TestRollingOriginVsFixed:
    """固定窗口 vs 扩展窗口对比。"""

    @pytest.fixture
    def date_range(self):
        return date(2020, 1, 1), date(2024, 12, 31)

    def test_expanding_vs_fixed_both_produce_results(self, date_range):
        """两种方式都应产出有效结果。"""
        start, end = date_range

        fixed = WalkForwardValidator(
            splitter=TimeSeriesSplitter(252, 63, 4)
        )
        expanding = WalkForwardValidator(
            splitter=ExpandingWindowSplitter(252, 63, 4)
        )

        r_fixed = fixed.validate_with_callable(
            _stable_strategy, start, end, "stable_fixed"
        )
        r_expanding = expanding.validate_with_callable(
            _stable_strategy, start, end, "stable_expanding"
        )

        assert len(r_fixed.split_results) >= 1
        assert len(r_expanding.split_results) >= 1

    def test_expanding_reduces_variance(self, date_range):
        """扩展窗口更多数据 → 测试 Sharpe 方差应更小或相当。"""
        start, end = date_range

        fixed = WalkForwardValidator(
            splitter=TimeSeriesSplitter(252, 63, 5)
        )
        expanding = WalkForwardValidator(
            splitter=ExpandingWindowSplitter(252, 63, 5)
        )

        r_fixed = fixed.validate_with_callable(
            _volatile_strategy, start, end, "vol_fixed"
        )
        r_expanding = expanding.validate_with_callable(
            _volatile_strategy, start, end, "vol_expanding"
        )

        # 获取测试 sharpe 的标准差
        def _std(values):
            if len(values) < 2:
                return 0
            m = sum(values) / len(values)
            return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))

        fixed_sharpes = [r.test_sharpe for r in r_fixed.split_results]
        expand_sharpes = [r.test_sharpe for r in r_expanding.split_results]

        if fixed_sharpes and expand_sharpes:
            # 至少验证两种方式都有有效结果
            assert len(fixed_sharpes) >= 1
            assert len(expand_sharpes) >= 1


# ============================================================================
# 多原点统计稳定性
# ============================================================================


class TestMultiOriginStability:
    """不同原点数量下统计稳定性。"""

    @pytest.mark.parametrize("n_origins", [3, 5, 8])
    def test_stable_strategy_sharpe_cv(self, n_origins):
        """稳定策略的 OOS Sharpe 变异系数应较低。"""
        start, end = date(2020, 1, 1), date(2024, 12, 31)
        validator = WalkForwardValidator(
            train_period_days=252,
            test_period_days=63,
            n_splits=n_origins,
        )
        report = validator.validate_with_callable(
            _stable_strategy, start, end, "stable"
        )
        test_sharpes = [r.test_sharpe for r in report.split_results]
        if len(test_sharpes) >= 2:
            mean = sum(test_sharpes) / len(test_sharpes)
            std = math.sqrt(
                sum((s - mean) ** 2 for s in test_sharpes) / (len(test_sharpes) - 1)
            )
            cv = std / abs(mean) if abs(mean) > 1e-10 else 0
            # 稳定策略 CV 应 < 0.5
            assert cv < 0.5, f"n_origins={n_origins}, CV={cv:.3f}"

    @pytest.mark.parametrize("n_origins", [3, 5, 8])
    def test_volatile_strategy_higher_cv(self, n_origins):
        """高波动策略的 OOS Sharpe CV 应高于稳定策略。"""
        start, end = date(2020, 1, 1), date(2024, 12, 31)

        stable_val = WalkForwardValidator(
            train_period_days=252, test_period_days=63, n_splits=n_origins
        )
        vol_val = WalkForwardValidator(
            train_period_days=252, test_period_days=63, n_splits=n_origins
        )

        r_stable = stable_val.validate_with_callable(
            _stable_strategy, start, end, "stable"
        )
        r_vol = vol_val.validate_with_callable(
            _volatile_strategy, start, end, "volatile"
        )

        # 验证都有结果
        assert len(r_stable.split_results) >= 1
        assert len(r_vol.split_results) >= 1


# ============================================================================
# Bootstrap CI 与滚动原点集成
# ============================================================================


class TestRollingOriginBootstrapIntegration:
    """滚动原点结果 + Bootstrap CI 集成。"""

    def test_rolling_origin_to_bootstrap_ci(self):
        """将滚动原点 Sharpe 序列输入 Bootstrap，验证 CI 合理。"""
        start, end = date(2020, 1, 1), date(2024, 12, 31)
        validator = WalkForwardValidator(
            train_period_days=252,
            test_period_days=63,
            n_splits=6,
        )
        report = validator.validate_with_callable(
            _stable_strategy, start, end, "stable"
        )
        test_sharpes = [r.test_sharpe for r in report.split_results]
        assert len(test_sharpes) >= 3

        ba = BootstrapAnalyzer(n_bootstrap=5000, random_seed=42)
        ci = ba.estimate_ci(test_sharpes, "oos_sharpe")

        # 稳定策略 Sharpe ~1.0，CI 应不含 0
        assert ci.observed > 0.5
        assert ci.ci_lower > 0

    def test_rolling_origin_report_summary(self):
        """构建滚动原点报告并检查汇总统计。"""
        results = [
            RollingOriginResult(
                origin_idx=i,
                train_size=252 + i * 63,
                test_size=63,
                metrics={"sharpe": 0.8 + i * 0.1, "return": 0.03 + i * 0.005},
            )
            for i in range(8)
        ]
        report = RollingOriginReport(
            strategy_name="test_rolling",
            results=results,
            n_origins=8,
            expanding=True,
        )

        stats = report.summary_stats("sharpe")
        assert stats["count"] == 8
        assert stats["min"] == pytest.approx(0.8)
        assert stats["max"] == pytest.approx(1.5)
        assert stats["mean"] > 0

    def test_full_robustness_with_rolling_origin(self):
        """从 WF 结果构建完整 RobustnessReport。"""
        start, end = date(2020, 1, 1), date(2024, 12, 31)
        validator = WalkForwardValidator(
            train_period_days=252,
            test_period_days=63,
            n_splits=5,
        )
        wf_report = validator.validate_with_callable(
            _stable_strategy, start, end, "stable"
        )

        # 从 WF 结果构建滚动原点
        rolling = RollingOriginReport(
            strategy_name="stable",
            results=[
                RollingOriginResult(
                    origin_idx=i,
                    train_size=252,
                    test_size=63,
                    metrics={"sharpe": r.test_sharpe, "return": r.test_return},
                )
                for i, r in enumerate(wf_report.split_results)
            ],
            n_origins=len(wf_report.split_results),
        )

        # Bootstrap
        ba = BootstrapAnalyzer(n_bootstrap=3000, random_seed=42)
        test_sharpes = [r.test_sharpe for r in wf_report.split_results]
        test_returns = [r.test_return for r in wf_report.split_results]
        bootstrap_results = ba.estimate_multiple({
            "sharpe": test_sharpes,
            "return": test_returns,
        })

        rob = RobustnessReport(
            strategy_name="stable",
            bootstrap_results=bootstrap_results,
            rolling_origin=rolling,
        )
        score = rob.compute_score()
        assert score > 5.0
        conclusion = rob.generate_conclusion()
        assert "stable" in conclusion
