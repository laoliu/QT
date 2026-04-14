"""止损模块 — 固定止损 / 追踪止损 / 熔断器"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class StopType(str, Enum):
    FIXED = "fixed"
    TRAILING = "trailing"


@dataclass
class StopLossConfig:
    stop_type: StopType = StopType.TRAILING
    threshold: float = 0.08       # 触发止损的跌幅
    trailing_high: bool = True    # 追踪最高点
    cooldown_days: int = 5        # 止损后冷却期


@dataclass
class StopLossState:
    symbol: str
    entry_price: float
    peak_price: float
    is_stopped: bool = False
    stop_date: str = ""
    stop_reason: str = ""


class StopLossManager:
    """止损管理器。

    用法::

        mgr = StopLossManager(StopLossConfig(threshold=0.08))
        mgr.open_position("600519", entry_price=1800.0)
        should_stop, reason = mgr.check("600519", current_price=1650.0)
    """

    def __init__(self, config: StopLossConfig | None = None) -> None:
        self._config = config or StopLossConfig()
        self._positions: dict[str, StopLossState] = {}

    def open_position(self, symbol: str, entry_price: float) -> None:
        self._positions[symbol] = StopLossState(
            symbol=symbol, entry_price=entry_price, peak_price=entry_price,
        )

    def close_position(self, symbol: str) -> None:
        self._positions.pop(symbol, None)

    def check(self, symbol: str, current_price: float, date_str: str = "") -> tuple[bool, str]:
        """检查是否触发止损。返回 (should_stop, reason)。"""
        state = self._positions.get(symbol)
        if state is None or state.is_stopped:
            return False, ""

        cfg = self._config

        # 更新峰值
        if cfg.trailing_high and current_price > state.peak_price:
            state.peak_price = current_price

        ref_price = state.peak_price if cfg.stop_type == StopType.TRAILING else state.entry_price
        if ref_price <= 0:
            return False, ""

        drawdown = (ref_price - current_price) / ref_price
        if drawdown >= cfg.threshold:
            state.is_stopped = True
            state.stop_date = date_str
            reason = (
                f"{cfg.stop_type.value} stop: "
                f"price {current_price:.2f} down {drawdown:.1%} "
                f"from {'peak' if cfg.stop_type == StopType.TRAILING else 'entry'} {ref_price:.2f}"
            )
            state.stop_reason = reason
            logger.info(f"Stop loss triggered: {symbol} — {reason}")
            return True, reason

        return False, ""

    def get_state(self, symbol: str) -> StopLossState | None:
        return self._positions.get(symbol)


# ── 熔断器 ──

@dataclass
class CircuitBreakerConfig:
    max_daily_loss_pct: float = 0.03       # 单日最大亏损 3%
    max_consecutive_losses: int = 5        # 连续亏损天数
    halt_duration_days: int = 1            # 熔断持续天数
    portfolio_drawdown_halt: float = 0.10  # 组合回撤 10% 熔断


class CircuitBreaker:
    """组合级熔断器。

    触发条件:
    1. 单日亏损超阈值
    2. 连续亏损天数超阈值
    3. 组合最大回撤超阈值
    """

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self._config = config or CircuitBreakerConfig()
        self._is_halted = False
        self._halt_remaining = 0
        self._consecutive_losses = 0
        self._peak_nav = 0.0

    @property
    def is_halted(self) -> bool:
        return self._is_halted

    def update(self, daily_return: float, nav: float) -> tuple[bool, str]:
        """每日更新，返回 (is_halted, reason)。"""
        cfg = self._config

        # 倒计时
        if self._halt_remaining > 0:
            self._halt_remaining -= 1
            if self._halt_remaining <= 0:
                self._is_halted = False
                logger.info("Circuit breaker lifted")

        if self._is_halted:
            return True, "circuit breaker active"

        # 检查单日亏损
        if daily_return < -cfg.max_daily_loss_pct:
            return self._halt(f"Daily loss {daily_return:.2%} exceeds -{cfg.max_daily_loss_pct:.2%}")

        # 连续亏损
        if daily_return < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
        if self._consecutive_losses >= cfg.max_consecutive_losses:
            return self._halt(f"Consecutive losses: {self._consecutive_losses} days")

        # 组合回撤
        if nav > self._peak_nav:
            self._peak_nav = nav
        if self._peak_nav > 0:
            dd = (self._peak_nav - nav) / self._peak_nav
            if dd >= cfg.portfolio_drawdown_halt:
                return self._halt(f"Portfolio drawdown {dd:.2%} >= {cfg.portfolio_drawdown_halt:.2%}")

        return False, ""

    def _halt(self, reason: str) -> tuple[bool, str]:
        self._is_halted = True
        self._halt_remaining = self._config.halt_duration_days
        logger.warning(f"Circuit breaker TRIGGERED: {reason}")
        return True, reason

    def reset(self) -> None:
        self._is_halted = False
        self._halt_remaining = 0
        self._consecutive_losses = 0
        self._peak_nav = 0.0
