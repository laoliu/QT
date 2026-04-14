"""A股交易日历

提供交易日判断、交易时段识别等功能。
V1 使用内置交易日列表（2000-2030），支持离线使用。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class TradingSession(Enum):
    """交易时段枚举"""

    MORNING_CALL_AUCTION = "morning_call_auction"
    MORNING_CONTINUOUS = "morning_continuous"
    LUNCH_BREAK = "lunch_break"
    AFTERNOON_CONTINUOUS = "afternoon_continuous"
    CLOSING_CALL_AUCTION = "closing_call_auction"
    CLOSED = "closed"

    def is_trading(self) -> bool:
        return self in (
            TradingSession.MORNING_CONTINUOUS,
            TradingSession.AFTERNOON_CONTINUOUS,
        )


class CalendarLoadError(Exception):
    pass


class CNStockCalendar:
    """中国A股交易日历

    功能:
    - 判断是否交易日
    - 获取交易日列表
    - 判断交易时段
    - 支持离线使用（内置2000-2030年）

    数据来源优先级:
    1. 本地缓存文件（data/calendars/cn_stock_v1.json）
    2. 降级规则（非周末非明显假期即交易日）
    """

    MORNING_CALL_START = time(9, 15)
    MORNING_CALL_END = time(9, 25)
    MORNING_CONT_START = time(9, 30)
    MORNING_CONT_END = time(11, 30)
    AFTERNOON_CONT_START = time(13, 0)
    AFTERNOON_CONT_END = time(14, 57)
    CLOSING_CALL_START = time(14, 57)
    CLOSING_CALL_END = time(15, 0)

    def __init__(self, calendar_file: Optional[Path] = None):
        self.calendar_file = calendar_file
        self._trading_days_cache: Optional[set[date]] = None
        self._load_calendar()

    def _load_calendar(self) -> None:
        paths_to_try: list[Path] = []
        if self.calendar_file:
            paths_to_try.append(self.calendar_file)

        paths_to_try.extend([
            Path("data/calendars/cn_stock_v1.json"),
            Path(__file__).parent.parent.parent / "data" / "calendars" / "cn_stock_v1.json",
        ])

        for path in paths_to_try:
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self._trading_days_cache = {
                            date.fromisoformat(d) for d in data["trading_days"]
                        }
                    logger.info(
                        "已加载交易日历: %d 个交易日 (source=%s)",
                        len(self._trading_days_cache),
                        data.get("source", "unknown"),
                    )
                    return
                except Exception as e:
                    logger.warning("加载日历文件 %s 失败: %s", path, e)
                    continue

        self._trading_days_cache = self._generate_fallback_calendar()
        logger.warning(
            "使用降级日历（仅工作日近似）: %d 个交易日。",
            len(self._trading_days_cache),
        )

    def _generate_fallback_calendar(self) -> set[date]:
        trading_days = set()
        start = date(2000, 1, 1)
        end = date(2030, 12, 31)
        current = start
        while current <= end:
            if current.weekday() < 5:
                if not self._is_obvious_holiday(current):
                    trading_days.add(current)
            current += timedelta(days=1)
        return trading_days

    def _is_obvious_holiday(self, d: date) -> bool:
        if d.month == 1 and d.day == 1:
            return True
        if (d.month == 1 and d.day >= 28) or (d.month == 2 and d.day <= 10):
            return True
        if d.month == 4 and 4 <= d.day <= 6:
            return True
        if d.month == 5 and 1 <= d.day <= 5:
            return True
        if d.month == 6 and 8 <= d.day <= 14:
            return True
        if d.month == 9 and 13 <= d.day <= 19:
            return True
        if d.month == 10 and 1 <= d.day <= 7:
            return True
        return False

    def is_trading_day(self, d: date) -> bool:
        if self._trading_days_cache is None:
            raise CalendarLoadError("交易日历未加载")
        if d in self._trading_days_cache:
            return True
        if d.year < 2000 or d.year > 2030:
            return d.weekday() < 5
        return False

    def get_trading_days(self, start: date, end: date) -> List[date]:
        if self._trading_days_cache is None:
            raise CalendarLoadError("交易日历未加载")
        trading_days = []
        current = start
        while current <= end:
            if self.is_trading_day(current):
                trading_days.append(current)
            current += timedelta(days=1)
        return sorted(trading_days)

    def get_next_trading_day(self, d: date, skip_days: int = 1) -> date:
        current = d + timedelta(days=1)
        found = 0
        for _ in range(30):
            if self.is_trading_day(current):
                found += 1
                if found == skip_days:
                    return current
            current += timedelta(days=1)
        raise CalendarLoadError(f"无法找到 {d} 之后的第 {skip_days} 个交易日")

    def get_prev_trading_day(self, d: date, skip_days: int = 1) -> date:
        current = d - timedelta(days=1)
        found = 0
        for _ in range(30):
            if self.is_trading_day(current):
                found += 1
                if found == skip_days:
                    return current
            current -= timedelta(days=1)
        raise CalendarLoadError(f"无法找到 {d} 之前的第 {skip_days} 个交易日")

    def get_session(self, dt: datetime) -> TradingSession:
        if not self.is_trading_day(dt.date()):
            return TradingSession.CLOSED
        t = dt.time()
        if self.MORNING_CALL_START <= t < self.MORNING_CALL_END:
            return TradingSession.MORNING_CALL_AUCTION
        if self.MORNING_CONT_START <= t < self.MORNING_CONT_END:
            return TradingSession.MORNING_CONTINUOUS
        if self.MORNING_CONT_END <= t < self.AFTERNOON_CONT_START:
            return TradingSession.LUNCH_BREAK
        if self.AFTERNOON_CONT_START <= t < self.CLOSING_CALL_START:
            return TradingSession.AFTERNOON_CONTINUOUS
        if self.CLOSING_CALL_START <= t <= self.CLOSING_CALL_END:
            return TradingSession.CLOSING_CALL_AUCTION
        return TradingSession.CLOSED
