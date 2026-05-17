"""系统级降级管理器

统一协调各子系统（数据、市场、风控、监控）的健康信号，
驱动系统在不同降级等级间自动转换。

状态机::

    NORMAL ──warning──► WARNING ──degrade──► DEGRADED ──halt──► HALTED
      ▲                   │                    │                 │
      └──────recover──────┘◄────recover────────┘◄───recover──────┘

信号来源：
- ``MarketRegimeDetector`` → 市场状态（BULL/NEUTRAL/BEAR）
- ``CircuitBreaker`` → 熔断状态
- ``DataHealthChecker`` → 数据质量
- ``TradingMonitor`` → 交易异常

降级行为：
- ``NORMAL``    → 正常交易
- ``WARNING``   → 告警 + 仓位缩减建议
- ``DEGRADED``  → 仅平仓/减仓允许，禁止新开仓
- ``HALTED``    → 策略完全暂停，不产生任何信号

使用示例::

    manager = DegradationManager(config=DegradationConfig())
    manager.report_signal(DegradationSignal(
        source="circuit_breaker",
        severity=DegradationLevel.HALTED,
        message="日内亏损超限",
    ))
    level = manager.current_level
    if level == DegradationLevel.HALTED:
        # 暂停策略
        ...
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ============================================================================
# 降级等级
# ============================================================================


class DegradationLevel(enum.IntEnum):
    """降级等级（数值越大 = 越严重）"""

    NORMAL = 0
    WARNING = 1
    DEGRADED = 2
    HALTED = 3


# ============================================================================
# 降级信号
# ============================================================================


@dataclass(frozen=True, slots=True)
class DegradationSignal:
    """降级信号

    Attributes:
        source: 信号来源标识（如 ``"circuit_breaker"``、``"data_health"``）
        severity: 建议降级等级
        message: 信号描述
        timestamp: 信号时间戳（秒，默认当前时间）
        metadata: 附加信息
    """

    source: str
    severity: DegradationLevel
    message: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


# ============================================================================
# 降级配置
# ============================================================================


@dataclass(frozen=True, slots=True)
class DegradationConfig:
    """降级管理器配置

    Attributes:
        auto_recover: 是否在信号解除后自动恢复
        recovery_cooldown_seconds: 恢复冷却时间（秒），防止频繁切换
        max_history: 信号历史记录上限
        position_scale_warning: WARNING 状态下仓位缩减比例（如 0.5 = 减半）
        position_scale_degraded: DEGRADED 状态下仓位缩减比例（如 0.0 = 全平）
    """

    auto_recover: bool = True
    recovery_cooldown_seconds: float = 300.0  # 5 分钟
    max_history: int = 1000
    position_scale_warning: float = 0.5
    position_scale_degraded: float = 0.0


# ============================================================================
# 状态转换事件
# ============================================================================


@dataclass(frozen=True, slots=True)
class LevelTransition:
    """状态转换记录

    Attributes:
        from_level: 转换前等级
        to_level: 转换后等级
        trigger: 触发信号
        timestamp: 转换时间
    """

    from_level: DegradationLevel
    to_level: DegradationLevel
    trigger: DegradationSignal
    timestamp: float


# ============================================================================
# 降级管理器
# ============================================================================


class DegradationManager:
    """系统级降级管理器

    聚合多个信号源的降级建议，维护系统当前降级等级，
    驱动仓位缩减与策略暂停。
    """

    def __init__(
        self,
        config: DegradationConfig | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config or DegradationConfig()
        self._clock = clock or time.time
        self._current_level = DegradationLevel.NORMAL
        self._active_signals: dict[str, DegradationSignal] = {}
        self._history: list[DegradationSignal] = []
        self._transitions: list[LevelTransition] = []
        self._last_level_change_time: float = 0.0
        self._listeners: list[Callable[[LevelTransition], None]] = []

    # ------------------------------------------------------------------
    # 信号接收
    # ------------------------------------------------------------------

    def report_signal(self, signal: DegradationSignal) -> None:
        """上报降级信号

        管理器会根据当前所有活跃信号的最高严重度重新计算系统等级。

        Args:
            signal: 降级信号
        """
        self._active_signals[signal.source] = signal
        self._add_history(signal)
        self._recalculate_level(signal)

    def clear_signal(self, source: str) -> None:
        """清除指定来源的降级信号（表示该子系统恢复正常）

        Args:
            source: 信号来源标识
        """
        if source in self._active_signals:
            removed = self._active_signals.pop(source)
            recovery_signal = DegradationSignal(
                source=source,
                severity=DegradationLevel.NORMAL,
                message=f"信号已清除: {removed.message}",
            )
            self._add_history(recovery_signal)
            if self._config.auto_recover:
                self._recalculate_level(recovery_signal)

    def clear_all_signals(self) -> None:
        """清除所有信号并恢复到 NORMAL"""
        self._active_signals.clear()
        recovery = DegradationSignal(
            source="system",
            severity=DegradationLevel.NORMAL,
            message="所有信号已清除",
        )
        self._add_history(recovery)
        self._set_level(DegradationLevel.NORMAL, recovery)

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    @property
    def current_level(self) -> DegradationLevel:
        """当前降级等级"""
        return self._current_level

    @property
    def position_scale(self) -> float:
        """当前仓位缩减比例

        Returns:
            1.0 (NORMAL), position_scale_warning (WARNING),
            position_scale_degraded (DEGRADED), 0.0 (HALTED)
        """
        if self._current_level == DegradationLevel.NORMAL:
            return 1.0
        if self._current_level == DegradationLevel.WARNING:
            return self._config.position_scale_warning
        if self._current_level == DegradationLevel.DEGRADED:
            return self._config.position_scale_degraded
        return 0.0  # HALTED

    @property
    def is_trading_allowed(self) -> bool:
        """是否允许交易（HALTED = 禁止）"""
        return self._current_level < DegradationLevel.HALTED

    @property
    def is_new_position_allowed(self) -> bool:
        """是否允许新开仓（DEGRADED/HALTED = 禁止）"""
        return self._current_level < DegradationLevel.DEGRADED

    @property
    def active_signals(self) -> dict[str, DegradationSignal]:
        """当前活跃信号（只读副本）"""
        return dict(self._active_signals)

    @property
    def transitions(self) -> list[LevelTransition]:
        """状态转换历史（只读副本）"""
        return list(self._transitions)

    @property
    def history(self) -> list[DegradationSignal]:
        """信号历史（只读副本）"""
        return list(self._history)

    def summary(self) -> dict[str, Any]:
        """系统降级状态摘要"""
        return {
            "current_level": self._current_level.name,
            "position_scale": self.position_scale,
            "is_trading_allowed": self.is_trading_allowed,
            "is_new_position_allowed": self.is_new_position_allowed,
            "active_signals_count": len(self._active_signals),
            "active_sources": list(self._active_signals.keys()),
            "transitions_count": len(self._transitions),
        }

    # ------------------------------------------------------------------
    # 监听器
    # ------------------------------------------------------------------

    def add_listener(self, listener: Callable[[LevelTransition], None]) -> None:
        """注册状态转换监听器"""
        self._listeners.append(listener)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _recalculate_level(self, trigger: DegradationSignal) -> None:
        """根据所有活跃信号重新计算系统等级"""
        if not self._active_signals:
            target = DegradationLevel.NORMAL
        else:
            target = max(s.severity for s in self._active_signals.values())

        if target != self._current_level:
            # 升级：立即生效
            # 降级（恢复）：遵守冷却时间
            if target > self._current_level:
                self._set_level(target, trigger)
            elif self._can_recover():
                self._set_level(target, trigger)

    def _can_recover(self) -> bool:
        """检查是否满足恢复冷却条件"""
        if self._last_level_change_time == 0.0:
            return True
        elapsed = self._clock() - self._last_level_change_time
        return elapsed >= self._config.recovery_cooldown_seconds

    def _set_level(
        self, level: DegradationLevel, trigger: DegradationSignal,
    ) -> None:
        if level == self._current_level:
            return
        transition = LevelTransition(
            from_level=self._current_level,
            to_level=level,
            trigger=trigger,
            timestamp=self._clock(),
        )
        self._transitions.append(transition)
        old = self._current_level
        self._current_level = level
        self._last_level_change_time = self._clock()

        logger.info(
            "降级等级变更: %s → %s [来源: %s, 原因: %s]",
            old.name, level.name, trigger.source, trigger.message,
        )

        for listener in self._listeners:
            try:
                listener(transition)
            except Exception as e:
                logger.error("降级监听器异常: %s", e)

    def _add_history(self, signal: DegradationSignal) -> None:
        self._history.append(signal)
        if len(self._history) > self._config.max_history:
            self._history = self._history[-self._config.max_history :]
