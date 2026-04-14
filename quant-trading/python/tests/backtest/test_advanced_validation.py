"""
高级验证工具测试

覆盖：
1. DeflatedSharpeRatio — 基本计算、多重检验惩罚、边界
2. MonteCarloPermutationTest — 基本排列、显著/不显著、自定义指标
3. DSRResult/PermutationTestResult — to_dict
"""

import math

import pytest

from aipha.backtest.advanced_validation import (
    DSRResult,
    DeflatedSharpeRatio,
    MonteCarloPermutationTest,
    PermutationTestResult,
)


# ============================================================================
# Helpers
# ============================================================================


def _random_returns(n: int, mean: float = 0.0, std: float = 0.01,
                    seed: int = 42) -> list[float]:
    import random
    rng = random.Random(seed)
    return [mean + rng.gauss(0, std) for _ in range(n)]


# ============================================================================
# DeflatedSharpeRatio
# ============================================================================


class TestDeflatedSharpeRatio:
    """Deflated Sharpe Ratio 测试"""

    def test_single_trial_no_penalty(self):
        """n_trials=1 → 无 haircut"""
        dsr = DeflatedSharpeRatio()
        result = dsr.compute(
            observed_sharpe=1.5,
            n_trials=1,
            trading_days=252,
        )
        assert result.n_trials == 1
        assert result.expected_max_sharpe == 0.0
        assert result.haircut_sharpe == pytest.approx(1.5)

    def test_many_trials_increases_threshold(self):
        """更多尝试 → 更高期望最大 Sharpe → p_value 更大"""
        dsr = DeflatedSharpeRatio()
        r1 = dsr.compute(observed_sharpe=1.5, n_trials=10, trading_days=252)
        r2 = dsr.compute(observed_sharpe=1.5, n_trials=100, trading_days=252)
        r3 = dsr.compute(observed_sharpe=1.5, n_trials=1000, trading_days=252)
        assert r1.expected_max_sharpe < r2.expected_max_sharpe
        assert r2.expected_max_sharpe < r3.expected_max_sharpe
        # 更多尝试 → p_value 更大（更难显著）
        assert r1.p_value < r2.p_value
        assert r2.p_value < r3.p_value

    def test_high_sharpe_survives(self):
        """非常高的 Sharpe 即使多重检验也显著"""
        dsr = DeflatedSharpeRatio(significance_level=0.05)
        rets = _random_returns(500, mean=0.002, std=0.005)
        result = dsr.compute(
            observed_sharpe=3.0,
            n_trials=100,
            trading_days=500,
            daily_returns=rets,
        )
        assert result.haircut_sharpe > 0

    def test_mediocre_sharpe_fails(self):
        """中等 Sharpe + 大量尝试 → haircut 后接近 0"""
        dsr = DeflatedSharpeRatio()
        result = dsr.compute(
            observed_sharpe=0.5,
            n_trials=1000,
            trading_days=252,
        )
        assert result.haircut_sharpe < 0.5  # 被 haircut 了
        assert result.expected_max_sharpe > 0.5  # 期望最大超过观察值

    def test_with_daily_returns(self):
        """提供日收益率 → 自动计算偏度/峰度"""
        dsr = DeflatedSharpeRatio()
        rets = _random_returns(252, mean=0.0005, std=0.01)
        result = dsr.compute(
            observed_sharpe=1.2,
            n_trials=50,
            trading_days=252,
            daily_returns=rets,
        )
        assert 0 <= result.p_value <= 1
        assert result.deflated_sharpe == pytest.approx(1 - result.p_value)
        assert result.n_trials == 50

    def test_expected_max_sharpe_monotonic(self):
        """期望最大 Sharpe 随 n_trials 单调递增"""
        prev = 0.0
        for n in [1, 5, 10, 50, 100, 500, 1000]:
            v = DeflatedSharpeRatio.expected_max_sharpe(n, 252)
            assert v >= prev
            prev = v

    def test_to_dict(self):
        dsr = DeflatedSharpeRatio()
        result = dsr.compute(observed_sharpe=1.5, n_trials=50, trading_days=252)
        d = result.to_dict()
        assert "observed_sharpe" in d
        assert "p_value" in d
        assert "haircut_sharpe" in d
        assert "is_significant" in d

    def test_invalid_significance_level(self):
        with pytest.raises(ValueError, match="significance_level"):
            DeflatedSharpeRatio(significance_level=0)
        with pytest.raises(ValueError):
            DeflatedSharpeRatio(significance_level=1)

    def test_zero_trials_safe(self):
        """n_trials=0 → 安全处理"""
        dsr = DeflatedSharpeRatio()
        result = dsr.compute(observed_sharpe=1.0, n_trials=0, trading_days=252)
        assert math.isfinite(result.haircut_sharpe)

    def test_p_value_range(self):
        """p_value 应在 [0, 1]"""
        dsr = DeflatedSharpeRatio()
        for sharpe in [0.0, 0.5, 1.0, 2.0, 5.0]:
            result = dsr.compute(
                observed_sharpe=sharpe, n_trials=50, trading_days=252,
            )
            assert 0 <= result.p_value <= 1


# ============================================================================
# MonteCarloPermutationTest
# ============================================================================


class TestMonteCarloPermutationTest:
    """蒙特卡洛排列检验测试"""

    def test_basic_runs_correctly(self):
        """排列检验基本运行正确"""
        rets = _random_returns(250, mean=0.003, std=0.005, seed=42)
        test = MonteCarloPermutationTest(n_permutations=200, random_seed=42)
        result = test.test(rets)
        assert result.metric_name == "sharpe_ratio"
        assert result.observed_metric > 0
        assert 0 <= result.p_value <= 1
        assert result.n_permutations == 200

    def test_zero_signal_not_significant(self):
        """零均值随机收益 → p 值应不太小"""
        rets = _random_returns(250, mean=0.0, std=0.01, seed=123)
        test = MonteCarloPermutationTest(n_permutations=500, random_seed=42)
        result = test.test(rets)
        assert result.p_value > 0.05  # 不应非常显著

    def test_short_series(self):
        """太短的序列 → 安全退出"""
        test = MonteCarloPermutationTest()
        result = test.test([0.01, -0.01])
        assert result.p_value == 1.0
        assert result.is_significant is False

    def test_custom_metric(self):
        """自定义指标函数"""
        rets = _random_returns(100, mean=0.001, std=0.01)

        def max_drawdown(returns):
            nav = 1.0
            peak = 1.0
            max_dd = 0.0
            for r in returns:
                nav *= (1 + r)
                if nav > peak:
                    peak = nav
                dd = (peak - nav) / peak
                if dd > max_dd:
                    max_dd = dd
            return -max_dd  # 负号：越小越好，排列检验里 more_better

        test = MonteCarloPermutationTest(n_permutations=200, random_seed=42)
        result = test.test(rets, metric_fn=max_drawdown, metric_name="max_drawdown")
        assert result.metric_name == "max_drawdown"
        assert result.n_permutations == 200

    def test_reproducible(self):
        """相同种子 → 结果可复现"""
        rets = _random_returns(100, mean=0.001, std=0.01)
        t1 = MonteCarloPermutationTest(n_permutations=200, random_seed=42)
        t2 = MonteCarloPermutationTest(n_permutations=200, random_seed=42)
        r1 = t1.test(rets)
        r2 = t2.test(rets)
        assert r1.p_value == r2.p_value
        assert r1.observed_metric == r2.observed_metric

    def test_to_dict(self):
        rets = _random_returns(100, mean=0.001, std=0.01)
        test = MonteCarloPermutationTest(n_permutations=50, random_seed=42)
        result = test.test(rets)
        d = result.to_dict()
        assert "observed_metric" in d
        assert "p_value" in d
        assert "percentile" in d

    def test_percentile_range(self):
        """percentile 应在 [0, 1]"""
        rets = _random_returns(100, mean=0.001, std=0.01)
        test = MonteCarloPermutationTest(n_permutations=100, random_seed=42)
        result = test.test(rets)
        assert 0 <= result.percentile <= 1
