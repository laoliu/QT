"""
止盈规则模块

支持多种止盈策略：
- 固定百分比止盈
- 追踪止盈（保护浮盈）
- 分段止盈（阶梯出场）

与 stop_loss.py 对称设计，共享 TriggerAction。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Callable

from aipha.risk.stop_loss import TriggerAction

logger = logging.getLogger(__name__)


class TakeProfitType(str, Enum):
    FIXED_PERCENT = "FIXED_PERCENT"
    TRAILING = "TRAILING"
    STAGED = "STAGED"


@dataclass
class TakeProfitConfig:
    enabled: bool = True
    profit_type: TakeProfitType = TakeProfitType.FIXED_PERCENT
    threshold: float = 0.10  # 10% 止盈
    action: TriggerAction = TriggerAction.CLOSE_POSITION
    # 分段止盈参数
    stages: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class TakeProfitState:
    symbol: str
    entry_price: Decimal
    highest_price: Decimal
    stages_triggered: list[int] = field(default_factory=list)
    triggered: bool = False
    triggered_at: datetime | None = None


@dataclass
class TakeProfitResult:
    triggered: bool
    symbol: str
    profit_type: TakeProfitType
    action: TriggerAction
    current_price: Decimal
    target_price: Decimal
    profit_pct: float
    exit_ratio: float  # 出仓比例 0.0~1.0
    message: str


class TakeProfitRule(ABC):
    """止盈规则基类"""

    def __init__(self, config: TakeProfitConfig,
                 clock: Callable[[], datetime] | None = None):
        self.config = config
        self._states: dict[str, TakeProfitState] = {}
        self._clock = clock or datetime.now

    @abstractmethod
    def check(self, symbol: str, current_price: Decimal,
              position_qty: int) -> TakeProfitResult | None:
        ...

    def register_entry(self, symbol: str, entry_price: Decimal) -> None:
        self._states[symbol] = TakeProfitState(
            symbol=symbol,
            entry_price=entry_price,
            highest_price=entry_price,
        )

    def update_price(self, symbol: str, current_price: Decimal) -> None:
        if symbol in self._states:
            s = self._states[symbol]
            if current_price > s.highest_price:
                s.highest_price = current_price

    def clear_position(self, symbol: str) -> None:
        self._states.pop(symbol, None)

    def get_state(self, symbol: str) -> TakeProfitState | None:
        return self._states.get(symbol)


class FixedPercentTakeProfit(TakeProfitRule):
    """固定百分比止盈 — 浮盈达到阈值时全部出场。"""

    def check(self, symbol: str, current_price: Decimal,
              position_qty: int) -> TakeProfitResult | None:
        if not self.config.enabled or position_qty == 0:
            return None
        state = self._states.get(symbol)
        if state is None:
            return None
        profit_pct = float(
            (current_price - state.entry_price) / state.entry_price)
        target = state.entry_price * Decimal(1 + self.config.threshold)
        if profit_pct >= self.config.threshold:
            state.triggered = True
            state.triggered_at = self._clock()
            return TakeProfitResult(
                triggered=True, symbol=symbol,
                profit_type=TakeProfitType.FIXED_PERCENT,
                action=self.config.action,
                current_price=current_price, target_price=target,
                profit_pct=profit_pct, exit_ratio=1.0,
                message=f"Fixed take-profit: {profit_pct:.2%} >= {self.config.threshold:.2%}",
            )
        return None


class TrailingTakeProfit(TakeProfitRule):
    """追踪止盈 — 从最高价回撤超过阈值时出场，锁定浮盈。"""

    def check(self, symbol: str, current_price: Decimal,
              position_qty: int) -> TakeProfitResult | None:
        if not self.config.enabled or position_qty == 0:
            return None
        state = self._states.get(symbol)
        if state is None:
            return None
        self.update_price(symbol, current_price)
        profit_from_entry = float(
            (current_price - state.entry_price) / state.entry_price)
        # 只有盈利超过 threshold/2 后才激活追踪
        activate_threshold = self.config.threshold / 2
        if profit_from_entry < activate_threshold:
            return None
        drop_from_peak = float(
            (state.highest_price - current_price) / state.highest_price)
        target = state.highest_price * Decimal(1 - self.config.threshold)
        if drop_from_peak >= self.config.threshold:
            state.triggered = True
            state.triggered_at = self._clock()
            return TakeProfitResult(
                triggered=True, symbol=symbol,
                profit_type=TakeProfitType.TRAILING,
                action=self.config.action,
                current_price=current_price, target_price=target,
                profit_pct=profit_from_entry, exit_ratio=1.0,
                message=f"Trailing take-profit: drop {drop_from_peak:.2%} from peak",
            )
        return None


class StagedTakeProfit(TakeProfitRule):
    """分段止盈 — 到达不同盈利水平时分批出场。

    config.stages 格式: [(profit_pct, exit_ratio), ...]
    例: [(0.05, 0.3), (0.10, 0.3), (0.20, 0.4)]
        盈利 5% 卖出 30%，10% 再卖 30%，20% 清仓剩余 40%
    """

    def check(self, symbol: str, current_price: Decimal,
              position_qty: int) -> TakeProfitResult | None:
        if not self.config.enabled or position_qty == 0:
            return None
        state = self._states.get(symbol)
        if state is None:
            return None
        profit_pct = float(
            (current_price - state.entry_price) / state.entry_price)

        stages = self.config.stages or [
            (self.config.threshold, 1.0)]  # 无分段则全出

        for i, (level, ratio) in enumerate(stages):
            if i in state.stages_triggered:
                continue
            if profit_pct >= level:
                state.stages_triggered.append(i)
                target = state.entry_price * Decimal(1 + level)
                is_final = (i == len(stages) - 1)
                if is_final:
                    state.triggered = True
                    state.triggered_at = self._clock()
                return TakeProfitResult(
                    triggered=True, symbol=symbol,
                    profit_type=TakeProfitType.STAGED,
                    action=self.config.action,
                    current_price=current_price, target_price=target,
                    profit_pct=profit_pct, exit_ratio=ratio,
                    message=f"Stage {i+1}/{len(stages)}: profit {profit_pct:.2%} >= {level:.2%}, exit {ratio:.0%}",
                )
        return None
