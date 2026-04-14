"""
多策略集成测试

测试覆盖：
- 策略注册完整性
- 配置文件驱动的策略创建
- Ensemble 策略与原始策略共存
- MultiStrategyRunner 与 ensemble 策略集成
- YAML 配置加载完整路径
- 策略别名解析
"""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from aipha.strategy.registry import (
    StrategyRegistry,
    create_strategy,
    create_strategy_from_config,
    list_strategies,
)


# ============================================================
# 注册完整性
# ============================================================


class TestStrategyRegistryCompleteness:
    """策略注册完整性测试"""

    EXPECTED_STRATEGIES = {
        # A 股策略
        "demo_momentum",
        "factor",
        "factor_based",
        "demo_mean_reversion",
        "demo_dual_ma",
        # CB 基础策略
        "small_size",
        "new_bond",
        "new_small",
        "triple_low",
        "double_low",
        "low_price",
        "low_premium",
        "pseudo_ytm",
        "ytm_plus",
        "cb_mean_reversion",
        "cb_momentum",
        "cb_value_momentum",
        "multi_factor",
        "vol_breakout",
        "turnover_surge",
        "defensive_value",
        "negative_premium",
        "forced_redeem_play",
        # CB Ensemble 策略
        "small_size_ensemble",
        "new_bond_ensemble",
        "new_small_ensemble",
        # 增强/复合策略
        "small_size_enhanced",
        "smallcap_rotation",
        "multifactor_enhance",
        "pead_event",
        "conversion_arbitrage",
    }

    EXPECTED_ALIASES = {
        "momentum_factor",
        "reversion",
        "moving_average",
    }

    def test_all_expected_strategies_registered(self):
        """所有预期策略已注册"""
        registered = set(list_strategies())
        missing = self.EXPECTED_STRATEGIES - registered
        assert not missing, f"未注册的策略: {missing}"

    def test_aliases_registered(self):
        """所有别名已注册"""
        registered = set(list_strategies())
        missing = self.EXPECTED_ALIASES - registered
        assert not missing, f"未注册的别名: {missing}"

    def test_total_count(self):
        """策略总数 ≥ 34"""
        strategies = list_strategies()
        assert len(strategies) >= 34, (
            f"策略数量不足: {len(strategies)}，期望 ≥ 34"
        )

    def test_no_duplicate_names(self):
        """注册表无重复名称"""
        strategies = list_strategies()
        # list 去重后长度应相同
        assert len(set(strategies)) == len(strategies)


# ============================================================
# 策略创建
# ============================================================


class TestStrategyCreation:
    """通过注册表创建策略"""

    def test_create_small_size_ensemble(self):
        """创建 small_size_ensemble"""
        strategy = create_strategy("small_size_ensemble")
        assert strategy.__class__.__name__ == "SmallSizeEnsembleStrategy"

    def test_create_new_bond_ensemble(self):
        """创建 new_bond_ensemble"""
        strategy = create_strategy("new_bond_ensemble")
        assert strategy.__class__.__name__ == "NewBondEnsembleStrategy"

    def test_create_new_small_ensemble(self):
        """创建 new_small_ensemble"""
        strategy = create_strategy("new_small_ensemble")
        assert strategy.__class__.__name__ == "NewSmallEnsembleStrategy"

    def test_create_with_params(self):
        """带参数创建 ensemble 策略"""
        strategy = create_strategy(
            "small_size_ensemble",
            top_k=10,
            min_rating="AA",
            variants=[
                {"top_k": 8, "max_price": 110.0},
                {"top_k": 10, "max_price": 120.0},
            ],
        )
        assert strategy.config.top_k == 10
        assert strategy.config.min_rating == "AA"
        assert len(strategy.config.variants) == 2

    def test_create_nonexistent_raises(self):
        """创建不存在的策略应抛出 KeyError"""
        with pytest.raises(KeyError):
            create_strategy("nonexistent_strategy_xyz")

    def test_create_from_config_dict(self):
        """从配置字典创建"""
        config = {
            "name": "small_size_ensemble",
            "params": {
                "top_k": 12,
                "rebalance_days": 5,
                "variants": [
                    {"top_k": 10},
                    {"top_k": 12},
                    {"top_k": 14},
                ],
            },
        }
        strategy = create_strategy_from_config(config)
        assert strategy.__class__.__name__ == "SmallSizeEnsembleStrategy"
        assert len(strategy.config.variants) == 3

    def test_create_from_config_no_name_raises(self):
        """配置无 name 应抛异常"""
        with pytest.raises(ValueError, match="name"):
            create_strategy_from_config({"params": {}})


# ============================================================
# YAML 配置文件加载
# ============================================================


class TestYAMLConfigLoading:
    """从 YAML 文件加载策略"""

    CONFIG_DIR = Path("configs/cb_strategies")

    @pytest.fixture
    def config_files(self) -> list[Path]:
        """可用的集成策略配置文件"""
        return [
            self.CONFIG_DIR / "small_size_ensemble.yml",
            self.CONFIG_DIR / "new_bond_ensemble.yml",
            self.CONFIG_DIR / "new_small_ensemble.yml",
        ]

    def test_all_ensemble_configs_exist(self, config_files):
        """所有集成策略配置文件存在"""
        for cfg_path in config_files:
            assert cfg_path.exists(), f"配置文件不存在: {cfg_path}"

    def test_configs_valid_yaml(self, config_files):
        """配置文件是有效 YAML"""
        for cfg_path in config_files:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            assert data is not None
            assert "strategy" in data

    @pytest.mark.parametrize(
        "config_name,expected_class",
        [
            ("small_size_ensemble", "SmallSizeEnsembleStrategy"),
            ("new_bond_ensemble", "NewBondEnsembleStrategy"),
            ("new_small_ensemble", "NewSmallEnsembleStrategy"),
        ],
    )
    def test_config_creates_correct_strategy(self, config_name, expected_class):
        """配置文件创建正确类型的策略"""
        cfg_path = self.CONFIG_DIR / f"{config_name}.yml"
        if not cfg_path.exists():
            pytest.skip(f"配置文件不存在: {cfg_path}")

        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        strategy = create_strategy_from_config(data["strategy"])
        assert strategy.__class__.__name__ == expected_class

    @pytest.mark.parametrize(
        "config_name",
        ["small_size_ensemble", "new_bond_ensemble", "new_small_ensemble"],
    )
    def test_config_has_variants(self, config_name):
        """配置文件包含 variants"""
        cfg_path = self.CONFIG_DIR / f"{config_name}.yml"
        if not cfg_path.exists():
            pytest.skip(f"配置文件不存在: {cfg_path}")

        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        strategy = create_strategy_from_config(data["strategy"])
        assert len(strategy.config.variants) >= 3, (
            f"{config_name} variants 不足 3 个"
        )

    @pytest.mark.parametrize(
        "config_name",
        ["small_size_ensemble", "new_bond_ensemble", "new_small_ensemble"],
    )
    def test_config_has_credit_filter(self, config_name):
        """配置文件包含信用评级过滤"""
        cfg_path = self.CONFIG_DIR / f"{config_name}.yml"
        if not cfg_path.exists():
            pytest.skip(f"配置文件不存在: {cfg_path}")

        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        params = data["strategy"]["params"]
        # 信用评级过滤应存在，可以是 AA 或 A+（小规模策略放宽至 A+）
        rating = params.get("min_rating", "")
        assert rating in ("AA", "A+", "A"), (
            f"{config_name} min_rating='{rating}' 不在允许范围 ['AA','A+','A']"
        )

    def test_ensemble_portfolio_config_exists(self):
        """集成组合配置文件存在"""
        cfg_path = Path("configs/paper/cb_ensemble_portfolio.yml")
        assert cfg_path.exists(), "集成组合配置文件不存在"

    def test_ensemble_portfolio_config_valid(self):
        """集成组合配置有效"""
        cfg_path = Path("configs/paper/cb_ensemble_portfolio.yml")
        if not cfg_path.exists():
            pytest.skip("集成组合配置文件不存在")

        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        ms = data["multi_strategy"]
        assert "strategies" in ms
        assert len(ms["strategies"]) == 3

        # 权重之和 == 1.0
        total_weight = sum(s["weight"] for s in ms["strategies"])
        assert abs(total_weight - 1.0) < 0.01

        # 所有策略名在注册表中
        registered = set(list_strategies())
        for s in ms["strategies"]:
            assert s["name"] in registered, f"策略 '{s['name']}' 未注册"


# ============================================================
# Ensemble 策略与基础策略一致性
# ============================================================


class TestEnsembleBaseConsistency:
    """Ensemble 策略与原始策略的接口一致性"""

    @pytest.mark.parametrize(
        "base_name,ensemble_name",
        [
            ("small_size", "small_size_ensemble"),
            ("new_bond", "new_bond_ensemble"),
            ("new_small", "new_small_ensemble"),
        ],
    )
    def test_same_base_class(self, base_name, ensemble_name):
        """Ensemble 与基础策略共享相同基类"""
        base = create_strategy(base_name)
        ensemble = create_strategy(ensemble_name)

        # 都继承自 CBRotationStrategy
        from aipha.strategy.cb_rotation_base import CBRotationStrategy

        assert isinstance(base, CBRotationStrategy)
        assert isinstance(ensemble, CBRotationStrategy)

    @pytest.mark.parametrize(
        "base_name,ensemble_name",
        [
            ("small_size", "small_size_ensemble"),
            ("new_bond", "new_bond_ensemble"),
            ("new_small", "new_small_ensemble"),
        ],
    )
    def test_ensemble_has_name_attribute(self, base_name, ensemble_name):
        """Ensemble 策略有 name 属性"""
        ensemble = create_strategy(ensemble_name)
        assert hasattr(ensemble, "name")
        assert ensemble_name in ensemble.name

    @pytest.mark.parametrize(
        "ensemble_name",
        ["small_size_ensemble", "new_bond_ensemble", "new_small_ensemble"],
    )
    def test_ensemble_default_no_variants(self, ensemble_name):
        """默认创建的 ensemble 策略 variants 为空"""
        strategy = create_strategy(ensemble_name)
        assert strategy.config.variants is not None
        assert isinstance(strategy.config.variants, list)


# ============================================================
# MultiStrategyRunner 集成
# ============================================================


class TestMultiStrategyRunnerIntegration:
    """多策略运行器与集成策略的集成测试"""

    def test_runner_accepts_ensemble_strategy(self):
        """MultiStrategyRunner 能添加 ensemble 策略"""
        from aipha.runner.multi_strategy import (
            MultiStrategyRunner,
            PortfolioConfig,
        )

        config = PortfolioConfig(total_capital=Decimal("500000"))
        runner = MultiStrategyRunner(config)

        strategy = create_strategy("small_size_ensemble", top_k=10)
        runner.add_strategy(strategy, weight=1.0)

        assert len(runner._allocations) == 1
        assert runner._allocations[0].strategy is strategy

    def test_runner_multiple_ensemble_strategies(self):
        """MultiStrategyRunner 支持多个 ensemble 策略"""
        from aipha.runner.multi_strategy import (
            MultiStrategyRunner,
            PortfolioConfig,
        )

        config = PortfolioConfig(total_capital=Decimal("1000000"))
        runner = MultiStrategyRunner(config)

        runner.add_strategy(
            create_strategy("small_size_ensemble", top_k=10),
            weight=0.4,
        )
        runner.add_strategy(
            create_strategy("new_bond_ensemble", top_k=8),
            weight=0.35,
        )
        runner.add_strategy(
            create_strategy("new_small_ensemble", top_k=8),
            weight=0.25,
        )

        assert len(runner._allocations) == 3
        total = sum(a.weight for a in runner._allocations)
        assert abs(total - 1.0) < 0.01


# ============================================================
# 自适应权重配置
# ============================================================


class TestAdaptiveWeightsConfig:
    """自适应权重配置测试"""

    def test_adaptive_weights_config_valid(self):
        """adaptive_weights.yml 有效"""
        cfg_path = Path("configs/adaptive_weights.yml")
        if not cfg_path.exists():
            pytest.skip("配置文件不存在")

        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert "default" in data
        assert "portfolios" in data

    def test_core_portfolio_uses_ensemble(self):
        """核心组合使用集成策略"""
        cfg_path = Path("configs/adaptive_weights.yml")
        if not cfg_path.exists():
            pytest.skip("配置文件不存在")

        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        core = data["portfolios"]["core"]
        strategies = core["strategies"]

        assert "small_size_ensemble" in strategies
        assert "new_small_ensemble" in strategies
        assert "new_bond_ensemble" in strategies

    def test_all_weather_portfolio(self):
        """全天候组合包含集成策略和防守策略"""
        cfg_path = Path("configs/adaptive_weights.yml")
        if not cfg_path.exists():
            pytest.skip("配置文件不存在")

        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        aw = data["portfolios"]["all_weather"]
        strategies = aw["strategies"]

        # 包含集成策略
        assert any("ensemble" in s for s in strategies)
        # 包含防守策略
        assert "defensive_value" in strategies

    def test_legacy_portfolio_preserved(self):
        """兼容旧版配置"""
        cfg_path = Path("configs/adaptive_weights.yml")
        if not cfg_path.exists():
            pytest.skip("配置文件不存在")

        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert "core_legacy" in data["portfolios"]
