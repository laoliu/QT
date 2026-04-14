"""StopLoss / CircuitBreaker / DegradationManager 补充测试。

覆盖:
- 空头止损 (FixedPercent + Trailing)
- DailyLossLimit 完整生命周期
- CircuitBreaker: kill_switch, reset, force_resume, consecutive_losses
- StopLossManager: 多规则同时触发
- DegradationManager: 冷却期间升级, listener 异常隔离
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from aipha.risk.stop_loss import (
    CircuitBreaker,
    CircuitBreakerConfig,
    DailyLossLimit,
    FixedPercentStopLoss,
    StopLossConfig,
    StopLossManager,
    StopLossType,
    TrailingStopLoss,
    TriggerAction,
)
from aipha.risk.degradation import (
    DegradationConfig,
    DegradationLevel,
    DegradationManager,
    DegradationSignal,
)


# ============================================================================
# 空头止损
# ============================================================================

class TestShortPositionStopLoss:
    """FixedPercent / Trailing 的空头止损路径。"""

    def test_fixed_percent_short_triggered(self):
        """空头: 价格上涨超过阈值 → 触发。"""
        config = StopLossConfig(threshold=0.05, action=TriggerAction.CLOSE_POSITION)
        rule = FixedPercentStopLoss(config)
        rule.register_entry("A", Decimal("10.0"))
        result = rule.check("A", Decimal("10.6"), position_qty=-100)
        assert result is not None
        assert result.triggered is True
        assert result.loss_pct >= 0.05

    def test_fixed_percent_short_no_trigger(self):
        """空头: 价格在安全范围内 → 不触发。"""
        config = StopLossConfig(threshold=0.05)
        rule = FixedPercentStopLoss(config)
        rule.register_entry("A", Decimal("10.0"))
        result = rule.check("A", Decimal("10.3"), position_qty=-100)
        assert result is None

    def test_trailing_short_triggered(self):
        """空头追踪止损: 价格从最低价反弹超过阈值 → 触发。"""
        config = StopLossConfig(threshold=0.05, stop_type=StopLossType.TRAILING)
        rule = TrailingStopLoss(config)
        rule.register_entry("A", Decimal("10.0"))
        # 价格下跌到 9.0 (更新 lowest)
        rule.update_price("A", Decimal("9.0"))
        # 价格反弹到 9.50 → (9.50 - 9.0) / 9.0 = 5.56% > 5%
        result = rule.check("A", Decimal("9.50"), position_qty=-100)
        assert result is not None
        assert result.triggered is True

    def test_trailing_short_no_trigger(self):
        """空头追踪: 价格继续下跌 → 不触发。"""
        config = StopLossConfig(threshold=0.05, stop_type=StopLossType.TRAILING)
        rule = TrailingStopLoss(config)
        rule.register_entry("A", Decimal("10.0"))
        rule.update_price("A", Decimal("9.5"))
        result = rule.check("A", Decimal("9.3"), position_qty=-100)
        assert result is None


# ============================================================================
# DailyLossLimit 完整生命周期
# ============================================================================

class TestDailyLossLimitLifecycle:
    """DailyLossLimit 从初始化到触发的完整周期。"""

    def test_reset_update_check_trigger(self):
        """reset → update → check: 超阈值触发。"""
        config = StopLossConfig(threshold=0.03, action=TriggerAction.HALT_TRADING)
        rule = DailyLossLimit(config, initial_capital=Decimal("100000"))
        rule.reset_daily()
        rule.update_pnl(realized=Decimal("-2000"), unrealized=Decimal("-1500"))
        result = rule.check("ANY", Decimal("0"), 0)
        assert result is not None
        assert result.triggered is True
        assert result.symbol == "PORTFOLIO"

    def test_no_trigger_within_limit(self):
        """日内亏损在阈值内 → 不触发。"""
        config = StopLossConfig(threshold=0.03)
        rule = DailyLossLimit(config, initial_capital=Decimal("100000"))
        rule.reset_daily()
        rule.update_pnl(realized=Decimal("-1000"), unrealized=Decimal("-500"))
        result = rule.check("ANY", Decimal("0"), 0)
        assert result is None

    def test_auto_reset_on_first_update(self):
        """未显式 reset 时，update_pnl 自动初始化。"""
        config = StopLossConfig(threshold=0.10)
        rule = DailyLossLimit(config, initial_capital=Decimal("100000"))
        rule.update_pnl(realized=Decimal("-500"), unrealized=Decimal("-200"))
        assert rule._daily_pnl is not None
        assert rule._daily_pnl.total_pnl == Decimal("-700")


# ============================================================================
# CircuitBreaker 边界
# ============================================================================

class TestCircuitBreakerEdge:
    """CircuitBreaker 的 kill_switch, reset, force_resume 等。"""

    def test_kill_switch_no_auto_resume(self):
        """kill_switch → resume_at=None → is_active 持续为 True。"""
        cb = CircuitBreaker(CircuitBreakerConfig(cooldown_minutes=1))
        cb.kill_switch("manual halt")
        assert cb.is_active is True
        assert cb._state.resume_at is None
        assert "KILL_SWITCH" in cb._state.trigger_reason

    def test_force_resume_after_kill_switch(self):
        """kill_switch 后 force_resume 解除。"""
        cb = CircuitBreaker(CircuitBreakerConfig())
        cb.kill_switch("test")
        assert cb.is_active is True
        cb.force_resume()
        assert cb.is_active is False

    def test_reset_clears_state(self):
        """reset 清除所有状态。"""
        cb = CircuitBreaker(CircuitBreakerConfig())
        cb.record_trade_result(False)
        cb.record_trade_result(False)
        cb.record_trade_result(False)
        cb.update_equity(Decimal("100000"))
        cb.reset()
        assert cb._state.consecutive_losses == 0
        assert cb._state.peak_equity == Decimal("0")
        assert cb._state.is_active is False

    def test_consecutive_losses_trigger(self):
        """连续亏损达标 → 触发熔断。"""
        cb = CircuitBreaker(CircuitBreakerConfig(max_consecutive_losses=3))
        cb.record_trade_result(False)
        cb.record_trade_result(False)
        cb.record_trade_result(False)
        triggered = cb.check(Decimal("100000"))
        assert triggered is True

    def test_consecutive_losses_reset_on_win(self):
        """胜利重置连续亏损计数。"""
        cb = CircuitBreaker(CircuitBreakerConfig(max_consecutive_losses=5))
        cb.record_trade_result(False)
        cb.record_trade_result(False)
        cb.record_trade_result(True)
        assert cb._state.consecutive_losses == 0

    def test_disabled_breaker_never_triggers(self):
        """disabled=False → 永不触发。"""
        cb = CircuitBreaker(CircuitBreakerConfig(enabled=False))
        cb.record_trade_result(False)
        cb.record_trade_result(False)
        cb.record_trade_result(False)
        cb.record_trade_result(False)
        cb.record_trade_result(False)
        assert cb.check(Decimal("50000"), daily_loss_pct=0.99) is False


# ============================================================================
# StopLossManager 多规则
# ============================================================================

class TestStopLossManagerMultiRule:
    """多个规则同时触发。"""

    def test_two_rules_both_trigger(self):
        """FixedPercent + Trailing 同时触发 → 返回 2 个结果。"""
        fixed_cfg = StopLossConfig(threshold=0.05)
        trail_cfg = StopLossConfig(threshold=0.05, stop_type=StopLossType.TRAILING)
        mgr = StopLossManager()
        mgr.add_rule(FixedPercentStopLoss(fixed_cfg))
        mgr.add_rule(TrailingStopLoss(trail_cfg))
        mgr.register_entry("A", Decimal("10.0"))
        results = mgr.check("A", Decimal("9.0"), position_qty=100)
        assert len(results) == 2
        types = {r.stop_type for r in results}
        assert StopLossType.FIXED_PERCENT in types
        assert StopLossType.TRAILING in types

    def test_no_rules_returns_empty(self):
        """无规则 → 空列表。"""
        mgr = StopLossManager()
        results = mgr.check("A", Decimal("10.0"), 100)
        assert results == []

    def test_circuit_breaker_integration(self):
        """StopLossManager 通过 circuit_breaker 代理检查。"""
        mgr = StopLossManager()
        cb = CircuitBreaker(CircuitBreakerConfig(max_daily_loss=0.03))
        mgr.set_circuit_breaker(cb)
        halted = mgr.check_circuit_breaker(Decimal("100000"), daily_loss_pct=0.05)
        assert halted is True
        assert mgr.is_halted is True


# ============================================================================
# DegradationManager 补充
# ============================================================================

class TestDegradationEdge:
    """DegradationManager 冷却期间升级 + listener 异常隔离。"""

    def test_upgrade_during_recovery_cooldown(self):
        """冷却期间收到更高级信号 → 立即升级（升级不受冷却限制）。"""
        # 用短冷却期
        config = DegradationConfig(auto_recover=True, recovery_cooldown_seconds=9999)
        mgr = DegradationManager(config)
        # 先升到 WARNING
        mgr.report_signal(DegradationSignal(
            source="test", severity=DegradationLevel.WARNING, message="warn"))
        assert mgr.current_level == DegradationLevel.WARNING
        # 清除 → 因冷却尚未过，仍在 WARNING
        mgr.clear_signal("test")
        assert mgr.current_level >= DegradationLevel.WARNING  # 冷却中不降级
        # 收到 HALTED 信号 → 应立即升到 HALTED
        mgr.report_signal(DegradationSignal(
            source="critical", severity=DegradationLevel.HALTED, message="halt"))
        assert mgr.current_level == DegradationLevel.HALTED

    def test_listener_exception_isolation(self):
        """第一个 listener 抛异常不影响第二个被调用。"""
        config = DegradationConfig()
        mgr = DegradationManager(config)
        calls = []

        def bad_listener(transition):
            raise RuntimeError("boom")

        def good_listener(transition):
            calls.append(transition)

        mgr.add_listener(bad_listener)
        mgr.add_listener(good_listener)
        mgr.report_signal(DegradationSignal(
            source="t", severity=DegradationLevel.WARNING, message="w"))
        assert len(calls) == 1  # good_listener 被调用

    def test_position_scale_by_level(self):
        """不同级别的 position_scale。"""
        config = DegradationConfig(
            position_scale_warning=0.5,
            position_scale_degraded=0.0,
        )
        mgr = DegradationManager(config)
        assert mgr.position_scale == 1.0  # NORMAL
        mgr.report_signal(DegradationSignal(
            source="a", severity=DegradationLevel.WARNING, message="w"))
        assert mgr.position_scale == 0.5  # WARNING
        mgr.report_signal(DegradationSignal(
            source="b", severity=DegradationLevel.DEGRADED, message="d"))
        assert mgr.position_scale == 0.0  # DEGRADED

    def test_is_trading_allowed(self):
        """HALTED 级别不允许交易。"""
        config = DegradationConfig()
        mgr = DegradationManager(config)
        assert mgr.is_trading_allowed is True
        mgr.report_signal(DegradationSignal(
            source="x", severity=DegradationLevel.HALTED, message="h"))
        assert mgr.is_trading_allowed is False

    def test_summary_output(self):
        """summary() 返回非空结构。"""
        config = DegradationConfig()
        mgr = DegradationManager(config)
        mgr.report_signal(DegradationSignal(
            source="s", severity=DegradationLevel.WARNING, message="w"))
        s = mgr.summary()
        assert s is not None
        # summary 可能是 dict 或 str
        if isinstance(s, dict):
            assert "current_level" in s
        else:
            assert len(s) > 0

# ============================================================================
# CircuitBreaker 可注入时钟测试
# ============================================================================

class TestCircuitBreakerInjectableClock:
    """验证 CircuitBreaker 使用注入时钟而非系统时间。"""

    def test_cooldown_with_injected_clock(self):
        """注入时钟前进到 resume_at 后，熔断自动解除。"""
        from datetime import datetime, timedelta

        simulated_time = [datetime(2024, 1, 15, 10, 0, 0)]

        def mock_clock():
            return simulated_time[0]

        config = CircuitBreakerConfig(cooldown_minutes=30, max_drawdown=0.05)
        cb = CircuitBreaker(config, clock=mock_clock)

        # 触发熔断
        cb._state.peak_equity = Decimal("1000000")
        triggered = cb.check(Decimal("940000"))  # 6% 回撤 > 5%
        assert triggered is True
        assert cb.is_active is True

        # 时间还没到冷却期
        simulated_time[0] = datetime(2024, 1, 15, 10, 20, 0)  # +20min
        assert cb.is_active is True

        # 时间到达冷却期
        simulated_time[0] = datetime(2024, 1, 15, 10, 30, 1)  # +30min, past
        assert cb.is_active is False

    def test_default_clock_is_datetime_now(self):
        """不传 clock 时使用 datetime.now 作为默认。"""
        config = CircuitBreakerConfig()
        cb = CircuitBreaker(config)
        assert cb._clock is not None
        # 调用应返回接近当前时间的 datetime
        now = cb._clock()
        assert isinstance(now, datetime)

    def test_trigger_timestamp_uses_injected_clock(self):
        """触发时间戳使用注入时钟而非系统时钟。"""
        fixed_time = datetime(2024, 6, 1, 14, 30, 0)
        config = CircuitBreakerConfig(max_drawdown=0.05)
        cb = CircuitBreaker(config, clock=lambda: fixed_time)

        cb._state.peak_equity = Decimal("1000000")
        cb.check(Decimal("940000"))

        assert cb._state.triggered_at == fixed_time
        assert cb._state.resume_at == fixed_time + timedelta(minutes=config.cooldown_minutes)

    def test_kill_switch_uses_injected_clock(self):
        """kill_switch 时间戳使用注入时钟。"""
        fixed_time = datetime(2024, 3, 15, 9, 30, 0)
        config = CircuitBreakerConfig()
        cb = CircuitBreaker(config, clock=lambda: fixed_time)

        cb.kill_switch("test reason")
        assert cb._state.triggered_at == fixed_time
        assert cb._state.resume_at is None  # kill switch 不自动恢复


# ============================================================================
# StopLoss 时钟注入测试
# ============================================================================


class TestStopLossClockInjection:
    """验证 StopLossRule 子类使用注入的时钟而非 datetime.now()"""

    def test_fixed_percent_uses_injected_clock(self):
        """FixedPercentStopLoss 触发时使用注入的时钟。"""
        bar_time = datetime(2024, 7, 15, 14, 30, 0)
        config = StopLossConfig(threshold=0.05)
        rule = FixedPercentStopLoss(config, clock=lambda: bar_time)

        rule.register_entry("A", Decimal("100.0"))
        result = rule.check("A", Decimal("90.0"), position_qty=100)

        assert result is not None
        assert result.triggered is True
        state = rule.get_state("A")
        assert state.triggered_at == bar_time

    def test_fixed_percent_default_clock(self):
        """不传 clock 时默认使用 datetime.now (向后兼容)。"""
        config = StopLossConfig(threshold=0.05)
        rule = FixedPercentStopLoss(config)

        rule.register_entry("B", Decimal("100.0"))
        before = datetime.now()
        result = rule.check("B", Decimal("90.0"), position_qty=100)
        after = datetime.now()

        assert result is not None
        state = rule.get_state("B")
        assert before <= state.triggered_at <= after

    def test_trailing_uses_injected_clock(self):
        """TrailingStopLoss 触发时使用注入的时钟。"""
        bar_time = datetime(2024, 8, 20, 10, 0, 0)
        config = StopLossConfig(threshold=0.05, stop_type=StopLossType.TRAILING)
        rule = TrailingStopLoss(config, clock=lambda: bar_time)

        rule.register_entry("C", Decimal("100.0"))
        # 先推高到 110，再跌到 100 (从峰值跌 ~9%)
        rule.update_price("C", Decimal("110.0"))
        result = rule.check("C", Decimal("100.0"), position_qty=100)

        assert result is not None
        assert result.triggered is True
        state = rule.get_state("C")
        assert state.triggered_at == bar_time

    def test_daily_loss_limit_uses_injected_clock(self):
        """DailyLossLimit.reset_daily 使用注入的时钟。"""
        bar_time = datetime(2024, 9, 10, 9, 30, 0)
        config = StopLossConfig(threshold=0.02, stop_type=StopLossType.DAILY_LOSS)
        rule = DailyLossLimit(
            config, initial_capital=Decimal("100000"),
            clock=lambda: bar_time,
        )

        rule.reset_daily()
        assert rule._daily_pnl is not None
        assert rule._daily_pnl.date == bar_time

    def test_manager_passes_clock_to_rules(self):
        """StopLossManager 中的规则可独立注入不同时钟。"""
        from aipha.risk.stop_loss import StopLossManager

        time_a = datetime(2024, 1, 1, 10, 0, 0)
        time_b = datetime(2024, 6, 1, 14, 0, 0)

        config_a = StopLossConfig(threshold=0.05)
        config_b = StopLossConfig(threshold=0.03, stop_type=StopLossType.TRAILING)

        rule_a = FixedPercentStopLoss(config_a, clock=lambda: time_a)
        rule_b = TrailingStopLoss(config_b, clock=lambda: time_b)

        mgr = StopLossManager()
        mgr.add_rule(rule_a)
        mgr.add_rule(rule_b)

        mgr.register_entry("X", Decimal("100.0"))
        # 跌幅足以触发两个规则
        results = mgr.check("X", Decimal("90.0"), position_qty=100)
        assert len(results) == 2

        for r in results:
            state = None
            if r.stop_type == StopLossType.FIXED_PERCENT:
                state = rule_a.get_state("X")
                assert state.triggered_at == time_a
            elif r.stop_type == StopLossType.TRAILING:
                state = rule_b.get_state("X")
                assert state.triggered_at == time_b