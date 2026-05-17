"""
止损与熔断规则模块

支持多种止损策略：
- 固定百分比止损
- 追踪止损
- 日内亏损限制
- 组合熔断

设计原则：
- 规则可组合
- 状态可持久化
- 与审计日志集成
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Callable


logger = logging.getLogger(__name__)


class BacktestClock:
    """Mutable clock for backtest mode.

    In production / paper trading, CircuitBreaker defaults to ``datetime.now()``.
    In backtest mode, the engine injects a *BacktestClock* whose time is
    advanced to the current bar's simulated timestamp on every bar, so that
    the 30-minute cooldown is measured in *simulated* time, not wall-clock
    time.  Without this, the cooldown never expires during a fast backtest,
    causing the breaker to stay permanently halted after the first trigger.
    """

    def __init__(self) -> None:
        self._time: datetime = datetime.min

    def set_time(self, t: datetime) -> None:
        self._time = t

    def __call__(self) -> datetime:
        return self._time


class StopLossType(str, Enum):
    """止损类型"""

    FIXED_PERCENT = "FIXED_PERCENT"
    TRAILING = "TRAILING"
    DAILY_LOSS = "DAILY_LOSS"
    DRAWDOWN = "DRAWDOWN"


class TriggerAction(str, Enum):
    """触发动作"""

    CLOSE_POSITION = "CLOSE_POSITION"  # 平仓
    REDUCE_POSITION = "REDUCE_POSITION"  # 减仓
    ALERT_ONLY = "ALERT_ONLY"  # 仅告警
    HALT_TRADING = "HALT_TRADING"  # 暂停交易


@dataclass
class StopLossConfig:
    """止损配置"""

    enabled: bool = True
    stop_type: StopLossType = StopLossType.FIXED_PERCENT
    threshold: float = 0.05  # 5% 止损
    action: TriggerAction = TriggerAction.CLOSE_POSITION


@dataclass
class StopLossState:
    """止损状态"""

    symbol: str
    entry_price: Decimal
    highest_price: Decimal  # 用于追踪止损
    lowest_price: Decimal
    stop_price: Decimal | None = None
    triggered: bool = False
    triggered_at: datetime | None = None
    trigger_price: Decimal | None = None


@dataclass
class StopLossResult:
    """止损检查结果"""

    triggered: bool
    symbol: str
    stop_type: StopLossType
    action: TriggerAction
    current_price: Decimal
    stop_price: Decimal
    loss_pct: float
    message: str


class StopLossRule(ABC):
    """止损规则基类

    Args:
        config: 止损配置
        clock: 可注入的时钟函数，返回当前时间。默认 datetime.now()。
               回测模式下应注入返回 bar 时间的函数，避免使用系统真实时间。
    """

    def __init__(self, config: StopLossConfig, clock: Callable[[], datetime] | None = None):
        self.config = config
        self._states: dict[str, StopLossState] = {}
        self._clock = clock or datetime.now

    @abstractmethod
    def check(
        self,
        symbol: str,
        current_price: Decimal,
        position_qty: int,
    ) -> StopLossResult | None:
        """检查是否触发止损"""
        ...

    def register_entry(
        self,
        symbol: str,
        entry_price: Decimal,
    ) -> None:
        """注册入场价格"""
        self._states[symbol] = StopLossState(
            symbol=symbol,
            entry_price=entry_price,
            highest_price=entry_price,
            lowest_price=entry_price,
        )

    def update_price(self, symbol: str, current_price: Decimal) -> None:
        """更新价格（用于追踪止损）"""
        if symbol in self._states:
            state = self._states[symbol]
            if current_price > state.highest_price:
                state.highest_price = current_price
            if current_price < state.lowest_price:
                state.lowest_price = current_price

    def clear_position(self, symbol: str) -> None:
        """清除持仓状态"""
        self._states.pop(symbol, None)

    def get_state(self, symbol: str) -> StopLossState | None:
        """获取止损状态"""
        return self._states.get(symbol)


class FixedPercentStopLoss(StopLossRule):
    """固定百分比止损

    当价格从入场价下跌超过阈值时触发。
    """

    def check(
        self,
        symbol: str,
        current_price: Decimal,
        position_qty: int,
    ) -> StopLossResult | None:
        """检查固定百分比止损是否触发。"""
        if not self.config.enabled or position_qty == 0:
            return None

        state = self._states.get(symbol)
        if state is None:
            return None

        # 计算亏损比例
        if position_qty > 0:  # 多头
            loss_pct = float((state.entry_price - current_price) / state.entry_price)
            stop_price = state.entry_price * Decimal(1 - self.config.threshold)
        else:  # 空头
            loss_pct = float((current_price - state.entry_price) / state.entry_price)
            stop_price = state.entry_price * Decimal(1 + self.config.threshold)

        triggered = loss_pct >= self.config.threshold

        if triggered:
            state.triggered = True
            state.triggered_at = self._clock()
            state.trigger_price = current_price
            state.stop_price = stop_price

            return StopLossResult(
                triggered=True,
                symbol=symbol,
                stop_type=StopLossType.FIXED_PERCENT,
                action=self.config.action,
                current_price=current_price,
                stop_price=stop_price,
                loss_pct=loss_pct,
                message=f"Fixed stop loss triggered: {loss_pct:.2%} >= {self.config.threshold:.2%}",
            )

        return None


class TrailingStopLoss(StopLossRule):
    """追踪止损

    止损价跟随最高价移动，保护浮盈。
    """

    def check(
        self,
        symbol: str,
        current_price: Decimal,
        position_qty: int,
    ) -> StopLossResult | None:
        """检查追踪止损是否触发。"""
        if not self.config.enabled or position_qty == 0:
            return None

        state = self._states.get(symbol)
        if state is None:
            return None

        # 更新最高价
        self.update_price(symbol, current_price)

        # 计算追踪止损价
        if position_qty > 0:  # 多头
            stop_price = state.highest_price * Decimal(1 - self.config.threshold)
            loss_from_peak = float(
                (state.highest_price - current_price) / state.highest_price
            )
            triggered = current_price <= stop_price
        else:  # 空头
            stop_price = state.lowest_price * Decimal(1 + self.config.threshold)
            loss_from_peak = float(
                (current_price - state.lowest_price) / state.lowest_price
            )
            triggered = current_price >= stop_price

        state.stop_price = stop_price

        if triggered:
            state.triggered = True
            state.triggered_at = self._clock()
            state.trigger_price = current_price

            return StopLossResult(
                triggered=True,
                symbol=symbol,
                stop_type=StopLossType.TRAILING,
                action=self.config.action,
                current_price=current_price,
                stop_price=stop_price,
                loss_pct=loss_from_peak,
                message=f"Trailing stop triggered: {loss_from_peak:.2%} from peak",
            )

        return None


@dataclass
class DailyPnL:
    """日内盈亏"""

    date: datetime
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    peak_pnl: Decimal = Decimal("0")

    @property
    def total_pnl(self) -> Decimal:
        """返回日内总盈亏（已实现 + 未实现）。"""
        return self.realized_pnl + self.unrealized_pnl


class DailyLossLimit(StopLossRule):
    """日内亏损限制

    当日亏损超过阈值时暂停交易。
    """

    def __init__(self, config: StopLossConfig, initial_capital: Decimal,
                 clock: Callable[[], datetime] | None = None):
        super().__init__(config, clock=clock)
        self.initial_capital = initial_capital
        self._daily_pnl: DailyPnL | None = None

    def reset_daily(self) -> None:
        """重置日内盈亏"""
        self._daily_pnl = DailyPnL(date=self._clock())

    def update_pnl(self, realized: Decimal, unrealized: Decimal) -> None:
        """更新盈亏"""
        if self._daily_pnl is None:
            self.reset_daily()

        self._daily_pnl.realized_pnl = realized
        self._daily_pnl.unrealized_pnl = unrealized

        # 更新峰值
        if self._daily_pnl.total_pnl > self._daily_pnl.peak_pnl:
            self._daily_pnl.peak_pnl = self._daily_pnl.total_pnl

    def check(
        self,
        symbol: str,
        current_price: Decimal,
        position_qty: int,
    ) -> StopLossResult | None:
        """检查日内亏损是否超过限制。"""
        if not self.config.enabled:
            return None

        if self._daily_pnl is None:
            return None

        # 计算日内亏损比例
        loss_pct = float(-self._daily_pnl.total_pnl / self.initial_capital)

        if loss_pct >= self.config.threshold:
            return StopLossResult(
                triggered=True,
                symbol="PORTFOLIO",
                stop_type=StopLossType.DAILY_LOSS,
                action=TriggerAction.HALT_TRADING,
                current_price=Decimal("0"),
                stop_price=Decimal("0"),
                loss_pct=loss_pct,
                message=f"Daily loss limit triggered: {loss_pct:.2%} >= {self.config.threshold:.2%}",
            )

        return None


@dataclass
class CircuitBreakerConfig:
    """熔断配置"""

    enabled: bool = True
    max_drawdown: float = 0.10  # 最大回撤 10%
    max_daily_loss: float = 0.05  # 日内最大亏损 5%
    max_consecutive_losses: int = 5  # 最大连续亏损次数
    cooldown_minutes: int = 30  # 冷却时间


@dataclass
class CircuitBreakerState:
    """熔断状态"""

    is_active: bool = False
    triggered_at: datetime | None = None
    trigger_reason: str = ""
    resume_at: datetime | None = None
    consecutive_losses: int = 0
    peak_equity: Decimal = Decimal("0")
    current_drawdown: float = 0.0


class CircuitBreaker:
    """熔断器

    当满足以下条件之一时触发熔断：
    1. 回撤超过阈值
    2. 日内亏损超过阈值
    3. 连续亏损次数超过阈值

    熔断期间暂停所有交易。

    Args:
        config: 熔断配置
        clock: 可注入的时钟函数，返回当前时间。默认 datetime.now()。
               回测模式下应注入返回 bar 时间的函数。
    """

    def __init__(self, config: CircuitBreakerConfig, clock: Callable[[], datetime] | None = None):
        self.config = config
        self._state = CircuitBreakerState()
        self._clock = clock or datetime.now

    @property
    def is_active(self) -> bool:
        """熔断是否生效"""
        if not self._state.is_active:
            return False

        # 检查是否已过冷却期
        if self._state.resume_at and self._clock() >= self._state.resume_at:
            self._state.is_active = False
            logger.info("Circuit breaker cooldown ended")
            return False

        return True

    @property
    def state(self) -> CircuitBreakerState:
        """返回当前熔断器状态。"""
        return self._state

    def update_equity(self, current_equity: Decimal) -> None:
        """更新权益并检查回撤"""
        if current_equity > self._state.peak_equity:
            self._state.peak_equity = current_equity

        if self._state.peak_equity > 0:
            drawdown = float(
                (self._state.peak_equity - current_equity) / self._state.peak_equity
            )
            self._state.current_drawdown = drawdown

    def record_trade_result(self, is_win: bool) -> None:
        """记录交易结果"""
        if is_win:
            self._state.consecutive_losses = 0
        else:
            self._state.consecutive_losses += 1

    def check(
        self,
        current_equity: Decimal,
        daily_loss_pct: float = 0.0,
    ) -> bool:
        """检查是否触发熔断

        Returns:
            是否触发熔断
        """
        if not self.config.enabled:
            return False

        # 已经在熔断状态
        if self.is_active:
            return True

        # 检查回撤
        self.update_equity(current_equity)
        if self._state.current_drawdown >= self.config.max_drawdown:
            self._trigger(f"Max drawdown: {self._state.current_drawdown:.2%}")
            return True

        # 检查日内亏损
        if daily_loss_pct >= self.config.max_daily_loss:
            self._trigger(f"Daily loss: {daily_loss_pct:.2%}")
            return True

        # 检查连续亏损
        if self._state.consecutive_losses >= self.config.max_consecutive_losses:
            self._trigger(f"Consecutive losses: {self._state.consecutive_losses}")
            return True

        return False

    def _trigger(self, reason: str) -> None:
        """触发熔断"""
        now = self._clock()
        self._state.is_active = True
        self._state.triggered_at = now
        self._state.trigger_reason = reason
        self._state.resume_at = now + timedelta(minutes=self.config.cooldown_minutes)

        logger.warning(
            f"Circuit breaker triggered: {reason}. "
            f"Resume at {self._state.resume_at}"
        )

    def kill_switch(self, reason: str) -> None:
        """永久 Kill Switch（不可自动恢复，需人工 force_resume 解除）

        与普通 _trigger 的区别：
        - resume_at 设为 None，不会自动恢复
        - 必须通过 force_resume() 手动解除

        Args:
            reason: 触发原因
        """
        now = self._clock()
        self._state.is_active = True
        self._state.triggered_at = now
        self._state.trigger_reason = f"[KILL_SWITCH] {reason}"
        self._state.resume_at = None  # 永不自动恢复

        logger.critical(
            f"KILL SWITCH activated: {reason}. "
            f"Manual force_resume() required to resume trading."
        )

    def reset(self) -> None:
        """重置熔断状态"""
        self._state = CircuitBreakerState()

    def force_resume(self) -> None:
        """强制恢复交易"""
        self._state.is_active = False
        logger.info("Circuit breaker force resumed")


class StopLossManager:
    """止损管理器

    管理多个止损规则，统一检查。
    """

    def __init__(self):
        self._rules: list[StopLossRule] = []
        self._circuit_breaker: CircuitBreaker | None = None

    def add_rule(self, rule: StopLossRule) -> "StopLossManager":
        """添加止损规则"""
        self._rules.append(rule)
        return self

    def set_circuit_breaker(self, breaker: CircuitBreaker) -> "StopLossManager":
        """设置熔断器"""
        self._circuit_breaker = breaker
        return self

    def register_entry(self, symbol: str, entry_price: Decimal) -> None:
        """注册入场"""
        for rule in self._rules:
            rule.register_entry(symbol, entry_price)

    def update_price(self, symbol: str, current_price: Decimal) -> None:
        """更新价格"""
        for rule in self._rules:
            rule.update_price(symbol, current_price)

    def clear_position(self, symbol: str) -> None:
        """清除持仓"""
        for rule in self._rules:
            rule.clear_position(symbol)

    def check(
        self,
        symbol: str,
        current_price: Decimal,
        position_qty: int,
    ) -> list[StopLossResult]:
        """检查所有止损规则

        Returns:
            触发的止损结果列表
        """
        results = []

        for rule in self._rules:
            result = rule.check(symbol, current_price, position_qty)
            if result and result.triggered:
                results.append(result)

        return results

    def check_circuit_breaker(
        self,
        current_equity: Decimal,
        daily_loss_pct: float = 0.0,
    ) -> bool:
        """检查熔断"""
        if self._circuit_breaker:
            return self._circuit_breaker.check(current_equity, daily_loss_pct)
        return False

    @property
    def is_halted(self) -> bool:
        """是否暂停交易"""
        if self._circuit_breaker:
            return self._circuit_breaker.is_active
        return False
