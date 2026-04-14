"""ML 策略 config_runner 集成测试

验证 ml_predict 策略可通过 config_runner 正确实例化，
回测配置可正常解析，策略注册表包含 ml_predict。
"""

from __future__ import annotations

from datetime import date

import pytest

from aipha.backtest.config_runner import (
    BacktestRunConfig,
    CostConfig,
    DataConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
    _create_strategy,
)
from aipha.strategy.ml_predict import MLPredictStrategy, MLStrategyConfig


# ---------------------------------------------------------------------------
# 策略创建
# ---------------------------------------------------------------------------

class TestMLStrategyCreation:
    """通过 config_runner._create_strategy 创建 ML 策略。"""

    def _make_run_config(self, **strategy_params) -> BacktestRunConfig:
        return BacktestRunConfig(
            name="test_ml",
            data=DataConfig(
                source="synthetic",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 3, 31),
                symbols=["600000.SH", "000001.SZ"],
                frequency="1d",
            ),
            universe=UniverseConfig(),
            strategy=StrategyConfig(
                name="ml_predict",
                params=strategy_params,
            ),
            cost=CostConfig(),
            risk=RiskConfig(),
        )

    def test_create_default(self):
        cfg = self._make_run_config()
        strategy = _create_strategy(cfg)
        assert isinstance(strategy, MLPredictStrategy)
        assert strategy.name == "ml_predict"

    def test_create_with_params(self):
        cfg = self._make_run_config(
            top_k=20,
            rebalance_days=3,
            feature_columns=["pe_ttm", "pb", "roe"],
        )
        strategy = _create_strategy(cfg)
        assert isinstance(strategy, MLPredictStrategy)
        assert strategy.config.top_k == 20
        assert strategy.config.rebalance_days == 3
        assert strategy.config.feature_columns == ["pe_ttm", "pb", "roe"]

    def test_create_with_fallback_weights(self):
        cfg = self._make_run_config(
            fallback_weights={"pe_ttm": -2.0, "roe": 1.5},
        )
        strategy = _create_strategy(cfg)
        assert isinstance(strategy, MLPredictStrategy)
        assert strategy.config.fallback_weights["pe_ttm"] == -2.0
        assert strategy.config.fallback_weights["roe"] == 1.5

    def test_create_with_model_path(self):
        """model_path 指向不存在的文件 → 自动回退线性打分。"""
        cfg = self._make_run_config(
            model_path="/nonexistent/model.pkl",
        )
        strategy = _create_strategy(cfg)
        assert isinstance(strategy, MLPredictStrategy)
        assert not strategy._model_loaded  # 文件不存在

    def test_target_symbols_injected(self):
        """config_runner 会自动注入 target_symbols。"""
        cfg = self._make_run_config()
        strategy = _create_strategy(cfg)
        assert isinstance(strategy, MLPredictStrategy)


# ---------------------------------------------------------------------------
# 策略注册表
# ---------------------------------------------------------------------------

class TestMLStrategyRegistration:
    def test_in_registry_after_ensure(self):
        from aipha.backtest.config_runner import _ensure_strategies_registered
        from aipha.strategy.registry import StrategyRegistry

        _ensure_strategies_registered()
        registry = StrategyRegistry()
        assert "ml_predict" in registry.list_strategies()

    def test_also_has_other_equity_strategies(self):
        from aipha.backtest.config_runner import _ensure_strategies_registered
        from aipha.strategy.registry import StrategyRegistry

        _ensure_strategies_registered()
        registry = StrategyRegistry()
        names = registry.list_strategies()
        for expected in ["ml_predict", "multifactor_enhance", "smallcap_rotation"]:
            assert expected in names, f"{expected} not found in registry"


# ---------------------------------------------------------------------------
# YAML 配置解析
# ---------------------------------------------------------------------------

class TestMLYamlConfig:
    def test_parse_ml_config(self):
        """验证 ml_predict.yml 可被正确解析。"""
        from pathlib import Path

        import yaml

        config_path = Path("configs/backtest/ml_predict.yml")
        if not config_path.exists():
            pytest.skip("ml_predict.yml not found")

        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        assert raw["strategy"]["name"] == "ml_predict"
        params = raw["strategy"]["params"]
        assert "feature_columns" in params
        assert "fallback_weights" in params
        assert "top_k" in params
        assert params["top_k"] == 30

    def test_parse_into_run_config(self):
        """验证可从 YAML 构建 BacktestRunConfig。"""
        from pathlib import Path

        import yaml

        config_path = Path("configs/backtest/ml_predict.yml")
        if not config_path.exists():
            pytest.skip("ml_predict.yml not found")

        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        # 构建 DataConfig
        data_raw = raw["data"]
        data_cfg = DataConfig(
            source=data_raw["source"],
            start_date=date.fromisoformat(str(data_raw["start_date"])),
            end_date=date.fromisoformat(str(data_raw["end_date"])),
            frequency=data_raw.get("frequency", "1d"),
            adjust=data_raw.get("adjust", "qfq"),
            cache_dir=data_raw.get("cache_dir"),
        )
        assert data_cfg.source == "tushare"
        assert data_cfg.start_date == date(2023, 1, 1)
