"""策略注册表测试"""

import logging
import logging.handlers

import pytest

from aipha.strategy.base import OrderIntent, Strategy, StrategyContext
from aipha.strategy.registry import (
    StrategyRegistry,
    _reset_registry,
    create_strategy,
    create_strategy_from_config,
    get_strategy,
    list_strategies,
    register,
)


# ============================================================================
# 测试用策略类
# ============================================================================


class SampleStrategy(Strategy):
    """测试用策略"""

    name = "SampleStrategy"

    def __init__(self, param1: int = 10, param2: str = "default"):
        self.param1 = param1
        self.param2 = param2

    def on_bar(self, bars, context):
        return []


class AnotherStrategy(Strategy):
    """另一个测试策略"""

    name = "Another"

    def on_bar(self, bars, context):
        return []


# ============================================================================
# StrategyRegistry 测试
# ============================================================================


class TestStrategyRegistry:
    """StrategyRegistry 类测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """每个测试前重置注册表"""
        # 保存当前策略
        registry = StrategyRegistry()
        saved = dict(registry._strategies)
        registry.clear()
        yield
        # 恢复（原地更新，避免替换 dict 引用导致不一致）
        registry._strategies.clear()
        registry._strategies.update(saved)

    def test_singleton(self):
        """测试单例模式"""
        r1 = StrategyRegistry()
        r2 = StrategyRegistry()
        assert r1 is r2

    def test_register_decorator(self):
        """测试装饰器注册"""
        registry = StrategyRegistry()

        @registry.register("test")
        class MyStrategy(Strategy):
            name = "My"

            def on_bar(self, bars, context):
                return []

        assert "test" in registry.list_strategies()
        assert registry.get("test") is MyStrategy

    def test_register_class(self):
        """测试直接注册"""
        registry = StrategyRegistry()
        registry.register_class("test_direct", SampleStrategy)

        assert "test_direct" in registry.list_strategies()

    def test_create_strategy(self):
        """测试创建策略"""
        registry = StrategyRegistry()
        registry.register_class("test", SampleStrategy)

        strategy = registry.create("test", param1=20, param2="custom")

        assert isinstance(strategy, SampleStrategy)
        assert strategy.param1 == 20
        assert strategy.param2 == "custom"

    def test_create_unknown_strategy(self):
        """测试创建未知策略"""
        registry = StrategyRegistry()

        with pytest.raises(KeyError, match="未注册"):
            registry.create("nonexistent")

    def test_create_with_invalid_params(self):
        """测试参数错误"""
        registry = StrategyRegistry()
        registry.register_class("test", SampleStrategy)

        with pytest.raises(TypeError, match="参数错误"):
            registry.create("test", invalid_param=123)

    def test_list_strategies(self):
        """测试列出策略"""
        registry = StrategyRegistry()
        registry.register_class("a", SampleStrategy)
        registry.register_class("b", AnotherStrategy)

        strategies = registry.list_strategies()
        assert "a" in strategies
        assert "b" in strategies

    def test_overwrite_warning(self):
        """测试覆盖警告：重复注册应覆盖且记录 warning"""
        import logging

        registry = StrategyRegistry()
        # 捕获 warning 日志
        handler = logging.handlers.MemoryHandler(capacity=100)
        handler.setLevel(logging.WARNING)
        strategy_logger = logging.getLogger("aipha.strategy.registry")
        strategy_logger.addHandler(handler)
        try:
            registry.register_class("test", SampleStrategy)
            registry.register_class("test", AnotherStrategy)
        finally:
            strategy_logger.removeHandler(handler)

        assert registry.get("test") is AnotherStrategy
        # 验证覆盖时产生了 warning
        warnings = [r for r in handler.buffer if r.levelno >= logging.WARNING]
        assert any("已存在" in r.getMessage() for r in warnings)


# ============================================================================
# 模块级函数测试
# ============================================================================


class TestModuleFunctions:
    """模块级函数测试"""

    def test_list_builtin_strategies(self):
        """测试列出内置策略"""
        strategies = list_strategies()
        # 应该包含内置策略（demo 前缀）
        assert "demo_momentum" in strategies
        assert "factor" in strategies

    def test_all_equity_strategies_registered(self):
        """所有 A 股策略必须在 registry 中注册（防止遗漏导入）"""
        strategies = list_strategies()
        expected_equity = [
            "multifactor_enhance",
            "smallcap_rotation",
            "pead_event",
            "demo_dual_ma",
            "demo_mean_reversion",
            "sector_momentum",
            "earnings_momentum",
            "value_quality",
        ]
        for name in expected_equity:
            assert name in strategies, f"A 股策略 '{name}' 未注册到 StrategyRegistry"

    def test_get_builtin_strategy(self):
        """测试获取内置策略"""
        from aipha.strategy.momentum import MomentumStrategy

        cls = get_strategy("demo_momentum")
        assert cls is MomentumStrategy

    def test_create_builtin_strategy(self):
        """测试创建内置策略"""
        strategy = create_strategy("demo_momentum", lookback_days=30, top_k=10)
        assert strategy.lookback_days == 30
        assert strategy.top_k == 10

    def test_create_from_config(self):
        """测试从配置创建"""
        config = {
            "name": "demo_momentum",
            "params": {
                "lookback_days": 15,
                "top_k": 3,
            },
        }
        strategy = create_strategy_from_config(config)

        assert strategy.lookback_days == 15
        assert strategy.top_k == 3

    def test_create_from_config_no_params(self):
        """测试从配置创建（无参数）"""
        config = {"name": "demo_momentum"}
        strategy = create_strategy_from_config(config)

        assert strategy is not None

    def test_create_from_config_no_name(self):
        """测试从配置创建（缺少name）"""
        with pytest.raises(ValueError, match="必须包含 'name'"):
            create_strategy_from_config({"params": {}})


# ============================================================================
# 装饰器使用测试
# ============================================================================


class TestRegisterDecorator:
    """register 装饰器测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """每个测试前记录注册表状态"""
        registry = StrategyRegistry()
        saved = dict(registry._strategies)
        yield
        # 清理新注册的
        for key in list(registry._strategies.keys()):
            if key not in saved:
                del registry._strategies[key]

    def test_register_with_name(self):
        """测试带名称注册"""

        @register("custom_name")
        class CustomStrategy(Strategy):
            name = "Custom"

            def on_bar(self, bars, context):
                return []

        assert get_strategy("custom_name") is CustomStrategy

    def test_register_without_name(self):
        """测试不带名称注册（使用类名）"""

        @register()
        class AutoNameStrategy(Strategy):
            name = "AutoName"

            def on_bar(self, bars, context):
                return []

        assert get_strategy("AutoNameStrategy") is AutoNameStrategy


# ============================================================================
# 集成测试
# ============================================================================


class TestIntegration:
    """集成测试"""

    def test_full_workflow(self):
        """测试完整工作流程"""
        # 1. 从配置创建策略
        config = {
            "name": "demo_momentum",
            "params": {
                "lookback_days": 20,
                "top_k": 5,
                "rebalance_days": 5,
                "target_symbols": ["000001.SZ", "600000.SH"],
            },
        }
        strategy = create_strategy_from_config(config)

        # 2. 验证策略属性
        assert strategy.lookback_days == 20
        assert strategy.top_k == 5
        assert "000001.SZ" in strategy.target_symbols

        # 3. 调用 on_bar（空输入）
        from datetime import datetime

        context = StrategyContext(
            current_time=datetime.now(),
            positions={},
            cash=1000000.0,
            nav=1000000.0,
        )
        intents = strategy.on_bar({}, context)
        assert isinstance(intents, list)

    def test_momentum_alias(self):
        """测试 momentum 别名"""
        # demo_momentum 和 momentum_factor 应该是同一个类
        from aipha.strategy.momentum import MomentumStrategy

        assert get_strategy("demo_momentum") is MomentumStrategy
        assert get_strategy("momentum_factor") is MomentumStrategy
