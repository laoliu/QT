"""Tests for portfolio optimizer config integration.

Tests:
- PortfolioConfig dataclass
- YAML parsing of portfolio section
- _build_weight_optimizer factory
- Strategy integration (optimizer via StrategyContext)
- config_to_dict round-trip
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from aipha.backtest.config_models import (
    BacktestRunConfig,
    CostConfig,
    DataConfig,
    FillModelConfig,
    PortfolioConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
)
from aipha.backtest.config_parser import config_to_dict, parse_config
from aipha.backtest.engine_builder import _build_weight_optimizer


# ============================================================================
# PortfolioConfig 基础测试
# ============================================================================


class TestPortfolioConfig:
    """PortfolioConfig 数据类测试。"""

    def test_default_values(self):
        cfg = PortfolioConfig()
        assert cfg.method == "equal"
        assert cfg.params == {}

    def test_custom_method(self):
        cfg = PortfolioConfig(method="risk_parity", params={"max_iterations": 200})
        assert cfg.method == "risk_parity"
        assert cfg.params["max_iterations"] == 200

    def test_in_backtest_run_config(self):
        """BacktestRunConfig 默认包含 PortfolioConfig。"""
        brc = BacktestRunConfig(
            name="test",
            data=DataConfig(source="synthetic", start_date=date(2024, 1, 1), end_date=date(2024, 6, 1)),
            universe=UniverseConfig(),
            strategy=StrategyConfig(name="test"),
            cost=CostConfig(),
        )
        assert brc.portfolio.method == "equal"
        assert brc.portfolio.params == {}

    def test_custom_portfolio_in_config(self):
        brc = BacktestRunConfig(
            name="test",
            data=DataConfig(source="synthetic", start_date=date(2024, 1, 1), end_date=date(2024, 6, 1)),
            universe=UniverseConfig(),
            strategy=StrategyConfig(name="test"),
            cost=CostConfig(),
            portfolio=PortfolioConfig(method="min_variance"),
        )
        assert brc.portfolio.method == "min_variance"


# ============================================================================
# YAML 解析测试
# ============================================================================


class TestPortfolioConfigParsing:
    """从 YAML dict 解析 portfolio 配置。"""

    @pytest.fixture
    def base_raw(self):
        return {
            "name": "test_backtest",
            "data": {"source": "synthetic", "start_date": "2024-01-01", "end_date": "2024-06-01"},
            "universe": {},
            "strategy": {"name": "buy_and_hold"},
            "cost": {},
        }

    def test_parse_no_portfolio(self, base_raw):
        """无 portfolio 节 → 默认等权。"""
        cfg = parse_config(base_raw)
        assert cfg.portfolio.method == "equal"
        assert cfg.portfolio.params == {}

    def test_parse_portfolio_method(self, base_raw):
        """显式 portfolio.method。"""
        base_raw["portfolio"] = {"method": "risk_parity"}
        cfg = parse_config(base_raw)
        assert cfg.portfolio.method == "risk_parity"

    def test_parse_portfolio_with_params(self, base_raw):
        """portfolio section with extra params。"""
        base_raw["portfolio"] = {
            "method": "max_sharpe",
            "risk_free_rate": 0.03,
        }
        cfg = parse_config(base_raw)
        assert cfg.portfolio.method == "max_sharpe"
        assert cfg.portfolio.params == {"risk_free_rate": 0.03}

    def test_parse_weight_method_fallback(self, base_raw):
        """strategy.params.weight_method 回退。"""
        base_raw["strategy"]["params"] = {"weight_method": "min_variance", "top_k": 10}
        cfg = parse_config(base_raw)
        assert cfg.portfolio.method == "min_variance"

    def test_portfolio_overrides_weight_method(self, base_raw):
        """portfolio 节优先于 strategy.params.weight_method。"""
        base_raw["portfolio"] = {"method": "risk_parity"}
        base_raw["strategy"]["params"] = {"weight_method": "min_variance"}
        cfg = parse_config(base_raw)
        assert cfg.portfolio.method == "risk_parity"

    def test_config_to_dict_roundtrip(self, base_raw):
        """config_to_dict 包含 portfolio 节。"""
        base_raw["portfolio"] = {"method": "risk_parity", "max_iterations": 200}
        cfg = parse_config(base_raw)
        d = config_to_dict(cfg)
        assert d["portfolio"]["method"] == "risk_parity"
        assert d["portfolio"]["max_iterations"] == 200


# ============================================================================
# _build_weight_optimizer 工厂测试
# ============================================================================


class TestBuildWeightOptimizer:
    """_build_weight_optimizer 工厂函数测试。"""

    def _make_config(self, method: str = "equal", **params) -> BacktestRunConfig:
        return BacktestRunConfig(
            name="test",
            data=DataConfig(source="synthetic", start_date=date(2024, 1, 1), end_date=date(2024, 6, 1)),
            universe=UniverseConfig(),
            strategy=StrategyConfig(name="test"),
            cost=CostConfig(),
            portfolio=PortfolioConfig(method=method, params=params),
        )

    def test_equal_returns_none(self):
        """等权 → 返回 None。"""
        assert _build_weight_optimizer(self._make_config("equal")) is None
        assert _build_weight_optimizer(self._make_config("ew")) is None
        assert _build_weight_optimizer(self._make_config("equal_weight")) is None

    def test_risk_parity(self):
        opt = _build_weight_optimizer(self._make_config("risk_parity"))
        assert opt is not None
        assert "RiskParity" in type(opt).__name__

    def test_min_variance(self):
        opt = _build_weight_optimizer(self._make_config("min_variance"))
        assert opt is not None
        assert "MinVariance" in type(opt).__name__

    def test_max_sharpe(self):
        opt = _build_weight_optimizer(self._make_config("max_sharpe"))
        assert opt is not None
        assert "MaxSharpe" in type(opt).__name__

    def test_mean_variance(self):
        opt = _build_weight_optimizer(self._make_config("mean_variance"))
        assert opt is not None
        assert "MeanVariance" in type(opt).__name__

    def test_market_cap(self):
        opt = _build_weight_optimizer(self._make_config("market_cap"))
        assert opt is not None
        assert "MarketCap" in type(opt).__name__

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError, match="未知优化方法"):
            _build_weight_optimizer(self._make_config("unknown_method"))

    def test_risk_parity_with_params(self):
        opt = _build_weight_optimizer(
            self._make_config("risk_parity", max_iterations=500, tolerance=1e-8)
        )
        assert opt is not None


# ============================================================================
# 策略集成测试 (optimizer via StrategyContext)
# ============================================================================


class TestOptimizerStrategyIntegration:
    """测试 optimizer 通过 StrategyContext.metadata 传递给策略。"""

    def test_context_metadata_includes_optimizer(self):
        """BacktestEngine 将 optimizer 注入到 context.metadata。"""
        from aipha.strategy.base import StrategyContext
        from datetime import datetime

        # 模拟 optimizer 在 metadata 中
        mock_opt = MagicMock()
        ctx = StrategyContext(
            current_time=datetime(2024, 1, 15, 15, 0),
            positions={"SZ000001": 100},
            cash=500_000.0,
            nav=1_000_000.0,
            metadata={"weight_optimizer": mock_opt},
        )
        assert ctx.metadata["weight_optimizer"] is mock_opt

    def test_compute_optimizer_weights_with_history(self):
        """测试 _compute_optimizer_weights 从价格历史计算收益率。"""
        from aipha.strategy.equity_rotation_base import (
            EquityRotationConfig,
            EquityRotationStrategy,
        )
        from aipha.portfolio.optimizer import EqualWeightOptimizer

        # 创建一个具体的子类实例以调用 _compute_optimizer_weights
        class _TestStrategy(EquityRotationStrategy):
            @property
            def config(self):
                return EquityRotationConfig()

            def _score_candidates(self, candidates, context):
                return []

        strategy = _TestStrategy()
        strategy._price_history = {
            "A": [100.0, 102.0, 101.0, 105.0],
            "B": [50.0, 48.0, 52.0, 53.0],
            "C": [200.0, 195.0, 198.0, 202.0],
        }
        optimizer = EqualWeightOptimizer()
        weights = strategy._compute_optimizer_weights(optimizer, ["A", "B", "C"])

        # 等权优化器 → 各 1/3
        assert len(weights) == 3
        for sym in ["A", "B", "C"]:
            assert abs(weights[sym] - 1.0 / 3) < 1e-6

    def test_compute_optimizer_weights_fallback_on_error(self):
        """优化器抛异常 → 回退等权。"""
        from aipha.strategy.equity_rotation_base import (
            EquityRotationConfig,
            EquityRotationStrategy,
        )

        class _TestStrategy(EquityRotationStrategy):
            @property
            def config(self):
                return EquityRotationConfig()

            def _score_candidates(self, candidates, context):
                return []

        strategy = _TestStrategy()
        strategy.name = "test"
        strategy._price_history = {"A": [100.0, 102.0], "B": [50.0, 48.0]}

        bad_optimizer = MagicMock()
        bad_optimizer.optimize.side_effect = RuntimeError("fail")

        weights = strategy._compute_optimizer_weights(bad_optimizer, ["A", "B"])
        assert abs(weights["A"] - 0.5) < 1e-6
        assert abs(weights["B"] - 0.5) < 1e-6

    def test_compute_optimizer_weights_empty_history(self):
        """无价格历史时仍可调用优化器（returns=None）。"""
        from aipha.strategy.equity_rotation_base import (
            EquityRotationConfig,
            EquityRotationStrategy,
        )
        from aipha.portfolio.optimizer import EqualWeightOptimizer

        class _TestStrategy(EquityRotationStrategy):
            @property
            def config(self):
                return EquityRotationConfig()

            def _score_candidates(self, candidates, context):
                return []

        strategy = _TestStrategy()
        strategy._price_history = {}

        weights = strategy._compute_optimizer_weights(
            EqualWeightOptimizer(), ["X", "Y"]
        )
        assert len(weights) == 2
        assert abs(sum(weights.values()) - 1.0) < 1e-6
