"""策略调度器 — 定时调仓 / 事件驱动 / 多频率复合

支持:
  - 固定频率 (每N天)
  - 日历事件 (月初/月末/季末)
  - 自定义触发条件
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
from typing import Callable


class ScheduleFrequency(str, Enum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    BIWEEKLY = "BIWEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    CUSTOM = "CUSTOM"


@dataclass
class ScheduleRule:
    strategy_name: str
    frequency: ScheduleFrequency = ScheduleFrequency.DAILY
    custom_days: int | None = None
    # 月初/月末
    month_day: int | None = None  # 1=月初第1天, -1=月末
    # 自定义触发函数
    trigger_fn: Callable[[datetime], bool] | None = None


class StrategyScheduler:
    """策略调度器 — 决定何时触发调仓。"""

    def __init__(self) -> None:
        self._rules: dict[str, ScheduleRule] = {}
        self._last_trigger: dict[str, datetime] = {}
        self._bar_counts: dict[str, int] = {}

    def add_rule(self, rule: ScheduleRule) -> None:
        self._rules[rule.strategy_name] = rule
        self._bar_counts[rule.strategy_name] = 0

    def should_trigger(self, strategy_name: str, current_time: datetime) -> bool:
        """判断是否应触发该策略的调仓。"""
        rule = self._rules.get(strategy_name)
        if rule is None:
            return True  # 无规则 = 每根 bar 都触发

        self._bar_counts[strategy_name] = self._bar_counts.get(strategy_name, 0) + 1

        if rule.trigger_fn is not None:
            return rule.trigger_fn(current_time)

        freq = rule.frequency
        if freq == ScheduleFrequency.DAILY:
            return True

        if freq == ScheduleFrequency.CUSTOM and rule.custom_days:
            return self._bar_counts[strategy_name] % rule.custom_days == 0

        if freq == ScheduleFrequency.WEEKLY:
            return current_time.weekday() == 0  # 周一

        if freq == ScheduleFrequency.BIWEEKLY:
            last = self._last_trigger.get(strategy_name)
            if last is None or (current_time - last).days >= 14:
                self._last_trigger[strategy_name] = current_time
                return True
            return False

        if freq == ScheduleFrequency.MONTHLY:
            day = rule.month_day or 1
            if day > 0:
                return current_time.day == day
            else:
                # 月末
                import calendar
                _, last_day = calendar.monthrange(current_time.year, current_time.month)
                return current_time.day == last_day

        if freq == ScheduleFrequency.QUARTERLY:
            return current_time.month in (1, 4, 7, 10) and current_time.day == 1

        return True

    def get_active_strategies(self, current_time: datetime) -> list[str]:
        """返回当前时间应触发的策略列表。"""
        return [name for name in self._rules if self.should_trigger(name, current_time)]
