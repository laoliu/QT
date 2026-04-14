"""凸优化组合优化器测试"""

import numpy as np
import pytest

from aipha.portfolio.covariance import SampleCovarianceEstimator
from aipha.portfolio.convex_optimizer import (
    CVXPY_AVAILABLE,
    BlackLittermanOptimizer,
    ConvexMaxSharpeOptimizer,
    ConvexMinVarianceOptimizer,
    ConvexRiskParityOptimizer,
    MeanVarianceOptimizer,
    OptimizationConstraints,
    create_optimizer,
)


# 测试数据
@pytest.fixture
def sample_returns():
    """样本收益率数据"""
    np.random.seed(42)
    n_periods = 100
    return {
        "A": list(np.random.normal(0.001, 0.02, n_periods)),
        "B": list(np.random.normal(0.0005, 0.015, n_periods)),
        "C": list(np.random.normal(0.0015, 0.025, n_periods)),
    }


@pytest.fixture
def correlated_returns():
    """相关收益率数据"""
    np.random.seed(42)
    n_periods = 100
    base = np.random.normal(0, 0.01, n_periods)
    return {
        "A": list(base + np.random.normal(0, 0.005, n_periods)),
        "B": list(base + np.random.normal(0, 0.005, n_periods)),
        "C": list(-base + np.random.normal(0, 0.005, n_periods)),  # 负相关
    }


class TestConvexMinVarianceOptimizer:
    """最小方差优化器测试"""

    def test_basic_optimization(self, sample_returns):
        """测试基本优化"""
        optimizer = ConvexMinVarianceOptimizer()
        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )

        assert len(weights) == 3
        assert sum(weights.values()) == pytest.approx(1.0)
        assert all(w >= 0 for w in weights.values())

    def test_two_assets(self, sample_returns):
        """测试双资产"""
        optimizer = ConvexMinVarianceOptimizer()
        weights = optimizer.optimize(
            symbols=["A", "B"],
            returns={k: sample_returns[k] for k in ["A", "B"]},
        )

        assert len(weights) == 2
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_fallback_on_missing_data(self):
        """测试数据缺失时回退"""
        optimizer = ConvexMinVarianceOptimizer()
        weights = optimizer.optimize(
            symbols=["A", "B"],
            returns=None,
        )

        # 应回退到等权
        assert weights == {"A": 0.5, "B": 0.5}

    def test_min_variance_beats_equal_weight(self, sample_returns):
        """测试最小方差组合波动率低于等权"""
        optimizer = ConvexMinVarianceOptimizer()
        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )

        # 计算组合波动率
        from aipha.portfolio.optimizer import calculate_portfolio_metrics

        mv_metrics = calculate_portfolio_metrics(weights, sample_returns)

        # 等权组合
        equal_weights = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
        eq_metrics = calculate_portfolio_metrics(equal_weights, sample_returns)

        # 最小方差组合波动率应 <= 等权（允许误差）
        assert mv_metrics["volatility"] <= eq_metrics["volatility"] + 0.01

    @pytest.mark.skipif(not CVXPY_AVAILABLE, reason="需要 cvxpy 进行约束优化")
    def test_with_constraints(self, sample_returns):
        """测试带约束优化"""
        constraints = OptimizationConstraints(
            min_weight=0.1,
            max_weight=0.5,
        )
        optimizer = ConvexMinVarianceOptimizer(constraints=constraints)
        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )

        assert len(weights) == 3
        assert sum(weights.values()) == pytest.approx(1.0)
        # 应满足约束
        for w in weights.values():
            assert w >= 0.1 - 1e-6
            assert w <= 0.5 + 1e-6


class TestTurnoverConstraint:
    """换手率约束测试"""

    @pytest.mark.skipif(not CVXPY_AVAILABLE, reason="需要 cvxpy")
    def test_turnover_limits_change(self, sample_returns):
        """换手率约束应限制权重变动"""
        prev_weights = {"A": 0.6, "B": 0.3, "C": 0.1}
        constraints = OptimizationConstraints(max_turnover=0.1)
        optimizer = ConvexMinVarianceOptimizer(constraints=constraints)

        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
            prev_weights=prev_weights,
        )

        # L1 norm of change <= max_turnover * 2
        total_change = sum(abs(weights[s] - prev_weights[s]) for s in weights)
        assert total_change <= 0.1 * 2 + 1e-4  # 允许数值误差
        assert sum(weights.values()) == pytest.approx(1.0)

    @pytest.mark.skipif(not CVXPY_AVAILABLE, reason="需要 cvxpy")
    def test_tight_turnover_preserves_weights(self, sample_returns):
        """极低换手率约束应使权重几乎不变"""
        prev_weights = {"A": 0.4, "B": 0.35, "C": 0.25}
        constraints = OptimizationConstraints(max_turnover=0.01)
        optimizer = ConvexMinVarianceOptimizer(constraints=constraints)

        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
            prev_weights=prev_weights,
        )

        # 权重变化极小
        for s in weights:
            assert abs(weights[s] - prev_weights[s]) < 0.02 + 1e-4

    @pytest.mark.skipif(not CVXPY_AVAILABLE, reason="需要 cvxpy")
    def test_no_turnover_constraint_without_prev_weights(self, sample_returns):
        """无 prev_weights 时换手率约束不生效"""
        constraints = OptimizationConstraints(max_turnover=0.05)
        optimizer = ConvexMinVarianceOptimizer(constraints=constraints)

        # 不传 prev_weights，应正常优化
        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )

        assert len(weights) == 3
        assert sum(weights.values()) == pytest.approx(1.0)

    @pytest.mark.skipif(not CVXPY_AVAILABLE, reason="需要 cvxpy")
    def test_large_turnover_matches_unconstrained(self, sample_returns):
        """高换手率约束应与无约束结果一致"""
        unconstrained = ConvexMinVarianceOptimizer()
        weights_free = unconstrained.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )

        prev_weights = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
        constrained = ConvexMinVarianceOptimizer(
            constraints=OptimizationConstraints(max_turnover=2.0)
        )
        weights_loose = constrained.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
            prev_weights=prev_weights,
        )

        for s in weights_free:
            assert weights_free[s] == pytest.approx(weights_loose[s], abs=1e-3)

    @pytest.mark.skipif(not CVXPY_AVAILABLE, reason="需要 cvxpy")
    def test_turnover_with_zero_prev_weights(self, sample_returns):
        """从零持仓开始应正常工作"""
        prev_weights = {"A": 0.0, "B": 0.0, "C": 0.0}
        constraints = OptimizationConstraints(max_turnover=0.5)
        optimizer = ConvexMinVarianceOptimizer(constraints=constraints)

        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
            prev_weights=prev_weights,
        )

        total_change = sum(abs(weights[s]) for s in weights)
        assert total_change <= 1.0 + 1e-4
        assert sum(weights.values()) == pytest.approx(1.0)


class TestSectorConstraint:
    """行业约束测试"""

    @pytest.mark.skipif(not CVXPY_AVAILABLE, reason="需要 cvxpy")
    def test_sector_limit_respected(self, sample_returns):
        """行业权重上限应被遵守"""
        sector_map = {"A": "tech", "B": "tech", "C": "finance"}
        constraints = OptimizationConstraints(
            sector_limits={"tech": 0.5}
        )
        optimizer = ConvexMinVarianceOptimizer(constraints=constraints)

        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
            sector_map=sector_map,
        )

        # tech = A + B <= 0.5
        tech_weight = weights["A"] + weights["B"]
        assert tech_weight <= 0.5 + 1e-4
        assert sum(weights.values()) == pytest.approx(1.0)

    @pytest.mark.skipif(not CVXPY_AVAILABLE, reason="需要 cvxpy")
    def test_multiple_sector_limits(self, sample_returns):
        """多行业约束"""
        sector_map = {"A": "tech", "B": "finance", "C": "finance"}
        constraints = OptimizationConstraints(
            sector_limits={"tech": 0.4, "finance": 0.7}
        )
        optimizer = ConvexMinVarianceOptimizer(constraints=constraints)

        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
            sector_map=sector_map,
        )

        assert weights["A"] <= 0.4 + 1e-4
        assert weights["B"] + weights["C"] <= 0.7 + 1e-4
        assert sum(weights.values()) == pytest.approx(1.0)

    @pytest.mark.skipif(not CVXPY_AVAILABLE, reason="需要 cvxpy")
    def test_no_sector_constraint_without_map(self, sample_returns):
        """无 sector_map 时行业约束不生效"""
        constraints = OptimizationConstraints(
            sector_limits={"tech": 0.3}
        )
        optimizer = ConvexMinVarianceOptimizer(constraints=constraints)

        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )

        assert len(weights) == 3
        assert sum(weights.values()) == pytest.approx(1.0)

    @pytest.mark.skipif(not CVXPY_AVAILABLE, reason="需要 cvxpy")
    def test_sector_limit_for_unlisted_sector(self, sample_returns):
        """对未出现在标的中的行业设限不影响结果"""
        sector_map = {"A": "tech", "B": "tech", "C": "tech"}
        constraints = OptimizationConstraints(
            sector_limits={"finance": 0.3}  # 无 finance 标的
        )
        optimizer = ConvexMinVarianceOptimizer(constraints=constraints)

        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
            sector_map=sector_map,
        )

        assert len(weights) == 3
        assert sum(weights.values()) == pytest.approx(1.0)

    @pytest.mark.skipif(not CVXPY_AVAILABLE, reason="需要 cvxpy")
    def test_combined_turnover_and_sector(self, sample_returns):
        """换手率 + 行业双重约束同时生效"""
        # prev tech = 0.35+0.25 = 0.6, 与 sector limit 兼容
        prev_weights = {"A": 0.35, "B": 0.25, "C": 0.4}
        sector_map = {"A": "tech", "B": "tech", "C": "finance"}
        constraints = OptimizationConstraints(
            max_turnover=0.3,
            sector_limits={"tech": 0.65},
        )
        optimizer = ConvexMinVarianceOptimizer(constraints=constraints)

        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
            prev_weights=prev_weights,
            sector_map=sector_map,
        )

        # 换手率约束
        total_change = sum(abs(weights[s] - prev_weights[s]) for s in weights)
        assert total_change <= 0.3 * 2 + 1e-4

        # 行业约束
        assert weights["A"] + weights["B"] <= 0.65 + 1e-4
        assert sum(weights.values()) == pytest.approx(1.0)


class TestConvexRiskParityOptimizer:
    """风险平价优化器测试"""

    def test_basic_risk_parity(self, sample_returns):
        """测试基本风险平价"""
        optimizer = ConvexRiskParityOptimizer()
        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )

        assert len(weights) == 3
        assert sum(weights.values()) == pytest.approx(1.0)
        assert all(w > 0 for w in weights.values())

    def test_risk_contributions_equal(self, sample_returns):
        """测试风险贡献应接近相等（需要 cvxpy 才能精确）"""
        optimizer = ConvexRiskParityOptimizer(max_iterations=1000, tolerance=1e-10)
        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )

        # 验证基本属性
        assert len(weights) == 3
        assert sum(weights.values()) == pytest.approx(1.0)
        assert all(w > 0 for w in weights.values())

        # 如果有 cvxpy，检查风险贡献更精确
        if CVXPY_AVAILABLE:
            cov_estimator = SampleCovarianceEstimator()
            cov_matrix = cov_estimator.estimate_from_dict(
                sample_returns, ["A", "B", "C"]
            )
            risk_contrib = optimizer.get_risk_contributions(weights, cov_matrix)
            contrib_values = list(risk_contrib.values())
            std_dev = np.std(contrib_values)
            assert std_dev < 0.3  # 有 cvxpy 时精度更高

    def test_low_vol_gets_higher_weight(self, sample_returns):
        """测试低波动资产获得更高权重"""
        optimizer = ConvexRiskParityOptimizer()
        weights = optimizer.optimize(
            symbols=["A", "B"],
            returns={
                "A": [0.001] * 50 + [0.002] * 50,  # 低波动
                "B": [0.01, -0.01] * 50,  # 高波动
            },
        )

        # 低波动资产权重应更高
        assert weights["A"] > weights["B"]


class TestConvexMaxSharpeOptimizer:
    """最大夏普比优化器测试"""

    def test_basic_max_sharpe(self, sample_returns):
        """测试基本最大夏普优化"""
        optimizer = ConvexMaxSharpeOptimizer(risk_free_rate=0.02)
        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )

        assert len(weights) == 3
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_prefers_high_sharpe_assets(self):
        """测试偏好高夏普资产"""
        # 构造数据：A 夏普高，B 夏普低
        returns = {
            "A": [0.003] * 50,  # 高收益低波动
            "B": [0.01, -0.01] * 25,  # 低收益高波动
        }
        optimizer = ConvexMaxSharpeOptimizer(risk_free_rate=0.0)
        weights = optimizer.optimize(
            symbols=["A", "B"],
            returns=returns,
        )

        # A 权重应更高
        assert weights["A"] > weights["B"]

    def test_fallback_on_negative_returns(self):
        """测试全负收益时回退"""
        returns = {
            "A": [-0.01] * 50,
            "B": [-0.02] * 50,
        }
        optimizer = ConvexMaxSharpeOptimizer(risk_free_rate=0.05)
        weights = optimizer.optimize(
            symbols=["A", "B"],
            returns=returns,
        )

        # 应有结果（回退到最小方差）
        assert len(weights) == 2
        assert sum(weights.values()) == pytest.approx(1.0)


class TestMeanVarianceOptimizer:
    """均值-方差优化器测试"""

    def test_basic_mean_variance(self, sample_returns):
        """测试基本均值-方差优化"""
        optimizer = MeanVarianceOptimizer(risk_aversion=2.0)
        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )

        assert len(weights) == 3
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_high_risk_aversion(self, sample_returns):
        """测试高风险厌恶"""
        optimizer_high = MeanVarianceOptimizer(risk_aversion=10.0)
        optimizer_low = MeanVarianceOptimizer(risk_aversion=0.5)

        weights_high = optimizer_high.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )
        weights_low = optimizer_low.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )

        # 高风险厌恶应更接近最小方差（更分散）
        from aipha.portfolio.optimizer import calculate_portfolio_metrics

        metrics_high = calculate_portfolio_metrics(weights_high, sample_returns)
        metrics_low = calculate_portfolio_metrics(weights_low, sample_returns)

        # 高风险厌恶波动率应 <= 低风险厌恶
        assert metrics_high["volatility"] <= metrics_low["volatility"] + 0.01

    def test_target_return_constraint(self, sample_returns):
        """测试目标收益约束"""
        optimizer = MeanVarianceOptimizer(target_return=0.10)  # 10% 年化
        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
        )

        assert len(weights) == 3
        assert sum(weights.values()) == pytest.approx(1.0)


class TestBlackLittermanOptimizer:
    """Black-Litterman 优化器测试"""

    def test_without_views(self, sample_returns):
        """测试无观点时使用市场权重"""
        optimizer = BlackLittermanOptimizer()
        market_caps = {"A": 1000, "B": 2000, "C": 3000}

        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
            market_caps=market_caps,
        )

        # 应接近市场权重
        assert weights["A"] == pytest.approx(1 / 6, abs=0.05)
        assert weights["B"] == pytest.approx(2 / 6, abs=0.05)
        assert weights["C"] == pytest.approx(3 / 6, abs=0.05)

    def test_with_views(self, sample_returns):
        """测试带观点"""
        # 观点：A 会涨 10%
        views = [
            (["A"], [1.0], 0.10, 1.0),  # A 预期收益 10%，置信度 1
        ]
        optimizer = BlackLittermanOptimizer(views=views)
        market_caps = {"A": 1000, "B": 1000, "C": 1000}

        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
            market_caps=market_caps,
        )

        # A 权重应高于市场权重
        assert weights["A"] > 1 / 3

    def test_fallback_on_missing_market_caps(self, sample_returns):
        """测试缺失市值时回退"""
        optimizer = BlackLittermanOptimizer()
        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            returns=sample_returns,
            market_caps=None,
        )

        # 应回退到等权
        assert weights == {"A": pytest.approx(1 / 3), "B": pytest.approx(1 / 3), "C": pytest.approx(1 / 3)}


class TestCreateOptimizer:
    """优化器工厂函数测试"""

    def test_create_equal_weight(self):
        """测试创建等权优化器"""
        optimizer = create_optimizer("equal")
        assert optimizer is not None

    def test_create_min_variance(self):
        """测试创建最小方差优化器"""
        optimizer = create_optimizer("min_variance")
        assert isinstance(optimizer, ConvexMinVarianceOptimizer)

    def test_create_risk_parity(self):
        """测试创建风险平价优化器"""
        optimizer = create_optimizer("risk_parity")
        assert isinstance(optimizer, ConvexRiskParityOptimizer)

    def test_create_max_sharpe(self):
        """测试创建最大夏普优化器"""
        optimizer = create_optimizer("max_sharpe")
        assert isinstance(optimizer, ConvexMaxSharpeOptimizer)

    def test_create_with_params(self):
        """测试带参数创建"""
        optimizer = create_optimizer("risk_parity", max_iterations=1000)
        assert optimizer.max_iterations == 1000

    def test_unknown_method_raises(self):
        """测试未知方法抛异常"""
        with pytest.raises(ValueError, match="未知优化方法"):
            create_optimizer("unknown_method")


class TestOptimizerIntegration:
    """优化器集成测试"""

    def test_all_optimizers_produce_valid_weights(self, sample_returns):
        """测试所有优化器产生有效权重"""
        market_caps = {"A": 1000, "B": 2000, "C": 3000}

        optimizers = [
            ConvexMinVarianceOptimizer(),
            ConvexRiskParityOptimizer(),
            ConvexMaxSharpeOptimizer(),
            MeanVarianceOptimizer(),
        ]

        for optimizer in optimizers:
            weights = optimizer.optimize(
                symbols=["A", "B", "C"],
                returns=sample_returns,
                market_caps=market_caps,
            )

            # 验证权重
            assert len(weights) == 3, f"{optimizer.__class__.__name__} 权重数量错误"
            assert sum(weights.values()) == pytest.approx(1.0), f"{optimizer.__class__.__name__} 权重和不为1"
            assert all(w >= -1e-6 for w in weights.values()), f"{optimizer.__class__.__name__} 权重为负"

    def test_optimizer_stability(self, sample_returns):
        """测试优化器稳定性（相同输入应得相同输出）"""
        optimizer = ConvexMinVarianceOptimizer()

        weights1 = optimizer.optimize(symbols=["A", "B", "C"], returns=sample_returns)
        weights2 = optimizer.optimize(symbols=["A", "B", "C"], returns=sample_returns)

        for symbol in weights1:
            assert weights1[symbol] == pytest.approx(weights2[symbol])


class TestCheckCvarConstraint:
    """CVaR 约束检查测试."""

    def test_cvar_pass(self):
        from aipha.portfolio.convex_optimizer import check_cvar_constraint
        import numpy as np

        np.random.seed(42)
        returns = {
            "A": list(np.random.normal(0.001, 0.01, 252)),
            "B": list(np.random.normal(0.001, 0.01, 252)),
        }
        result = check_cvar_constraint({"A": 0.5, "B": 0.5}, returns, max_cvar=0.10)
        assert result["pass"] is True
        assert result["cvar"] < 0.10

    def test_cvar_fail_tight_limit(self):
        from aipha.portfolio.convex_optimizer import check_cvar_constraint
        import numpy as np

        np.random.seed(42)
        returns = {
            "X": list(np.random.normal(0.0, 0.05, 252)),
        }
        result = check_cvar_constraint({"X": 1.0}, returns, max_cvar=0.001)
        assert result["pass"] is False

    def test_empty_portfolio(self):
        from aipha.portfolio.convex_optimizer import check_cvar_constraint

        result = check_cvar_constraint({}, {}, max_cvar=0.03)
        assert result["pass"] is True
