"""A股交易日历

提供交易日判断、交易时段识别等功能。
V1 使用内置交易日列表（2000-2030），支持离线使用。
"""

import json
import logging
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import List, Optional
from aipha.utils.paths import PROJECT_ROOT, project_path

logger = logging.getLogger(__name__)


class TradingSession(Enum):
    """交易时段枚举"""

    MORNING_CALL_AUCTION = "morning_call_auction"  # 早盘集合竞价 9:15-9:25
    MORNING_CONTINUOUS = "morning_continuous"  # 早盘连续竞价 9:30-11:30
    LUNCH_BREAK = "lunch_break"  # 午休 11:30-13:00
    AFTERNOON_CONTINUOUS = "afternoon_continuous"  # 午盘连续竞价 13:00-14:57
    CLOSING_CALL_AUCTION = "closing_call_auction"  # 收盘集合竞价 14:57-15:00
    CLOSED = "closed"  # 收盘后/非交易时段

    def is_trading(self) -> bool:
        """判断当前时段是否可交易（连续竞价）"""
        return self in (
            TradingSession.MORNING_CONTINUOUS,
            TradingSession.AFTERNOON_CONTINUOUS,
        )


class CalendarLoadError(Exception):
    """日历加载错误"""

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
    2. 内置交易日数据（硬编码关键年份）
    3. 降级规则（非周末非明显假期即交易日）
    """

    # 交易时段定义（北京时间）
    MORNING_CALL_START = time(9, 15)  # 9:15
    MORNING_CALL_END = time(9, 25)  # 9:25
    MORNING_CONT_START = time(9, 30)  # 9:30
    MORNING_CONT_END = time(11, 30)  # 11:30
    AFTERNOON_CONT_START = time(13, 0)  # 13:00
    AFTERNOON_CONT_END = time(14, 57)  # 14:57
    CLOSING_CALL_START = time(14, 57)  # 14:57
    CLOSING_CALL_END = time(15, 0)  # 15:00

    def __init__(self, calendar_file: Optional[Path] = None):
        """
        Args:
            calendar_file: 日历文件路径（可选，默认自动搜寻 data/calendars/cn_stock_v1.json）
        """
        self.calendar_file = calendar_file
        self._trading_days_cache: Optional[set[date]] = None
        self._is_fallback = False
        self._load_calendar()

    @property
    def is_fallback(self) -> bool:
        """当前用的是降级日历（近似）还是精确日历文件。

        降级日历只排除公历固定假期，会把农历假期当成交易日
        （见 ``_is_obvious_holiday``）。调用方若对交易日数量敏感
        （年化、T+N、缺数据告警），应据此决定是否降低置信度。
        """
        return self._is_fallback

    def _load_calendar(self) -> None:
        """加载交易日历数据（优先本地文件，降级为内置数据）"""
        # 尝试指定路径
        paths_to_try: list[Path] = []
        if self.calendar_file:
            paths_to_try.append(self.calendar_file)

        # 自动搜索常见位置
        paths_to_try.extend([
            project_path("data", "calendars", "cn_stock_v1.json"),
            PROJECT_ROOT / "data" / "calendars" / "cn_stock_v1.json",
        ])

        for path in paths_to_try:
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self._trading_days_cache = {
                            date.fromisoformat(d) for d in data["trading_days"]
                        }
                        self._is_fallback = False
                    logger.info(
                        f"已加载交易日历: {len(self._trading_days_cache)} 个交易日 "
                        f"(source={data.get('source', 'unknown')}, "
                        f"range={data.get('range', {}).get('start', '?')}~"
                        f"{data.get('range', {}).get('end', '?')})"
                    )
                    # P1-4: 日历过期检查
                    range_end = data.get("range", {}).get("end")
                    if range_end:
                        end_date = date.fromisoformat(range_end)
                        today = date.today()
                        days_remaining = (end_date - today).days
                        if days_remaining < 0:
                            logger.critical(
                                "交易日历已过期! end_date=%s, today=%s. "
                                "请更新日历数据.",
                                range_end, today,
                            )
                        elif days_remaining < 30:
                            logger.warning(
                                "交易日历即将过期: end_date=%s, "
                                "剩余 %d 天覆盖. 请尽快更新.",
                                range_end, days_remaining,
                            )
                    return
                except Exception as e:
                    logger.warning(f"加载日历文件 {path} 失败: {e}")
                    continue

        # 使用内置简化数据（生成2000-2030年的工作日，排除明显假期）
        self._trading_days_cache = self._generate_fallback_calendar()
        self._is_fallback = True
        logger.warning(
            f"使用降级日历（仅工作日近似）: {len(self._trading_days_cache)} 个交易日。"
            f"建议运行 `python scripts/data/generate_calendar.py` 生成精确日历。"
        )

    def _generate_fallback_calendar(self) -> set[date]:
        """生成降级日历（简化版：周一到周五，排除明显假期）"""
        trading_days = set()
        start = date(2000, 1, 1)
        end = date(2030, 12, 31)

        current = start
        while current <= end:
            # 基本规则：周一到周五
            if current.weekday() < 5:  # 0=周一, 4=周五
                # 排除明显假期（简化版）
                if not self._is_obvious_holiday(current):
                    trading_days.add(current)
            current += timedelta(days=1)

        return trading_days

    def _is_obvious_holiday(self, d: date) -> bool:
        """判断是否是明显假期（降级日历专用，仅公历固定日期节假日）

        设计原则（R13 提出，此处收紧）：
        误判"假日为交易日"只会多出一个没有行情的日期（下游取不到 bar，
        或交易所直接废单），而误判"交易日为假日"会让
        ``order_validator`` 拒掉合法委托、让 live/paper 引擎**静默跳过**
        一整个真实交易日。两者代价差一个数量级，所以宁可多算。

        据此**不再猜测农历节日**（春节/端午/中秋）。原实现用固定公历窗口
        近似它们，但农历节日每年漂移 30 天以上，固定窗口在多数年份是
        错位的 —— 以 2024 年为例，春节实际休市 2/9–2/16，而 1/28~2/10
        的窗口反而抹掉了 1/29–2/8 这 9 个真实交易日，同时没能盖住
        2/12–2/16 的真实休市。这不是"精度不够"，是方向性错误。

        结果：降级日历按 2024 年口径给出约 251 个交易日（真实 242），
        多出的那几天集中在农历假期，属于上面选定的安全方向。
        需要精确日历时请提供 ``data/calendars/cn_stock_v1.json``
        （见 ``is_fallback``）。
        """
        # 元旦：1月1日
        if d.month == 1 and d.day == 1:
            return True

        # 清明：4月4-6日
        if d.month == 4 and 4 <= d.day <= 6:
            return True

        # 劳动节：5月1-5日（近年多为5天假期）
        if d.month == 5 and 1 <= d.day <= 5:
            return True

        # 国庆：10月1-7日
        if d.month == 10 and 1 <= d.day <= 7:
            return True

        return False

    def is_trading_day(self, d: date) -> bool:
        """判断是否是交易日
        
        Args:
            d: 日期
            
        Returns:
            bool: 是否交易日
        """
        if self._trading_days_cache is None:
            raise CalendarLoadError("交易日历未加载")

        # 如果在缓存中，直接返回
        if d in self._trading_days_cache:
            return True

        # 如果超出范围（2000-2030），使用降级规则
        if d.year < 2000 or d.year > 2030:
            logger.warning(f"日期 {d} 超出日历范围，使用降级规则")
            # 降级规则：非周末即交易日
            return d.weekday() < 5

        return False

    def get_trading_days(self, start: date, end: date) -> List[date]:
        """获取指定区间内的所有交易日
        
        Args:
            start: 开始日期（包含）
            end: 结束日期（包含）
            
        Returns:
            交易日列表（升序）
        """
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
        """获取下一个交易日
        
        Args:
            d: 当前日期
            skip_days: 跳过几个交易日（默认1）
            
        Returns:
            下一个交易日
        """
        current = d + timedelta(days=1)
        found = 0

        # 最多查找30天（避免无限循环）
        for _ in range(30):
            if self.is_trading_day(current):
                found += 1
                if found == skip_days:
                    return current
            current += timedelta(days=1)

        raise CalendarLoadError(f"无法找到 {d} 之后的第 {skip_days} 个交易日")

    def get_prev_trading_day(self, d: date, skip_days: int = 1) -> date:
        """获取上一个交易日
        
        Args:
            d: 当前日期
            skip_days: 跳过几个交易日（默认1）
            
        Returns:
            上一个交易日
        """
        current = d - timedelta(days=1)
        found = 0

        # 最多查找30天
        for _ in range(30):
            if self.is_trading_day(current):
                found += 1
                if found == skip_days:
                    return current
            current -= timedelta(days=1)

        raise CalendarLoadError(f"无法找到 {d} 之前的第 {skip_days} 个交易日")

    def get_session(self, dt: datetime) -> TradingSession:
        """获取指定时刻的交易时段
        
        Args:
            dt: 日期时间
            
        Returns:
            交易时段枚举
        """
        # 首先检查是否是交易日
        if not self.is_trading_day(dt.date()):
            return TradingSession.CLOSED

        t = dt.time()

        # 早盘集合竞价 9:15-9:25
        if self.MORNING_CALL_START <= t < self.MORNING_CALL_END:
            return TradingSession.MORNING_CALL_AUCTION

        # 早盘连续竞价 9:30-11:30
        if self.MORNING_CONT_START <= t < self.MORNING_CONT_END:
            return TradingSession.MORNING_CONTINUOUS

        # 午休 11:30-13:00
        if self.MORNING_CONT_END <= t < self.AFTERNOON_CONT_START:
            return TradingSession.LUNCH_BREAK

        # 午盘连续竞价 13:00-14:57
        if self.AFTERNOON_CONT_START <= t < self.AFTERNOON_CONT_END:
            return TradingSession.AFTERNOON_CONTINUOUS

        # 收盘集合竞价 14:57-15:00
        if self.CLOSING_CALL_START <= t < self.CLOSING_CALL_END:
            return TradingSession.CLOSING_CALL_AUCTION

        # 其他时段：收盘
        return TradingSession.CLOSED

    def is_market_open(self, dt: datetime) -> bool:
        """判断指定时刻市场是否开放（连续竞价时段）
        
        Args:
            dt: 日期时间
            
        Returns:
            bool: 是否开放
        """
        session = self.get_session(dt)
        return session.is_trading()

    def count_trading_days(self, start: date, end: date) -> int:
        """统计指定区间内的交易日数量
        
        Args:
            start: 开始日期
            end: 结束日期
            
        Returns:
            交易日数量
        """
        return len(self.get_trading_days(start, end))

    def fingerprint(self) -> str:
        """生成日历指纹（用于可复现性）
        
        Returns:
            日历版本标识
        """
        if self._trading_days_cache:
            # 计算覆盖的年份范围
            years = {d.year for d in self._trading_days_cache}
            min_year = min(years)
            max_year = max(years)
            return f"cn_stock_v1_range_{min_year}_{max_year}_days_{len(self._trading_days_cache)}"
        return "cn_stock_v1_fallback"
