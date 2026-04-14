"""DailyLossLimit + TrailingStopLoss 补充测试。

目标: 将两个止损规则的测试总数提升至 ≥8/rule。
- DailyLossLimit: 现有 3 个 (edge) → +5 = 8
- TrailingStopLoss: 现有 5 个 (3 main + 2 edge) → +3 = 8
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from aipha.risk.stop_loss import (
    DailyLossLimit,
    StopLossConfig,
    StopLossType,
    TrailingStopLoss,
    TriggerAction,
)


# ============================================================================
# DailyLossLimit 补充测试
# ============================================================================


class TestDailyLossLimitCoverage:
    """DailyLossLimit 补充逻辑测试。"""

    def _make(self, threshold: float = 0.05, capital: float = 1_000_000) -> DailyLossLimit:
        config = StopLossConfig(threshold=threshold, action=TriggerAction.HALT_TRADING)
        return DailyLossLimit(config, initial_capital=Decimal(str(capital)))

    def test_no_loss_no_trigger(self) -> None:
        """无亏损 → 不触发。"""
        dll = self._make(threshold=0.05)
        dll.reset_daily()
        dll.update_pnl(Decimal("0"), Decimal("0"))
        result = dll.check("A", Decimal("10.0"), 100)
        assert result is None

    def test_loss_below_threshold_no_trigger(self) -> None:
        """亏损低于阈值 → 不触发。"""
        dll = self._make(threshold=0.05)
        dll.reset_daily()
        dll.update_pnl(Decimal("-30000"), Decimal("0"))  # -3% < 5%
        result = dll.check("A", Decimal("10.0"), 100)
        assert result is None

    def test_loss_exceeds_threshold_triggers(self) -> None:
        """亏损超过阈值 → 触发 HALT_TRADING。"""
        dll = self._make(threshold=0.05)
        dll.reset_daily()
        dll.update_pnl(Decimal("-60000"), Decimal("0"))  # -6% > 5%
        result = dll.check("A", Decimal("10.0"), 100)
        assert result is not None
        assert result.triggered is True
        assert result.stop_type == StopLossType.DAILY_LOSS
        assert result.action == TriggerAction.HALT_TRADING

    def test_unrealized_loss_counted(self) -> None:
        """未实现亏损也计入。"""
        dll = self._make(threshold=0.05)
        dll.reset_daily()
        dll.update_pnl(Decimal("0"), Decimal("-60000"))  # 未实现亏损 -6%
        result = dll.check("A", Decimal("10.0"), 100)
        assert result is not None
        assert result.triggered is True

    def test_disabled_no_trigger(self) -> None:
        """config.enabled=False → 不触发。"""
        config = StopLossConfig(threshold=0.01, enabled=False)
        dll = DailyLossLimit(config, initial_capital=Decimal("1000000"))
        dll.reset_daily()
        dll.update_pnl(Decimal("-100000"), Decimal("0"))
        result = dll.check("A", Decimal("10.0"), 100)
        assert result is None

    def test_no_daily_pnl_no_trigger(self) -> None:
        """未初始化 daily_pnl → 不触发。"""
        dll = self._make(threshold=0.05)
        # 不调用 reset_daily
        result = dll.check("A", Decimal("10.0"), 100)
        assert result is None

    def test_reset_daily_clears(self) -> None:
        """reset_daily 重置后不再触发。"""
        dll = self._make(threshold=0.05)
        dll.reset_daily()
        dll.update_pnl(Decimal("-60000"), Decimal("0"))
        result = dll.check("A", Decimal("10.0"), 100)
        assert result is not None  # 先触发

        dll.reset_daily()  # 新的一天
        result = dll.check("A", Decimal("10.0"), 100)
        assert result is None  # 不再触发


# ============================================================================
# TrailingStopLoss 补充测试
# ============================================================================


class TestTrailingStopLossCoverage:
    """TrailingStopLoss 补充逻辑测试。"""

    def _make(self, threshold: float = 0.10) -> TrailingStopLoss:
        config = StopLossConfig(
            threshold=threshold,
            stop_type=StopLossType.TRAILING,
            action=TriggerAction.CLOSE_POSITION,
        )
        return TrailingStopLoss(config)

    def test_long_trailing_profit_run(self) -> None:
        """多头: 价格上涨→回落超阈值 → 触发。"""
        ts = self._make(threshold=0.10)
        ts.register_entry("A", Decimal("100"))
        # 上涨到 120
        result = ts.check("A", Decimal("120"), position_qty=100)
        assert result is None  # 无回落

        # 回落到 107 → (120-107)/120 = 10.83% > 10%
        result = ts.check("A", Decimal("107"), position_qty=100)
        assert result is not None
        assert result.triggered is True

    def test_long_trailing_no_trigger(self) -> None:
        """多头: 回落未超阈值 → 不触发。"""
        ts = self._make(threshold=0.10)
        ts.register_entry("A", Decimal("100"))
        ts.check("A", Decimal("120"), position_qty=100)
        result = ts.check("A", Decimal("110"), position_qty=100)
        # (120-110)/120 = 8.33% < 10%
        assert result is None

    def test_zero_position_no_trigger(self) -> None:
        """持仓为 0 → 不检查。"""
        ts = self._make(threshold=0.05)
        ts.register_entry("A", Decimal("100"))
        result = ts.check("A", Decimal("80"), position_qty=0)
        assert result is None

    def test_no_state_no_trigger(self) -> None:
        """未注册入场价 → 不触发。"""
        ts = self._make()
        result = ts.check("A", Decimal("80"), position_qty=100)
        assert result is None

    def test_highest_price_updates(self) -> None:
        """highest_price 正确追踪。"""
        ts = self._make(threshold=0.20)
        ts.register_entry("A", Decimal("100"))
        ts.check("A", Decimal("110"), 100)
        ts.check("A", Decimal("130"), 100)
        ts.check("A", Decimal("125"), 100)
        state = ts.get_state("A")
        assert state is not None
        assert state.highest_price == Decimal("130")

    def test_clear_position_removes_state(self) -> None:
        """clear_position 后无状态。"""
        ts = self._make()
        ts.register_entry("A", Decimal("100"))
        ts.clear_position("A")
        assert ts.get_state("A") is None
