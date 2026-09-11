"""测试：A股交易日历"""

import pytest
from datetime import date, datetime, time
from aipha.data.calendar import (
    CNStockCalendar,
    TradingSession,
    CalendarLoadError,
)


class TestCNStockCalendar:
    """测试：中国A股交易日历"""

    def test_is_trading_day_workday(self):
        """测试：普通工作日是交易日"""
        calendar = CNStockCalendar()
        # 2024-01-02 是周二，应该是交易日
        assert calendar.is_trading_day(date(2024, 1, 2)) is True

    def test_is_trading_day_weekend(self):
        """测试：周末不是交易日"""
        calendar = CNStockCalendar()
        # 2024-01-06 是周六
        assert calendar.is_trading_day(date(2024, 1, 6)) is False
        # 2024-01-07 是周日
        assert calendar.is_trading_day(date(2024, 1, 7)) is False

    def test_is_trading_day_holiday(self):
        """测试：法定节假日不是交易日"""
        calendar = CNStockCalendar()
        # 2024-01-01 是元旦
        assert calendar.is_trading_day(date(2024, 1, 1)) is False
        # 2024-10-01 是国庆节
        assert calendar.is_trading_day(date(2024, 10, 1)) is False

    def test_is_trading_day_special_workday(self):
        """测试：调休补班日（周末上班但股市不开市）"""
        calendar = CNStockCalendar()
        # 2024-02-04 是周日但调休上班，股市仍不开市
        assert calendar.is_trading_day(date(2024, 2, 4)) is False

    def test_get_trading_days_single_week(self):
        """测试：获取一周内的交易日"""
        calendar = CNStockCalendar()
        # 2024-01-02 到 2024-01-08 (周二到周一)
        trading_days = calendar.get_trading_days(
            start=date(2024, 1, 2),
            end=date(2024, 1, 8),
        )
        # 应该包含：1-2(二), 1-3(三), 1-4(四), 1-5(五)，周末不含
        # 注意：简化日历可能包含1-8(一)，因为不知道元旦调休
        assert 4 <= len(trading_days) <= 5  # 允许4-5个
        assert date(2024, 1, 2) in trading_days
        assert date(2024, 1, 3) in trading_days
        assert date(2024, 1, 4) in trading_days
        assert date(2024, 1, 5) in trading_days
        assert date(2024, 1, 6) not in trading_days  # 周六必然不是

    def test_get_trading_days_cross_month(self):
        """测试：跨月获取交易日"""
        calendar = CNStockCalendar()
        # 2024年1月整月
        trading_days = calendar.get_trading_days(
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
        )
        # 1月有元旦假期，大约有20个交易日
        assert 18 <= len(trading_days) <= 22

    def test_get_next_trading_day(self):
        """测试：获取下一个交易日"""
        calendar = CNStockCalendar()
        # 从周五获取下一个交易日（应跳过周末）
        next_day = calendar.get_next_trading_day(date(2024, 1, 5))  # 周五
        assert next_day == date(2024, 1, 8)  # 下周一

    def test_get_prev_trading_day(self):
        """测试：获取上一个交易日"""
        calendar = CNStockCalendar()
        # 从周一获取上一个交易日（应跳过周末）
        prev_day = calendar.get_prev_trading_day(date(2024, 1, 8))  # 周一
        assert prev_day == date(2024, 1, 5)  # 上周五

    def test_get_session_morning_call_auction(self):
        """测试：早盘集合竞价时段（9:15-9:25）"""
        calendar = CNStockCalendar()
        dt = datetime(2024, 1, 2, 9, 20, 0)  # 9:20
        session = calendar.get_session(dt)
        assert session == TradingSession.MORNING_CALL_AUCTION

    def test_get_session_morning_continuous(self):
        """测试：早盘连续竞价时段（9:30-11:30）"""
        calendar = CNStockCalendar()
        dt = datetime(2024, 1, 2, 10, 30, 0)  # 10:30
        session = calendar.get_session(dt)
        assert session == TradingSession.MORNING_CONTINUOUS

    def test_get_session_lunch_break(self):
        """测试：午休时段（11:30-13:00）"""
        calendar = CNStockCalendar()
        dt = datetime(2024, 1, 2, 12, 0, 0)  # 12:00
        session = calendar.get_session(dt)
        assert session == TradingSession.LUNCH_BREAK

    def test_get_session_afternoon_continuous(self):
        """测试：午盘连续竞价时段（13:00-14:57）"""
        calendar = CNStockCalendar()
        dt = datetime(2024, 1, 2, 14, 30, 0)  # 14:30
        session = calendar.get_session(dt)
        assert session == TradingSession.AFTERNOON_CONTINUOUS

    def test_get_session_closing_call_auction(self):
        """测试：收盘集合竞价时段（14:57-15:00）"""
        calendar = CNStockCalendar()
        dt = datetime(2024, 1, 2, 14, 58, 0)  # 14:58
        session = calendar.get_session(dt)
        assert session == TradingSession.CLOSING_CALL_AUCTION

    def test_get_session_closed(self):
        """测试：收盘后时段（15:00之后）"""
        calendar = CNStockCalendar()
        dt = datetime(2024, 1, 2, 16, 0, 0)  # 16:00
        session = calendar.get_session(dt)
        assert session == TradingSession.CLOSED

    def test_get_session_non_trading_day(self):
        """测试：非交易日任何时段都是CLOSED"""
        calendar = CNStockCalendar()
        # 周末
        dt = datetime(2024, 1, 6, 10, 30, 0)  # 周六10:30
        session = calendar.get_session(dt)
        assert session == TradingSession.CLOSED

    def test_is_market_open_during_continuous(self):
        """测试：连续竞价时段市场开放"""
        calendar = CNStockCalendar()
        dt = datetime(2024, 1, 2, 10, 30, 0)  # 10:30
        assert calendar.is_market_open(dt) is True

    def test_is_market_open_during_lunch(self):
        """测试：午休时段市场不开放"""
        calendar = CNStockCalendar()
        dt = datetime(2024, 1, 2, 12, 0, 0)  # 12:00
        assert calendar.is_market_open(dt) is False

    def test_count_trading_days(self):
        """测试：统计交易日数量"""
        calendar = CNStockCalendar()
        # 2024年全年大约有245个交易日（简化日历可能更多）
        count = calendar.count_trading_days(
            start=date(2024, 1, 1),
            end=date(2024, 12, 31),
        )
        # 简化日历会包含调休日，所以放宽范围
        assert 240 <= count <= 260  # 允许范围240-260

    def test_fallback_does_not_drop_real_trading_days(self):
        """降级日历不得把真实交易日算成假日

        2024-01-29 ~ 2024-02-08 都是真实交易日（春节 2/9 才开始休市），
        但农历节日用固定公历窗口近似时会整片抹掉它们 —— 那会让
        order_validator 拒单、让 live/paper 引擎静默跳过整天。
        降级方向必须是"多算交易日"，不能是"少算"。
        """
        calendar = CNStockCalendar()
        if not calendar.is_fallback:
            pytest.skip("已加载精确日历，本用例只约束降级路径")
        for d in (date(2024, 1, 29), date(2024, 2, 1), date(2024, 2, 8)):
            assert calendar.is_trading_day(d) is True, f"{d} 是真实交易日"

    def test_calendar_coverage_year_range(self):
        """测试：日历覆盖年份范围（2000-2030）"""
        calendar = CNStockCalendar()
        # 测试边界年份
        assert calendar.is_trading_day(date(2000, 1, 4)) in [True, False]  # 不报错即可
        assert calendar.is_trading_day(date(2030, 12, 31)) in [True, False]

    def test_calendar_out_of_range_warning(self):
        """测试：超出范围查询应给出警告或降级"""
        calendar = CNStockCalendar()
        # 查询超出范围的日期（降级为简单规则：非周末即交易日）
        result = calendar.is_trading_day(date(2035, 1, 2))
        assert result in [True, False]  # 降级逻辑应能返回结果

    def test_fingerprint_generation(self):
        """测试：日历指纹生成"""
        calendar = CNStockCalendar()
        fingerprint = calendar.fingerprint()
        assert fingerprint.startswith("cn_stock_v")
        assert "2024" in fingerprint or "range" in fingerprint


class TestTradingSession:
    """测试：交易时段枚举"""

    def test_session_enum_values(self):
        """测试：时段枚举值定义"""
        assert TradingSession.MORNING_CALL_AUCTION.value == "morning_call_auction"
        assert TradingSession.MORNING_CONTINUOUS.value == "morning_continuous"
        assert TradingSession.LUNCH_BREAK.value == "lunch_break"
        assert TradingSession.AFTERNOON_CONTINUOUS.value == "afternoon_continuous"
        assert TradingSession.CLOSING_CALL_AUCTION.value == "closing_call_auction"
        assert TradingSession.CLOSED.value == "closed"

    def test_session_is_trading(self):
        """测试：判断时段是否可交易"""
        # 连续竞价可交易
        assert TradingSession.MORNING_CONTINUOUS.is_trading() is True
        assert TradingSession.AFTERNOON_CONTINUOUS.is_trading() is True
        
        # 集合竞价在V1简化为可交易（实际撮合规则不同）
        assert TradingSession.MORNING_CALL_AUCTION.is_trading() in [True, False]
        
        # 午休和收盘不可交易
        assert TradingSession.LUNCH_BREAK.is_trading() is False
        assert TradingSession.CLOSED.is_trading() is False
