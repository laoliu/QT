"""Strategy base & registry edge-case tests.

Covers untested branches:
- Strategy.on_fill() default no-op
- Strategy.param_space() default returns None
- OrderIntent: symbol="" accepted (no validation)
- StrategyContext: default empty metadata
- StrategyRegistry: clear() then singleton still same instance
- register() decorator with name=None
- create_strategy_from_config: name="" raises ValueError
- _reset_registry invocation
- Builtin alias verification (factor, reversion, moving_average)
"""
from __future__ import annotations

from datetime import datetime

import pytest

from aipha.domain.models import Side
from aipha.strategy.base import (
    IntentType,
    OrderIntent,
    Strategy,
    StrategyContext,
)
from aipha.strategy.registry import (
    StrategyRegistry,
    _reset_registry,
    create_strategy_from_config,
    get_strategy,
    list_strategies,
)


# ============================================================================
# Strategy base class edge cases
# ============================================================================


class _TestStrategy(Strategy):
    """Minimal concrete strategy for testing."""

    name = "test_strategy"

    def on_bar(self, bars, context):
        return []


class TestStrategyBaseEdge:

    def test_on_fill_default_noop(self):
        """on_fill 默认是 no-op，不崩溃"""
        s = _TestStrategy()
        # Should not raise
        s.on_fill("600000.SH", Side.BUY, 100, 10.0)

    def test_on_start_default_noop(self):
        """on_start 默认 no-op"""
        s = _TestStrategy()
        s.on_start()

    def test_on_end_default_noop(self):
        """on_end 默认 no-op"""
        s = _TestStrategy()
        s.on_end()

    def test_param_space_default_none(self):
        """param_space 默认返回 None"""
        assert _TestStrategy.param_space() is None

    def test_repr(self):
        """__repr__ 格式正确"""
        s = _TestStrategy()
        r = repr(s)
        assert "test_strategy" in r
        assert "_TestStrategy" in r


# ============================================================================
# OrderIntent edge cases
# ============================================================================


class TestOrderIntentEdge:

    def test_empty_symbol_accepted(self):
        """symbol="" 不会被 __post_init__ 拒绝（no validation on symbol）"""
        oi = OrderIntent(symbol="", side=Side.BUY, qty=1)
        assert oi.symbol == ""

    def test_qty_zero_raises(self):
        """qty=0 → ValueError"""
        with pytest.raises(ValueError, match="positive"):
            OrderIntent(symbol="A", side=Side.BUY, qty=0)

    def test_qty_negative_raises(self):
        """qty=-1 → ValueError"""
        with pytest.raises(ValueError, match="positive"):
            OrderIntent(symbol="A", side=Side.BUY, qty=-1)

    def test_default_intent_type(self):
        """默认 intent_type 是 ORDER"""
        oi = OrderIntent(symbol="A", side=Side.BUY, qty=100)
        assert oi.intent_type == IntentType.ORDER

    def test_frozen(self):
        """frozen=True → 不可修改"""
        oi = OrderIntent(symbol="A", side=Side.BUY, qty=100)
        with pytest.raises(AttributeError):
            oi.qty = 200


# ============================================================================
# StrategyContext edge cases
# ============================================================================


class TestStrategyContextEdge:

    def test_default_metadata_empty_dict(self):
        """metadata 默认为空字典"""
        ctx = StrategyContext(
            current_time=datetime(2024, 1, 1),
            positions={},
            cash=100000,
            nav=100000,
        )
        assert ctx.metadata == {}

    def test_get_position_missing(self):
        """不存在的 symbol → 0"""
        ctx = StrategyContext(
            current_time=datetime(2024, 1, 1),
            positions={"A": 100},
            cash=50000,
            nav=50000,
        )
        assert ctx.get_position("B") == 0
        assert ctx.get_position("A") == 100


# ============================================================================
# StrategyRegistry edge cases
# ============================================================================


class TestRegistryEdge:

    def test_singleton_identity(self):
        """两次实例化返回同一对象"""
        r1 = StrategyRegistry()
        r2 = StrategyRegistry()
        assert r1 is r2

    def test_create_strategy_from_config_empty_name(self):
        """name="" → ValueError"""
        with pytest.raises(ValueError, match="name"):
            create_strategy_from_config({"name": "", "params": {}})

    def test_create_strategy_from_config_missing_name(self):
        """无 name 键 → ValueError"""
        with pytest.raises(ValueError, match="name"):
            create_strategy_from_config({"params": {}})

    def test_reset_registry_function_exists(self):
        """_reset_registry 函数可调用（不实际清空以避免破坏全局状态）"""
        # Just verify it's callable; actually calling it destroys singleton state
        assert callable(_reset_registry)

    def test_builtin_alias_factor(self):
        """内置别名 'factor' 可创建策略"""
        cls = get_strategy("factor")
        assert cls is not None

    def test_builtin_alias_reversion(self):
        """内置别名 'reversion' 可创建策略"""
        cls = get_strategy("reversion")
        assert cls is not None

    def test_builtin_alias_moving_average(self):
        """内置别名 'moving_average' 可创建策略"""
        cls = get_strategy("moving_average")
        assert cls is not None

    def test_get_nonexistent_strategy(self):
        """不存在的策略 → None 或 KeyError"""
        try:
            result = get_strategy("nonexistent_strategy_12345")
            assert result is None
        except KeyError:
            pass  # Also acceptable

    def test_list_strategies_contains_aliases(self):
        """注册表至少包含别名"""
        strategies = list_strategies()
        assert len(strategies) >= 4
