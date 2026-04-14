"""系统降级状态机 — NORMAL → WARNING → DEGRADED → HALTED"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class DegradationLevel(IntEnum):
    NORMAL = 0
    WARNING = 1
    DEGRADED = 2
    HALTED = 3


@dataclass
class DegradationConfig:
    drawdown_warning: float = 0.05     # 回撤 5% → WARNING
    drawdown_degraded: float = 0.10    # 回撤 10% → DEGRADED
    drawdown_halt: float = 0.20        # 回撤 20% → HALTED
    position_scale_warning: float = 0.80   # WARNING 时仓位缩放
    position_scale_degraded: float = 0.50  # DEGRADED 时仓位缩放
    recovery_threshold: float = 0.5    # 回撤恢复50%后才升级状态
    min_hold_days: int = 3             # 状态至少维持N天


@dataclass
class DegradationState:
    level: DegradationLevel = DegradationLevel.NORMAL
    position_scale: float = 1.0
    peak_nav: float = 0.0
    current_drawdown: float = 0.0
    days_in_state: int = 0
    transition_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.name,
            "position_scale": round(self.position_scale, 4),
            "current_drawdown": round(self.current_drawdown, 4),
            "days_in_state": self.days_in_state,
        }


class DegradationManager:
    """系统降级管理器。

    根据组合回撤动态调整交易状态：
    - NORMAL: 正常交易
    - WARNING: 降低仓位到 80%
    - DEGRADED: 降低仓位到 50%，只允许平仓
    - HALTED: 完全停止交易
    """

    def __init__(self, config: DegradationConfig | None = None) -> None:
        self._config = config or DegradationConfig()
        self._state = DegradationState()

    @property
    def state(self) -> DegradationState:
        return self._state

    @property
    def level(self) -> DegradationLevel:
        return self._state.level

    @property
    def position_scale(self) -> float:
        return self._state.position_scale

    def update(self, nav: float, date_str: str = "") -> DegradationLevel:
        """每日更新。返回当前降级级别。"""
        cfg = self._config
        state = self._state

        if nav > state.peak_nav:
            state.peak_nav = nav

        state.current_drawdown = (state.peak_nav - nav) / state.peak_nav if state.peak_nav > 0 else 0.0
        dd = state.current_drawdown

        # 决定目标状态
        if dd >= cfg.drawdown_halt:
            target = DegradationLevel.HALTED
        elif dd >= cfg.drawdown_degraded:
            target = DegradationLevel.DEGRADED
        elif dd >= cfg.drawdown_warning:
            target = DegradationLevel.WARNING
        else:
            target = DegradationLevel.NORMAL

        old_level = state.level
        state.days_in_state += 1

        # 升级（恶化）立即执行
        if target > state.level:
            self._transition(target, date_str, f"drawdown={dd:.2%}")
        # 降级（恢复）需满足条件
        elif target < state.level:
            if state.days_in_state >= cfg.min_hold_days:
                # 只能逐级恢复
                new_level = DegradationLevel(state.level - 1)
                self._transition(new_level, date_str, f"recovery, drawdown={dd:.2%}")

        # 设置仓位缩放
        if state.level == DegradationLevel.WARNING:
            state.position_scale = cfg.position_scale_warning
        elif state.level == DegradationLevel.DEGRADED:
            state.position_scale = cfg.position_scale_degraded
        elif state.level == DegradationLevel.HALTED:
            state.position_scale = 0.0
        else:
            state.position_scale = 1.0

        return state.level

    def _transition(self, new_level: DegradationLevel, date_str: str, reason: str) -> None:
        old = self._state.level
        self._state.level = new_level
        self._state.days_in_state = 0
        entry = {"from": old.name, "to": new_level.name, "date": date_str, "reason": reason}
        self._state.transition_log.append(entry)
        logger.info(f"Degradation: {old.name} → {new_level.name} ({reason})")

    def reset(self) -> None:
        self._state = DegradationState()
