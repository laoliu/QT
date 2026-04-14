"""DegradationManager 测试

覆盖：
- DegradationLevel 枚举排序
- DegradationSignal 数据结构
- DegradationConfig 默认值与自定义
- DegradationManager:
  - report_signal 升级
  - clear_signal 恢复
  - 多源信号聚合（取最高严重度）
  - 冷却时间控制
  - 仓位缩减比例
  - 交易权限判断
  - 监听器通知
  - 信号历史与转换记录
  - summary 输出
"""

from __future__ import annotations

import time

import pytest

from aipha.risk.degradation import (
    DegradationConfig,
    DegradationLevel,
    DegradationManager,
    DegradationSignal,
    LevelTransition,
)


# ============================================================================
# TestDegradationLevel
# ============================================================================


class TestDegradationLevel:
    """降级等级枚举"""

    def test_ordering(self) -> None:
        assert DegradationLevel.NORMAL < DegradationLevel.WARNING
        assert DegradationLevel.WARNING < DegradationLevel.DEGRADED
        assert DegradationLevel.DEGRADED < DegradationLevel.HALTED

    def test_values(self) -> None:
        assert DegradationLevel.NORMAL == 0
        assert DegradationLevel.WARNING == 1
        assert DegradationLevel.DEGRADED == 2
        assert DegradationLevel.HALTED == 3

    def test_max(self) -> None:
        levels = [DegradationLevel.WARNING, DegradationLevel.HALTED, DegradationLevel.NORMAL]
        assert max(levels) == DegradationLevel.HALTED


# ============================================================================
# TestDegradationSignal
# ============================================================================


class TestDegradationSignal:
    """降级信号数据结构"""

    def test_frozen(self) -> None:
        s = DegradationSignal(source="test", severity=DegradationLevel.WARNING)
        with pytest.raises(AttributeError):
            s.source = "x"  # type: ignore[misc]

    def test_default_message(self) -> None:
        s = DegradationSignal(source="test", severity=DegradationLevel.NORMAL)
        assert s.message == ""

    def test_timestamp_auto(self) -> None:
        before = time.time()
        s = DegradationSignal(source="test", severity=DegradationLevel.WARNING)
        after = time.time()
        assert before <= s.timestamp <= after

    def test_metadata_default(self) -> None:
        s = DegradationSignal(source="test", severity=DegradationLevel.WARNING)
        assert s.metadata == {}


# ============================================================================
# TestDegradationConfig
# ============================================================================


class TestDegradationConfig:
    """降级配置"""

    def test_defaults(self) -> None:
        cfg = DegradationConfig()
        assert cfg.auto_recover is True
        assert cfg.recovery_cooldown_seconds == 300.0
        assert cfg.max_history == 1000
        assert cfg.position_scale_warning == 0.5
        assert cfg.position_scale_degraded == 0.0

    def test_frozen(self) -> None:
        cfg = DegradationConfig()
        with pytest.raises(AttributeError):
            cfg.auto_recover = False  # type: ignore[misc]

    def test_custom(self) -> None:
        cfg = DegradationConfig(
            position_scale_warning=0.7,
            position_scale_degraded=0.2,
            recovery_cooldown_seconds=60.0,
        )
        assert cfg.position_scale_warning == 0.7
        assert cfg.position_scale_degraded == 0.2


# ============================================================================
# TestDegradationManager — 核心状态机
# ============================================================================


class TestDegradationManagerCore:
    """DegradationManager 核心"""

    def test_initial_state(self) -> None:
        mgr = DegradationManager()
        assert mgr.current_level == DegradationLevel.NORMAL
        assert mgr.position_scale == 1.0
        assert mgr.is_trading_allowed
        assert mgr.is_new_position_allowed

    def test_upgrade_to_warning(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="test", severity=DegradationLevel.WARNING, message="test",
        ))
        assert mgr.current_level == DegradationLevel.WARNING

    def test_upgrade_to_degraded(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="test", severity=DegradationLevel.DEGRADED,
        ))
        assert mgr.current_level == DegradationLevel.DEGRADED
        assert not mgr.is_new_position_allowed
        assert mgr.is_trading_allowed

    def test_upgrade_to_halted(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="circuit_breaker", severity=DegradationLevel.HALTED,
            message="日内亏损超限",
        ))
        assert mgr.current_level == DegradationLevel.HALTED
        assert not mgr.is_trading_allowed
        assert not mgr.is_new_position_allowed


# ============================================================================
# TestDegradationManager — 仓位缩减
# ============================================================================


class TestPositionScale:
    """仓位缩减比例"""

    def test_normal_scale(self) -> None:
        mgr = DegradationManager()
        assert mgr.position_scale == 1.0

    def test_warning_scale_default(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="a", severity=DegradationLevel.WARNING,
        ))
        assert mgr.position_scale == 0.5

    def test_degraded_scale_default(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="a", severity=DegradationLevel.DEGRADED,
        ))
        assert mgr.position_scale == 0.0

    def test_halted_scale(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="a", severity=DegradationLevel.HALTED,
        ))
        assert mgr.position_scale == 0.0

    def test_custom_scales(self) -> None:
        cfg = DegradationConfig(
            position_scale_warning=0.7,
            position_scale_degraded=0.3,
            recovery_cooldown_seconds=0,
        )
        mgr = DegradationManager(config=cfg)
        mgr.report_signal(DegradationSignal(
            source="a", severity=DegradationLevel.WARNING,
        ))
        assert mgr.position_scale == 0.7


# ============================================================================
# TestDegradationManager — 信号聚合
# ============================================================================


class TestSignalAggregation:
    """多源信号聚合"""

    def test_max_severity_wins(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="data", severity=DegradationLevel.WARNING,
        ))
        mgr.report_signal(DegradationSignal(
            source="circuit_breaker", severity=DegradationLevel.HALTED,
        ))
        assert mgr.current_level == DegradationLevel.HALTED

    def test_clear_reduces_level(self) -> None:
        cfg = DegradationConfig(recovery_cooldown_seconds=0)
        mgr = DegradationManager(config=cfg)
        mgr.report_signal(DegradationSignal(
            source="data", severity=DegradationLevel.WARNING,
        ))
        mgr.report_signal(DegradationSignal(
            source="market", severity=DegradationLevel.HALTED,
        ))
        assert mgr.current_level == DegradationLevel.HALTED

        mgr.clear_signal("market")
        assert mgr.current_level == DegradationLevel.WARNING

    def test_clear_all_resets(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="a", severity=DegradationLevel.HALTED,
        ))
        mgr.clear_all_signals()
        assert mgr.current_level == DegradationLevel.NORMAL

    def test_clear_nonexistent_source(self) -> None:
        mgr = DegradationManager()
        mgr.clear_signal("nonexistent")  # no error
        assert mgr.current_level == DegradationLevel.NORMAL

    def test_active_signals(self) -> None:
        mgr = DegradationManager()
        s1 = DegradationSignal(source="a", severity=DegradationLevel.WARNING)
        s2 = DegradationSignal(source="b", severity=DegradationLevel.DEGRADED)
        mgr.report_signal(s1)
        mgr.report_signal(s2)
        active = mgr.active_signals
        assert len(active) == 2
        assert "a" in active
        assert "b" in active

    def test_same_source_overwrites(self) -> None:
        cfg = DegradationConfig(recovery_cooldown_seconds=0)
        mgr = DegradationManager(config=cfg)
        mgr.report_signal(DegradationSignal(
            source="data", severity=DegradationLevel.HALTED,
        ))
        mgr.report_signal(DegradationSignal(
            source="data", severity=DegradationLevel.WARNING,
        ))
        assert mgr.current_level == DegradationLevel.WARNING


# ============================================================================
# TestDegradationManager — 恢复与冷却
# ============================================================================


class TestRecovery:
    """恢复与冷却"""

    def test_recovery_with_zero_cooldown(self) -> None:
        cfg = DegradationConfig(recovery_cooldown_seconds=0)
        mgr = DegradationManager(config=cfg)
        mgr.report_signal(DegradationSignal(
            source="x", severity=DegradationLevel.HALTED,
        ))
        mgr.clear_signal("x")
        assert mgr.current_level == DegradationLevel.NORMAL

    def test_recovery_blocked_by_cooldown(self) -> None:
        cfg = DegradationConfig(recovery_cooldown_seconds=9999)
        mgr = DegradationManager(config=cfg)
        mgr.report_signal(DegradationSignal(
            source="x", severity=DegradationLevel.HALTED,
        ))
        mgr.clear_signal("x")
        # 冷却未过 → 不恢复
        assert mgr.current_level == DegradationLevel.HALTED

    def test_no_auto_recover(self) -> None:
        cfg = DegradationConfig(auto_recover=False, recovery_cooldown_seconds=0)
        mgr = DegradationManager(config=cfg)
        mgr.report_signal(DegradationSignal(
            source="x", severity=DegradationLevel.WARNING,
        ))
        mgr.clear_signal("x")
        # auto_recover=False → 不恢复
        assert mgr.current_level == DegradationLevel.WARNING


# ============================================================================
# TestDegradationManager — 监听器
# ============================================================================


class TestListeners:
    """状态转换监听器"""

    def test_listener_called(self) -> None:
        events: list[LevelTransition] = []
        mgr = DegradationManager()
        mgr.add_listener(events.append)

        mgr.report_signal(DegradationSignal(
            source="test", severity=DegradationLevel.WARNING,
        ))
        assert len(events) == 1
        assert events[0].from_level == DegradationLevel.NORMAL
        assert events[0].to_level == DegradationLevel.WARNING

    def test_listener_exception_does_not_break(self) -> None:
        def bad_listener(t: LevelTransition) -> None:
            raise ValueError("boom")

        mgr = DegradationManager()
        mgr.add_listener(bad_listener)

        # 不应抛出
        mgr.report_signal(DegradationSignal(
            source="test", severity=DegradationLevel.HALTED,
        ))
        assert mgr.current_level == DegradationLevel.HALTED


# ============================================================================
# TestDegradationManager — 历史与审计
# ============================================================================


class TestHistoryAndAudit:
    """信号历史与转换记录"""

    def test_history_records(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="a", severity=DegradationLevel.WARNING,
        ))
        mgr.report_signal(DegradationSignal(
            source="b", severity=DegradationLevel.DEGRADED,
        ))
        assert len(mgr.history) == 2

    def test_transitions_records(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="a", severity=DegradationLevel.WARNING,
        ))
        mgr.report_signal(DegradationSignal(
            source="b", severity=DegradationLevel.HALTED,
        ))
        assert len(mgr.transitions) == 2

    def test_history_max_limit(self) -> None:
        cfg = DegradationConfig(max_history=5, recovery_cooldown_seconds=0)
        mgr = DegradationManager(config=cfg)
        for i in range(10):
            mgr.report_signal(DegradationSignal(
                source=f"s{i}", severity=DegradationLevel.WARNING,
            ))
        assert len(mgr.history) <= 5

    def test_summary(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="data", severity=DegradationLevel.WARNING,
        ))
        s = mgr.summary()
        assert s["current_level"] == "WARNING"
        assert s["position_scale"] == 0.5
        assert s["is_trading_allowed"] is True
        assert s["active_signals_count"] == 1
        assert "data" in s["active_sources"]

    def test_history_immutability(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="x", severity=DegradationLevel.WARNING,
        ))
        h = mgr.history
        h.clear()
        assert len(mgr.history) == 1

    def test_transitions_immutability(self) -> None:
        mgr = DegradationManager()
        mgr.report_signal(DegradationSignal(
            source="x", severity=DegradationLevel.WARNING,
        ))
        t = mgr.transitions
        t.clear()
        assert len(mgr.transitions) == 1


class TestClockInjection:
    """时间源注入测试 —— 确保 DegradationManager 使用可注入的时钟"""

    def test_default_clock_is_time_time(self) -> None:
        """默认时钟应为 time.time。"""
        mgr = DegradationManager()
        assert mgr._clock is time.time

    def test_custom_clock_used_in_transitions(self) -> None:
        """自定义时钟的返回值应出现在 transition.timestamp 中。"""
        fake_time = 1000000.0
        mgr = DegradationManager(clock=lambda: fake_time)
        mgr.report_signal(DegradationSignal(
            source="test", severity=DegradationLevel.WARNING,
        ))
        assert mgr.transitions[0].timestamp == fake_time

    def test_recovery_respects_injected_clock(self) -> None:
        """注入时钟应控制冷却时间判断。"""
        t = [100.0]
        cfg = DegradationConfig(recovery_cooldown_seconds=60, auto_recover=True)
        mgr = DegradationManager(config=cfg, clock=lambda: t[0])

        # 升级到 WARNING
        mgr.report_signal(DegradationSignal(
            source="x", severity=DegradationLevel.WARNING,
        ))
        assert mgr.current_level == DegradationLevel.WARNING

        # 30秒后清除信号 → 冷却未过，不恢复
        t[0] = 130.0
        mgr.clear_signal("x")
        assert mgr.current_level == DegradationLevel.WARNING

        # 再推进到冷却时间之后 → 新信号触发重新计算
        t[0] = 200.0
        mgr.report_signal(DegradationSignal(
            source="y", severity=DegradationLevel.WARNING,
        ))
        mgr.clear_signal("y")
        assert mgr.current_level == DegradationLevel.NORMAL

    def test_clock_injection_does_not_affect_default_behavior(self) -> None:
        """不传 clock 参数时行为与之前一致（兼容性）。"""
        cfg = DegradationConfig(recovery_cooldown_seconds=0)
        mgr = DegradationManager(config=cfg)
        mgr.report_signal(DegradationSignal(
            source="a", severity=DegradationLevel.HALTED,
        ))
        mgr.clear_signal("a")
        assert mgr.current_level == DegradationLevel.NORMAL
