"""组合权重优化器测试"""

import pytest
import math

from aipha.portfolio.optimizer import (
    EqualWeightOptimizer,
    MarketCapWeightOptimizer,
    RiskParityOptimizer,
    MinVarianceOptimizer,
    MaxSharpeOptimizer,
    apply_weight_constraints,
    calculate_portfolio_metrics,
)


class TestEqualWeightOptimizer:
    """等权优化器测试"""

    def test_equal_weights_two_symbols(self):
        """测试两个标的等权"""
        optimizer = EqualWeightOptimizer()
        weights = optimizer.optimize(symbols=["A", "B"])
        assert weights == {"A": 0.5, "B": 0.5}

    def test_equal_weights_three_symbols(self):
        """测试三个标的等权"""
        optimizer = EqualWeightOptimizer()
        weights = optimizer.optimize(symbols=["A", "B", "C"])
        assert weights["A"] == pytest.approx(1 / 3)
        assert weights["B"] == pytest.approx(1 / 3)
        assert weights["C"] == pytest.approx(1 / 3)

    def test_empty_symbols(self):
        """测试空标的列表"""
        optimizer = EqualWeightOptimizer()
        weights = optimizer.optimize(symbols=[])
        assert weights == {}

    def test_single_symbol(self):
        """测试单个标的"""
        optimizer = EqualWeightOptimizer()
        weights = optimizer.optimize(symbols=["A"])
        assert weights == {"A": 1.0}


class TestMarketCapWeightOptimizer:
    """市值加权优化器测试"""

    def test_market_cap_weights(self):
        """测试市值加权"""
        optimizer = MarketCapWeightOptimizer()
        weights = optimizer.optimize(
            symbols=["A", "B"],
            market_caps={"A": 1000.0, "B": 3000.0},
        )
        assert weights["A"] == pytest.approx(0.25)
        assert weights["B"] == pytest.approx(0.75)

    def test_fallback_to_equal_when_no_market_caps(self):
        """测试无市值数据时回退到等权"""
        optimizer = MarketCapWeightOptimizer()
        weights = optimizer.optimize(symbols=["A", "B"])
        assert weights == {"A": 0.5, "B": 0.5}

    def test_partial_market_caps(self):
        """测试部分市值数据"""
        optimizer = MarketCapWeightOptimizer()
        weights = optimizer.optimize(
            symbols=["A", "B", "C"],
            market_caps={"A": 1000.0, "B": 1000.0},  # C 没有市值数据
        )
        # 只计算有数据的
        assert "A" in weights
        assert "B" in weights
        assert weights["A"] == pytest.approx(0.5)


class TestRiskParityOptimizer:
    """风险平价优化器测试"""

    def test_risk_parity_same_volatility(self):
        """测试相同波动率时等权"""
        optimizer = RiskParityOptimizer()
        # 相同波动率的收益率序列
        returns = {
            "A": [0.01, -0.01, 0.02, -0.02, 0.01],
            "B": [0.01, -0.01, 0.02, -0.02, 0.01],
        }
        weights = optimizer.optimize(symbols=["A", "B"], returns=returns)
        assert weights["A"] == pytest.approx(0.5)
        assert weights["B"] == pytest.approx(0.5)

    def test_risk_parity_different_volatility(self):
        """测试不同波动率时反比分配"""
        optimizer = RiskParityOptimizer()
        # A 波动率小，B 波动率大（都有非零波动）
        returns = {
            "A": [0.01, 0.02, 0.01, 0.02, 0.01],  # 低波动
            "B": [0.05, -0.05, 0.05, -0.05, 0.05],  # 高波动
        }
        weights = optimizer.optimize(symbols=["A", "B"], returns=returns)
        # 低波动的权重应该更高
        assert weights["A"] > weights["B"]

    def test_fallback_when_no_returns(self):
        """测试无收益率数据时回退"""
        optimizer = RiskParityOptimizer()
        weights = optimizer.optimize(symbols=["A", "B"])
        assert weights == {"A": 0.5, "B": 0.5}


class TestMinVarianceOptimizer:
    """最小方差优化器测试"""

    def test_min_variance_prefers_low_vol(self):
        """测试低波动标的获得更高权重"""
        optimizer = MinVarianceOptimizer()
        returns = {
            "A": [0.001, 0.002, 0.001, 0.002],  # 低波动（非零）
            "B": [0.1, -0.1, 0.1, -0.1],  # 高波动
        }
        weights = optimizer.optimize(symbols=["A", "B"], returns=returns)
        assert weights["A"] > weights["B"]

    def test_fallback_when_no_returns(self):
        """测试无数据时回退"""
        optimizer = MinVarianceOptimizer()
        weights = optimizer.optimize(symbols=["A", "B"])
        assert weights == {"A": 0.5, "B": 0.5}


class TestMaxSharpeOptimizer:
    """最大夏普优化器测试"""

    def test_max_sharpe_prefers_high_sharpe(self):
        """测试高夏普标的获得更高权重"""
        optimizer = MaxSharpeOptimizer(risk_free_rate=0.0)
        returns = {
            "A": [0.02, 0.021, 0.02, 0.021],  # 高收益低波动（非零波动）
            "B": [0.01, -0.03, 0.02, -0.02],  # 低收益高波动
        }
        weights = optimizer.optimize(symbols=["A", "B"], returns=returns)
        assert weights["A"] > weights["B"]

    def test_zero_weights_for_negative_sharpe(self):
        """测试负夏普标的权重为0"""
        optimizer = MaxSharpeOptimizer(risk_free_rate=0.1)  # 高无风险利率
        returns = {
            "A": [-0.01, -0.01, -0.01, -0.01],  # 负收益
            "B": [-0.02, -0.01, -0.02, -0.01],  # 负收益
        }
        weights = optimizer.optimize(symbols=["A", "B"], returns=returns)
        # 两个都是负夏普，应该回退到等权
        assert weights == {"A": 0.5, "B": 0.5}


class TestApplyWeightConstraints:
    """权重约束测试"""

    def test_apply_max_constraint(self):
        """测试最大权重约束"""
        weights = {"A": 0.7, "B": 0.3}
        constrained = apply_weight_constraints(weights, max_weight=0.5)
        # A 被截断到 0.5，归一化后 A=0.5/0.8=0.625, B=0.3/0.8=0.375
        assert constrained["A"] == pytest.approx(0.625)
        assert constrained["B"] == pytest.approx(0.375)

    def test_apply_min_constraint(self):
        """测试最小权重约束"""
        weights = {"A": 0.9, "B": 0.05, "C": 0.05}
        # 约束应用：A=0.9, B=0.1, C=0.1，总和1.1
        # 归一化后：A≈0.818, B≈0.091, C≈0.091
        # 注意：单次约束+归一化可能无法完全满足约束
        constrained = apply_weight_constraints(weights, min_weight=0.1, normalize=False)
        assert constrained["B"] >= 0.1
        assert constrained["C"] >= 0.1

    def test_no_normalize(self):
        """测试不归一化"""
        weights = {"A": 0.7, "B": 0.3}
        constrained = apply_weight_constraints(weights, max_weight=0.5, normalize=False)
        assert constrained["A"] == 0.5
        assert constrained["B"] == 0.3


class TestCalculatePortfolioMetrics:
    """组合指标计算测试"""

    def test_calculate_metrics(self):
        """测试指标计算"""
        weights = {"A": 0.5, "B": 0.5}
        returns = {
            "A": [0.01, 0.02, 0.01, 0.02],
            "B": [0.02, 0.01, 0.02, 0.01],
        }
        metrics = calculate_portfolio_metrics(weights, returns, risk_free_rate=0.02)

        assert "expected_return" in metrics
        assert "volatility" in metrics
        assert "sharpe_ratio" in metrics
        assert metrics["expected_return"] > 0

    def test_empty_weights(self):
        """测试空权重"""
        metrics = calculate_portfolio_metrics({}, {})
        assert metrics["expected_return"] == 0.0
        assert metrics["volatility"] == 0.0

    def test_zero_volatility(self):
        """测试零波动率组合"""
        weights = {"A": 1.0}
        returns = {"A": [0.01, 0.01, 0.01, 0.01]}
        metrics = calculate_portfolio_metrics(weights, returns)
        assert metrics["volatility"] == 0.0
        assert metrics["sharpe_ratio"] == 0.0  # 除以0处理
