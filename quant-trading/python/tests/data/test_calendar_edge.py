"""Calendar 模块边界测试 — Phase 38

覆盖 calendar.py 中 10 个未覆盖的分支/边界场景。
"""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from aipha.data.calendar import (
    CalendarLoadError,
    CNStockCalendar,
    TradingSession,
)


# ============================================================
# 1. _load_calendar: 自定义 calendar_file 有效路径
# ============================================================
class TestCustomCalendarFile:
    def test_load_custom_file(self, tmp_path):
        """指定自定义 calendar_file 能优先加载"""
        cal_data = {
            "trading_days": ["2023-01-03", "2023-01-04", "2023-01-05"],
            "source": "custom_test",
            "range": {"start": "2023-01-03", "end": "2023-01-05"},
        }
        cal_file = tmp_path / "custom_cal.json"
        cal_file.write_text(json.dumps(cal_data), encoding="utf-8")

        cal = CNStockCalendar(calendar_file=cal_file)
        assert cal.is_trading_day(date(2023, 1, 3))
        assert cal.is_trading_day(date(2023, 1, 4))
        # 不在自定义文件中的日期
        assert not cal.is_trading_day(date(2023, 1, 6))


# ============================================================
# 2. _load_calendar: 损坏 JSON → except → continue → 降级到默认路径
# ============================================================
class TestCorruptCalendarFile:
    def test_invalid_json_fallback(self, tmp_path):
        """损坏的 JSON 应触发 warning 并 continue 到下一路径"""
        bad_file = tmp_path / "bad_cal.json"
        bad_file.write_text("{invalid json", encoding="utf-8")
        # 不会抛异常，因为会 continue 到默认路径或 fallback
        cal = CNStockCalendar(calendar_file=bad_file)
        # 至少 fallback 生效了
        assert cal._trading_days_cache is not None
        assert len(cal._trading_days_cache) > 0

    def test_missing_key_fallback(self, tmp_path):
        """JSON 有效但缺少 trading_days key"""
        bad_file = tmp_path / "nokey.json"
        bad_file.write_text('{"source": "test"}', encoding="utf-8")
        cal = CNStockCalendar(calendar_file=bad_file)
        assert cal._trading_days_cache is not None


# ============================================================
# 3. is_trading_day / get_trading_days: cache=None → CalendarLoadError
# ============================================================
class TestCacheNoneRaises:
    def _make_broken_calendar(self):
        cal = CNStockCalendar()
        cal._trading_days_cache = None
        return cal

    def test_is_trading_day_none_cache(self):
        cal = self._make_broken_calendar()
        with pytest.raises(CalendarLoadError, match="交易日历未加载"):
            cal.is_trading_day(date(2023, 1, 3))

    def test_get_trading_days_none_cache(self):
        cal = self._make_broken_calendar()
        with pytest.raises(CalendarLoadError, match="交易日历未加载"):
            cal.get_trading_days(date(2023, 1, 3), date(2023, 1, 10))


# ============================================================
# 4. get_next_trading_day: skip_days > 1
# ============================================================
class TestNextTradingDaySkip:
    def test_skip_3(self):
        cal = CNStockCalendar()
        # 连续 skip_days=3 应返回第 3 个交易日
        d = date(2023, 1, 3)  # 周二 — 交易日
        result = cal.get_next_trading_day(d, skip_days=3)
        # 验证在 d 之后，且中间有恰好 3 个交易日
        td = cal.get_trading_days(d + timedelta(days=1), result)
        assert len(td) == 3
        assert td[-1] == result

    def test_skip_5(self):
        cal = CNStockCalendar()
        d = date(2023, 6, 1)
        result = cal.get_next_trading_day(d, skip_days=5)
        td = cal.get_trading_days(d + timedelta(days=1), result)
        assert len(td) == 5


# ============================================================
# 5. get_prev_trading_day: skip_days > 1
# ============================================================
class TestPrevTradingDaySkip:
    def test_skip_3(self):
        cal = CNStockCalendar()
        d = date(2023, 1, 10)
        result = cal.get_prev_trading_day(d, skip_days=3)
        td = cal.get_trading_days(result, d - timedelta(days=1))
        assert len(td) == 3
        assert td[0] == result

    def test_skip_5(self):
        cal = CNStockCalendar()
        d = date(2023, 6, 15)
        result = cal.get_prev_trading_day(d, skip_days=5)
        td = cal.get_trading_days(result, d - timedelta(days=1))
        assert len(td) == 5


# ============================================================
# 6. get_next/prev_trading_day: 30天内找不到 → raise
# ============================================================
class TestNoTradingDayFound:
    def _make_empty_calendar(self):
        cal = CNStockCalendar()
        cal._trading_days_cache = set()  # 空 cache
        return cal

    def test_next_no_trading_day(self):
        cal = self._make_empty_calendar()
        with pytest.raises(CalendarLoadError, match="无法找到"):
            cal.get_next_trading_day(date(2023, 1, 1))

    def test_prev_no_trading_day(self):
        cal = self._make_empty_calendar()
        with pytest.raises(CalendarLoadError, match="无法找到"):
            cal.get_prev_trading_day(date(2023, 1, 31))


# ============================================================
# 7. get_session: 边界精确时刻
# ============================================================
class TestSessionBoundary:
    @pytest.fixture
    def cal(self):
        return CNStockCalendar()

    def _td(self, t: time) -> datetime:
        """构造已知交易日的 datetime"""
        return datetime(2023, 1, 3, t.hour, t.minute, t.second)

    def test_0915_morning_call(self, cal):
        assert cal.get_session(self._td(time(9, 15))) == TradingSession.MORNING_CALL_AUCTION

    def test_0925_gap(self, cal):
        """9:25 不在任何 [start, end) 区间 → CLOSED"""
        assert cal.get_session(self._td(time(9, 25))) == TradingSession.CLOSED

    def test_0930_morning_cont(self, cal):
        assert cal.get_session(self._td(time(9, 30))) == TradingSession.MORNING_CONTINUOUS

    def test_1130_lunch(self, cal):
        assert cal.get_session(self._td(time(11, 30))) == TradingSession.LUNCH_BREAK

    def test_1300_afternoon(self, cal):
        assert cal.get_session(self._td(time(13, 0))) == TradingSession.AFTERNOON_CONTINUOUS

    def test_1457_closing_call(self, cal):
        """14:57 — AFTERNOON_CONT_END == CLOSING_CALL_START == 14:57
        不满足 < 14:57，但满足 >= 14:57 → CLOSING_CALL_AUCTION"""
        assert cal.get_session(self._td(time(14, 57))) == TradingSession.CLOSING_CALL_AUCTION

    def test_1500_closed(self, cal):
        assert cal.get_session(self._td(time(15, 0))) == TradingSession.CLOSED

    def test_before_open(self, cal):
        assert cal.get_session(self._td(time(8, 0))) == TradingSession.CLOSED


# ============================================================
# 8. is_trading_day: 超范围降级 (年份 < 2000 或 > 2030)
# ============================================================
class TestOutOfRangeFallback:
    def test_year_before_2000(self):
        cal = CNStockCalendar()
        # 周三应该返回 True (weekday < 5)
        assert cal.is_trading_day(date(1999, 6, 2))  # 周三
        # 周六返回 False
        assert not cal.is_trading_day(date(1999, 6, 5))  # 周六

    def test_year_after_2030(self):
        cal = CNStockCalendar()
        assert cal.is_trading_day(date(2031, 3, 3))  # 周一
        assert not cal.is_trading_day(date(2031, 3, 1))  # 周六


# ============================================================
# 9. fingerprint: empty cache → fallback string
# ============================================================
class TestFingerprintEdge:
    def test_empty_cache_fallback(self):
        cal = CNStockCalendar()
        cal._trading_days_cache = set()  # 空非 None
        assert cal.fingerprint() == "cn_stock_v1_fallback"

    def test_none_cache_fallback(self):
        cal = CNStockCalendar()
        cal._trading_days_cache = None
        assert cal.fingerprint() == "cn_stock_v1_fallback"

    def test_normal_fingerprint(self):
        cal = CNStockCalendar()
        fp = cal.fingerprint()
        assert fp.startswith("cn_stock_v1_range_")
        assert "days_" in fp


# ============================================================
# 10. _is_obvious_holiday: 春节/端午/中秋正确标记（已修复）
# ============================================================
class TestObviousHolidayFixed:
    def test_lunar_holidays_not_guessed(self):
        """降级日历不猜农历节日 —— 这些都是真实交易日

        原实现用固定公历窗口近似春节/端午/中秋，结果把下面这三天
        （全是真实开市日）当成了假期：
        - 2023-02-01：2023 年春节休市 1/21~1/27，2/1 正常交易
        - 2023-06-12：2023 年端午休市 6/22~6/24
        - 2023-09-18：2023 年中秋在 9/29
        农历日期每年漂移 30 天以上，固定窗口不是"精度不够"而是错位。
        把真实交易日标成假期，order_validator 会拒单、engine 会整天跳过。
        """
        cal = CNStockCalendar()
        fallback = cal._generate_fallback_calendar()
        for d in (date(2023, 2, 1), date(2023, 6, 12), date(2023, 9, 18)):
            assert d in fallback, f"{d} 是真实交易日，不能被降级日历排除"

    def test_spring_festival_edge_not_holiday(self):
        """春节区间外的日期不应被标记为假期"""
        cal = CNStockCalendar()
        d = date(2023, 1, 25)  # 周三，不在缩窄后的区间内
        fallback = cal._generate_fallback_calendar()
        assert d in fallback  # 应该是交易日

    def test_non_holiday_still_trading(self):
        """非假期区间的工作日仍然是交易日"""
        cal = CNStockCalendar()
        # 3月15日非任何假期区间
        d = date(2023, 3, 15)  # 周三
        fallback = cal._generate_fallback_calendar()
        assert d in fallback

    def test_national_day_still_holiday(self):
        """国庆节依然正确标记"""
        cal = CNStockCalendar()
        d = date(2023, 10, 3)  # 周二
        fallback = cal._generate_fallback_calendar()
        assert d not in fallback


# ============================================================
# 11. Fallback 假日区间精度验证 (R13 修复)
# ============================================================
class TestFallbackHolidayRanges:
    """验证 _is_obvious_holiday 缩窄后不会丢失过多交易日。"""

    def test_annual_trading_days_reasonable(self):
        """每年的 fallback 交易日应在 230~250 天之间（A股实际约 242~244）"""
        cal = CNStockCalendar()
        fallback = cal._generate_fallback_calendar()
        for year in range(2015, 2026):
            year_days = [d for d in fallback if d.year == year]
            assert 225 <= len(year_days) <= 255, (
                f"Year {year}: {len(year_days)} trading days, expected 225~255"
            )

    def test_no_lunar_holiday_windows(self):
        """春节/端午/中秋所在月份不得出现任何猜测出来的假期

        除 1/1 元旦外，1、2、6、9 四个月里不应有 fallback 假期 ——
        有就说明农历窗口又被加回来了。
        """
        cal = CNStockCalendar()
        for year in range(2010, 2026):
            for month, last_day in [(1, 31), (2, 28), (6, 30), (9, 30)]:
                guessed = [
                    date(year, month, day)
                    for day in range(1, last_day + 1)
                    if cal._is_obvious_holiday(date(year, month, day))
                    and not (month == 1 and day == 1)  # 元旦是公历固定日
                ]
                assert not guessed, f"{year}-{month:02d} 出现猜测假期: {guessed}"

    def test_known_non_holidays_are_trading_days(self):
        """一些确定不是假日的日期不应被 fallback 误判"""
        cal = CNStockCalendar()
        non_holidays = [
            date(2024, 1, 22),  # 1月22日周一，不应被标记为春节
            date(2024, 2, 19),  # 2月19日周一，春节后
            date(2024, 6, 3),   # 6月3日周一，端午前
            date(2024, 6, 17),  # 6月17日周一，端午后
            date(2024, 9, 9),   # 9月9日周一，中秋前
            date(2024, 9, 23),  # 9月23日周一，中秋后
        ]
        for d in non_holidays:
            assert not cal._is_obvious_holiday(d), (
                f"{d} should NOT be marked as holiday"
            )

    def test_known_holidays_still_marked(self):
        """公历固定日期的假日仍然被正确标记

        只列公历固定假期。原用例还断言 2024-02-05「春节期间」，
        但 2024 年春节休市是 2/9~2/16，2/5 周一是正常交易日 ——
        那条断言本身就和市场事实相反。农历假期改由精确日历文件负责。
        """
        cal = CNStockCalendar()
        holidays = [
            date(2024, 1, 1),   # 元旦
            date(2024, 4, 5),   # 清明
            date(2024, 5, 1),   # 劳动节
            date(2024, 10, 1),  # 国庆
        ]
        for d in holidays:
            assert cal._is_obvious_holiday(d), (
                f"{d} should be marked as holiday"
            )

    def test_real_trading_days_never_dropped(self):
        """一组已核实的真实交易日必须全部保留在降级日历里"""
        cal = CNStockCalendar()
        fallback = cal._generate_fallback_calendar()
        real_trading_days = [
            date(2024, 1, 29),  # 春节前，正常交易
            date(2024, 2, 5),   # 春节前，正常交易
            date(2024, 2, 8),   # 春节休市前最后一个交易日
            date(2024, 6, 3),   # 端午前
            date(2024, 9, 13),  # 中秋休市前最后一个交易日
        ]
        for d in real_trading_days:
            assert d in fallback, f"{d} 是真实交易日，不能被降级日历排除"
