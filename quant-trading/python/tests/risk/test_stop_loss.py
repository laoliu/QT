"""
止损规则单元测试
"""

from decimal import Decimal

import pytest

from aipha.risk.stop_loss import (
    DailyLossLimit,
    FixedPercentStopLoss,
    StopLossConfig,
    StopLossManager,
    StopLossType,
    TrailingStopLoss,
    TriggerAction,
)


# ============================================================================
# FixedPercentStopLoss Tests
# ============================================================================


class TestFixedPercentStopLoss:
    """固定百分比止损测试"""

    def _make_rule(self, threshold: float = 0.10) -> FixedPercentStopLoss:
        config = StopLossConfig(
            threshold=threshold,
            stop_type=StopLossType.FIXED_PERCENT,
        )
        return FixedPercentStopLoss(config)

    def test_fixed_percent_no_trigger(self):
        """价格高于止损线，不触发"""
        rule = self._make_rule(threshold=0.10)
        rule.register_entry("A", Decimal("100"))

        # 价格只跌 5%，未达 10% 阈值
        result = rule.check("A", Decimal("95"), position_qty=100)
        assert result is None

    def test_fixed_percent_triggers(self):
        """价格跌破止损线，触发"""
        rule = self._make_rule(threshold=0.10)
        rule.register_entry("A", Decimal("100"))

        # 价格跌 12%，超过 10% 阈值
        result = rule.check("A", Decimal("88"), position_qty=100)
        assert result is not None
        assert result.triggered is True
        assert result.stop_type == StopLossType.FIXED_PERCENT
        assert result.action == TriggerAction.CLOSE_POSITION
        assert result.loss_pct >= 0.10

    def test_fixed_percent_exact_threshold(self):
        """价格刚好等于阈值，触发"""
        rule = self._make_rule(threshold=0.10)
        rule.register_entry("A", Decimal("100"))

        # loss_pct = (100 - 90) / 100 = 0.10 == threshold
        result = rule.check("A", Decimal("90"), position_qty=100)
        assert result is not None
        assert result.triggered is True

    def test_fixed_percent_no_state(self):
        """未注册入场价，不触发"""
        rule = self._make_rule(threshold=0.10)
        result = rule.check("A", Decimal("50"), position_qty=100)
        assert result is None

    def test_fixed_percent_zero_position(self):
        """持仓为 0，不检查"""
        rule = self._make_rule(threshold=0.10)
        rule.register_entry("A", Decimal("100"))
        result = rule.check("A", Decimal("50"), position_qty=0)
        assert result is None

    def test_fixed_percent_disabled(self):
        """规则禁用"""
        config = StopLossConfig(enabled=False, threshold=0.10)
        rule = FixedPercentStopLoss(config)
        rule.register_entry("A", Decimal("100"))
        result = rule.check("A", Decimal("50"), position_qty=100)
        assert result is None


# ============================================================================
# TrailingStopLoss Tests
# ============================================================================


class TestTrailingStopLoss:
    """追踪止损测试"""

    def _make_rule(self, threshold: float = 0.10) -> TrailingStopLoss:
        config = StopLossConfig(
            threshold=threshold,
            stop_type=StopLossType.TRAILING,
        )
        return TrailingStopLoss(config)

    def test_trailing_price_updates_high(self):
        """最高价跟踪上涨"""
        rule = self._make_rule(threshold=0.10)
        rule.register_entry("A", Decimal("100"))

        # 价格上涨至 120
        rule.update_price("A", Decimal("110"))
        rule.update_price("A", Decimal("120"))

        state = rule.get_state("A")
        assert state is not None
        assert state.highest_price == Decimal("120")

    def test_trailing_triggers_from_peak(self):
        """从最高价回落触发止损"""
        rule = self._make_rule(threshold=0.10)
        rule.register_entry("A", Decimal("100"))

        # 先更新至最高价 120
        rule.update_price("A", Decimal("120"))

        # check 内部也会先 update_price，然后计算追踪止损价
        # 止损价 = 120 * 0.9 = 108
        # 当前价 105 < 108 → 触发
        result = rule.check("A", Decimal("105"), position_qty=100)
        assert result is not None
        assert result.triggered is True
        assert result.stop_type == StopLossType.TRAILING

    def test_trailing_no_trigger_above_stop(self):
        """价格在止损线上方，不触发"""
        rule = self._make_rule(threshold=0.10)
        rule.register_entry("A", Decimal("100"))

        rule.update_price("A", Decimal("120"))

        # 止损价 = 120 * 0.9 = 108, 当前价 110 > 108 → 不触发
        result = rule.check("A", Decimal("110"), position_qty=100)
        assert result is None


# ============================================================================
# StopLossManager Tests
# ============================================================================


class TestStopLossManager:
    """止损管理器测试"""

    def test_manager_register_and_check(self):
        """注册入场并检查"""
        config = StopLossConfig(threshold=0.10, stop_type=StopLossType.FIXED_PERCENT)
        rule = FixedPercentStopLoss(config)
        manager = StopLossManager()
        manager.add_rule(rule)

        manager.register_entry("A", Decimal("100"))

        # 价格在安全区间
        results = manager.check("A", Decimal("95"), position_qty=100)
        assert len(results) == 0

        # 价格跌破止损
        results = manager.check("A", Decimal("85"), position_qty=100)
        assert len(results) == 1
        assert results[0].triggered is True

    def test_manager_clear_position(self):
        """清除持仓后不再触发"""
        config = StopLossConfig(threshold=0.10, stop_type=StopLossType.FIXED_PERCENT)
        rule = FixedPercentStopLoss(config)
        manager = StopLossManager()
        manager.add_rule(rule)

        manager.register_entry("A", Decimal("100"))
        manager.clear_position("A")

        # 即使价格大幅下跌也不触发
        results = manager.check("A", Decimal("50"), position_qty=100)
        assert len(results) == 0

    def test_manager_multiple_rules(self):
        """多条规则同时检查"""
        fixed_config = StopLossConfig(threshold=0.10, stop_type=StopLossType.FIXED_PERCENT)
        trailing_config = StopLossConfig(threshold=0.08, stop_type=StopLossType.TRAILING)

        manager = StopLossManager()
        manager.add_rule(FixedPercentStopLoss(fixed_config))
        manager.add_rule(TrailingStopLoss(trailing_config))

        manager.register_entry("A", Decimal("100"))

        # 先涨到 120 再跌到 85
        manager.update_price("A", Decimal("120"))
        results = manager.check("A", Decimal("85"), position_qty=100)

        # 两条规则都应触发 (fixed: 15% > 10%, trailing: from 120 drop to 85 = 29% > 8%)
        assert len(results) == 2
        stop_types = {r.stop_type for r in results}
        assert StopLossType.FIXED_PERCENT in stop_types
        assert StopLossType.TRAILING in stop_types

    def test_manager_update_price(self):
        """更新价格传播到所有规则"""
        config = StopLossConfig(threshold=0.10, stop_type=StopLossType.TRAILING)
        rule = TrailingStopLoss(config)
        manager = StopLossManager()
        manager.add_rule(rule)

        manager.register_entry("A", Decimal("100"))
        manager.update_price("A", Decimal("150"))

        state = rule.get_state("A")
        assert state is not None
        assert state.highest_price == Decimal("150")

    def test_manager_add_rule_returns_self(self):
        """add_rule 返回自身（支持链式调用）"""
        manager = StopLossManager()
        config = StopLossConfig(threshold=0.10)
        result = manager.add_rule(FixedPercentStopLoss(config))
        assert result is manager
