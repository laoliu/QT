"""多策略组合模块测试"""

from datetime import date, timedelta
import numpy as np
import pytest

from aipha.strategy.portfolio import (
    StrategyInfo,
    StrategyMetrics,
    CorrelationMatrix,
    PortfolioResult,
    StrategyPortfolio,
    RiskParityAllocator,
    create_equal_weight_portfolio,
)


class TestStrategyPortfolio:
    """StrategyPortfolio测试"""

    def _generate_nav(
        self,
        n_days: int = 100,
        annual_return: float = 0.10,
        volatility: float = 0.20,
        seed: int = 42,
    ) -> dict[date, float]:
        """生成模拟净值"""
        rng = np.random.default_rng(seed)
        daily_return = annual_return / 252
        daily_vol = volatility / np.sqrt(252)

        start_date = date(2023, 1, 1)
        nav = {start_date: 1.0}
        current_nav = 1.0

        for i in range(1, n_days):
            current_date = start_date + timedelta(days=i)
            ret = daily_return + daily_vol * rng.standard_normal()
            current_nav = current_nav * (1 + ret)
            nav[current_date] = current_nav

        return nav

    def test_add_strategy(self):
        """测试添加策略"""
        portfolio = StrategyPortfolio()
        nav = self._generate_nav()

        portfolio.add_strategy("test", nav, weight=0.5)

        assert "test" in portfolio._strategies
        assert portfolio._strategies["test"].weight == 0.5

    def test_remove_strategy(self):
        """测试移除策略"""
        portfolio = StrategyPortfolio()
        portfolio.add_strategy("test", self._generate_nav())
        portfolio.remove_strategy("test")

        assert "test" not in portfolio._strategies

    def test_equal_weight(self):
        """测试等权配置"""
        portfolio = StrategyPortfolio()
        portfolio.add_strategy("s1", self._generate_nav(seed=1), weight=0.8)
        portfolio.add_strategy("s2", self._generate_nav(seed=2), weight=0.2)
        portfolio.equal_weight()

        assert portfolio._strategies["s1"].weight == 0.5
        assert portfolio._strategies["s2"].weight == 0.5

    def test_set_weights(self):
        """测试设置权重"""
        portfolio = StrategyPortfolio()
        portfolio.add_strategy("s1", self._generate_nav(seed=1))
        portfolio.add_strategy("s2", self._generate_nav(seed=2))

        portfolio.set_weights({"s1": 0.7, "s2": 0.3})

        assert portfolio._strategies["s1"].weight == 0.7
        assert portfolio._strategies["s2"].weight == 0.3

    def test_compute_empty(self):
        """测试空组合"""
        portfolio = StrategyPortfolio()
        result = portfolio.compute()

        assert result.total_return == 0
        assert len(result.strategy_metrics) == 0

    def test_compute_single_strategy(self):
        """测试单策略组合"""
        portfolio = StrategyPortfolio()
        nav = self._generate_nav(n_days=100, annual_return=0.20, seed=42)
        portfolio.add_strategy("test", nav)

        result = portfolio.compute()

        assert len(result.strategy_metrics) == 1
        assert result.weights["test"] == 1.0

    def test_compute_multiple_strategies(self):
        """测试多策略组合"""
        portfolio = StrategyPortfolio()
        portfolio.add_strategy("s1", self._generate_nav(seed=1), weight=0.6)
        portfolio.add_strategy("s2", self._generate_nav(seed=2), weight=0.4)

        result = portfolio.compute()

        assert len(result.strategy_metrics) == 2
        assert result.weights["s1"] == pytest.approx(0.6, rel=0.01)
        assert result.weights["s2"] == pytest.approx(0.4, rel=0.01)

    def test_portfolio_nav(self):
        """测试组合净值"""
        portfolio = StrategyPortfolio()

        # 两个策略，一个涨一个跌
        nav1 = {date(2023, 1, i): 1.0 + i * 0.01 for i in range(1, 11)}
        nav2 = {date(2023, 1, i): 1.0 - i * 0.005 for i in range(1, 11)}

        portfolio.add_strategy("up", nav1, weight=0.5)
        portfolio.add_strategy("down", nav2, weight=0.5)

        result = portfolio.compute()

        # 第一天组合净值 = 0.5 * 1.01 + 0.5 * 0.995 = 1.0025
        assert result.portfolio_nav[date(2023, 1, 1)] == pytest.approx(1.0025, rel=0.01)

    def test_correlation_matrix(self):
        """测试相关性矩阵"""
        portfolio = StrategyPortfolio()
        portfolio.add_strategy("s1", self._generate_nav(seed=1))
        portfolio.add_strategy("s2", self._generate_nav(seed=2))

        result = portfolio.compute()

        assert result.correlation is not None
        assert len(result.correlation.strategy_names) == 2
        assert result.correlation.matrix.shape == (2, 2)
        # 对角线应为1
        assert result.correlation.matrix[0, 0] == pytest.approx(1.0)

    def test_get_correlation(self):
        """测试获取相关性"""
        portfolio = StrategyPortfolio()
        portfolio.add_strategy("s1", self._generate_nav(seed=1))
        portfolio.add_strategy("s2", self._generate_nav(seed=2))

        result = portfolio.compute()
        corr = result.correlation.get_correlation("s1", "s2")

        assert -1 <= corr <= 1

    def test_contribution(self):
        """测试策略贡献"""
        portfolio = StrategyPortfolio()

        # 一个高收益策略
        start = date(2023, 1, 1)
        nav1 = {start + timedelta(days=i): 1.0 + i * 0.02 for i in range(50)}
        # 一个低收益策略
        nav2 = {start + timedelta(days=i): 1.0 + i * 0.005 for i in range(50)}

        portfolio.add_strategy("high", nav1, weight=0.6)
        portfolio.add_strategy("low", nav2, weight=0.4)

        result = portfolio.compute()

        # 高收益策略应该贡献更多
        high_metric = next(m for m in result.strategy_metrics if m.name == "high")
        low_metric = next(m for m in result.strategy_metrics if m.name == "low")

        assert high_metric.contribution > low_metric.contribution

    def test_summary(self):
        """测试摘要报告"""
        portfolio = StrategyPortfolio()
        portfolio.add_strategy("s1", self._generate_nav(seed=1))
        portfolio.add_strategy("s2", self._generate_nav(seed=2))

        summary = portfolio.summary()

        assert "多策略组合报告" in summary
        assert "组合整体表现" in summary
        assert "各策略表现" in summary
        assert "权重配置" in summary
        assert "策略相关性" in summary


class TestRiskParityAllocator:
    """RiskParityAllocator测试"""

    def test_compute_weights(self):
        """测试计算权重"""
        allocator = RiskParityAllocator()

        # 两个策略，波动率不同
        returns = {
            "low_vol": np.random.randn(100) * 0.01,  # 低波动
            "high_vol": np.random.randn(100) * 0.03,  # 高波动
        }

        weights = allocator.compute_weights(returns)

        assert "low_vol" in weights
        assert "high_vol" in weights
        # 低波动策略应该获得更高权重
        assert weights["low_vol"] > weights["high_vol"]
        # 权重和为1
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_zero_volatility(self):
        """测试零波动率处理"""
        allocator = RiskParityAllocator()

        # 一个策略有零波动率
        returns = {
            "zero": np.zeros(100),
            "normal": np.random.randn(100) * 0.01,
        }

        weights = allocator.compute_weights(returns)

        # 应该回退到等权
        assert weights["zero"] == pytest.approx(0.5)
        assert weights["normal"] == pytest.approx(0.5)

    def test_empty_returns(self):
        """测试空收益"""
        allocator = RiskParityAllocator()
        weights = allocator.compute_weights({})
        assert weights == {}


class TestConvenienceFunction:
    """便捷函数测试"""

    def test_create_equal_weight_portfolio(self):
        """测试create_equal_weight_portfolio"""
        strategies = {
            "s1": {date(2023, 1, i): 1.0 + i * 0.01 for i in range(1, 21)},
            "s2": {date(2023, 1, i): 1.0 + i * 0.02 for i in range(1, 21)},
        }

        result = create_equal_weight_portfolio(strategies)

        assert isinstance(result, PortfolioResult)
        assert result.weights["s1"] == pytest.approx(0.5)
        assert result.weights["s2"] == pytest.approx(0.5)


class TestCorrelationMatrix:
    """CorrelationMatrix测试"""

    def test_dataclass(self):
        """测试数据类"""
        matrix = np.array([[1.0, 0.5], [0.5, 1.0]])
        corr = CorrelationMatrix(
            strategy_names=["s1", "s2"],
            matrix=matrix,
            avg_correlation=0.5,
        )

        assert corr.strategy_names == ["s1", "s2"]
        assert corr.avg_correlation == 0.5
        assert corr.get_correlation("s1", "s2") == 0.5


class TestStrategyMetrics:
    """StrategyMetrics测试"""

    def test_dataclass(self):
        """测试数据类"""
        metrics = StrategyMetrics(
            name="test",
            total_return=0.15,
            annualized_return=0.12,
            volatility=0.18,
            sharpe=1.2,
            max_drawdown=0.08,
            contribution=0.06,
        )

        assert metrics.name == "test"
        assert metrics.sharpe == 1.2
        assert metrics.contribution == 0.06
