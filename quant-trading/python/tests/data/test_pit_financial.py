"""tests/data/test_pit_financial.py – PIT 财务数据框架测试

覆盖 ReportDate / FinancialStatement / InMemoryPITProvider / PITValidator / PITAwareFactorMixin
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from aipha.data.pit_financial import (
    FinancialFields,
    FinancialIndicator,
    FinancialStatement,
    InMemoryPITProvider,
    PITAwareFactorMixin,
    PITValidator,
    ReportDate,
    ReportPeriod,
)


# ── ReportPeriod ──────────────────────────────────────────


class TestReportPeriod:
    def test_values(self):
        assert ReportPeriod.Q1.value == "Q1"
        assert ReportPeriod.Q4.value == "Q4"

    def test_str_enum(self):
        # ReportPeriod 继承 str
        assert isinstance(ReportPeriod.Q2, str)


# ── ReportDate ────────────────────────────────────────────


class TestReportDate:
    def test_str(self):
        rd = ReportDate(2023, ReportPeriod.Q3)
        assert str(rd) == "2023Q3"

    def test_from_string(self):
        rd = ReportDate.from_string("2022Q1")
        assert rd.year == 2022
        assert rd.period == ReportPeriod.Q1

    def test_from_string_roundtrip(self):
        for p in ReportPeriod:
            rd = ReportDate(2024, p)
            assert ReportDate.from_string(str(rd)) == rd

    def test_to_date_q1(self):
        assert ReportDate(2023, ReportPeriod.Q1).to_date() == date(2023, 3, 31)

    def test_to_date_q2(self):
        assert ReportDate(2023, ReportPeriod.Q2).to_date() == date(2023, 6, 30)

    def test_to_date_q3(self):
        assert ReportDate(2023, ReportPeriod.Q3).to_date() == date(2023, 9, 30)

    def test_to_date_q4(self):
        assert ReportDate(2023, ReportPeriod.Q4).to_date() == date(2023, 12, 31)

    def test_previous_q2_to_q1(self):
        assert ReportDate(2023, ReportPeriod.Q2).previous() == ReportDate(2023, ReportPeriod.Q1)

    def test_previous_q1_wraps_year(self):
        assert ReportDate(2023, ReportPeriod.Q1).previous() == ReportDate(2022, ReportPeriod.Q4)

    def test_previous_q4_to_q3(self):
        assert ReportDate(2023, ReportPeriod.Q4).previous() == ReportDate(2023, ReportPeriod.Q3)

    def test_frozen(self):
        rd = ReportDate(2023, ReportPeriod.Q1)
        with pytest.raises(AttributeError):
            rd.year = 2024  # type: ignore[misc]

    def test_from_string_invalid(self):
        with pytest.raises((ValueError, KeyError)):
            ReportDate.from_string("2023X1")


# ── FinancialStatement ─────────────────────────────────────


class TestFinancialStatement:
    def _make(self, **kwargs):
        defaults = dict(
            symbol="000001.SZ",
            report_date=ReportDate(2023, ReportPeriod.Q3),
            announce_date=date(2023, 10, 28),
            statement_type="income",
            data={"revenue": Decimal("100.5"), "net_profit": Decimal("20.3")},
        )
        defaults.update(kwargs)
        return FinancialStatement(**defaults)

    def test_creation(self):
        s = self._make()
        assert s.symbol == "000001.SZ"
        assert s.statement_type == "income"

    def test_get_existing(self):
        s = self._make()
        assert s.get("revenue") == Decimal("100.5")

    def test_get_missing_default_none(self):
        s = self._make()
        assert s.get("missing") is None

    def test_get_missing_custom_default(self):
        s = self._make()
        assert s.get("missing", Decimal("0")) == Decimal("0")

    def test_empty_data(self):
        s = self._make(data={})
        assert s.get("revenue") is None


# ── FinancialIndicator ─────────────────────────────────────


class TestFinancialIndicator:
    def test_creation(self):
        fi = FinancialIndicator(
            symbol="600036.SH",
            report_date=ReportDate(2023, ReportPeriod.Q2),
            announce_date=date(2023, 8, 25),
            indicators={"roe": Decimal("0.15"), "roa": Decimal("0.03")},
        )
        assert fi.indicators["roe"] == Decimal("0.15")

    def test_empty_indicators(self):
        fi = FinancialIndicator(
            symbol="600036.SH",
            report_date=ReportDate(2023, ReportPeriod.Q2),
            announce_date=date(2023, 8, 25),
        )
        assert fi.indicators == {}


# ── InMemoryPITProvider ────────────────────────────────────


class TestInMemoryPITProvider:
    def _provider_with_statements(self):
        p = InMemoryPITProvider()
        # 同一个股票两期报表，公告日期不同
        p.add_statement(FinancialStatement(
            symbol="000001.SZ",
            report_date=ReportDate(2023, ReportPeriod.Q1),
            announce_date=date(2023, 4, 25),
            statement_type="income",
            data={"revenue": Decimal("50")},
        ))
        p.add_statement(FinancialStatement(
            symbol="000001.SZ",
            report_date=ReportDate(2023, ReportPeriod.Q2),
            announce_date=date(2023, 8, 28),
            statement_type="income",
            data={"revenue": Decimal("120")},
        ))
        return p

    def test_pit_returns_latest_available(self):
        p = self._provider_with_statements()
        # 查询日在 Q2 公告之后 → 返回 Q2
        s = p.get_statement_pit("000001.SZ", date(2023, 9, 1), "income")
        assert s is not None
        assert s.report_date.period == ReportPeriod.Q2

    def test_pit_returns_only_announced(self):
        p = self._provider_with_statements()
        # 查询日在 Q1 公告之后、Q2 公告之前
        s = p.get_statement_pit("000001.SZ", date(2023, 5, 1), "income")
        assert s is not None
        assert s.report_date.period == ReportPeriod.Q1

    def test_pit_returns_none_before_any(self):
        p = self._provider_with_statements()
        assert p.get_statement_pit("000001.SZ", date(2023, 1, 1), "income") is None

    def test_pit_returns_none_wrong_type(self):
        p = self._provider_with_statements()
        assert p.get_statement_pit("000001.SZ", date(2023, 9, 1), "balance") is None

    def test_pit_returns_none_wrong_symbol(self):
        p = self._provider_with_statements()
        assert p.get_statement_pit("600036.SH", date(2023, 9, 1), "income") is None

    def test_indicator_pit(self):
        p = InMemoryPITProvider()
        p.add_indicator(FinancialIndicator(
            symbol="000001.SZ",
            report_date=ReportDate(2023, ReportPeriod.Q1),
            announce_date=date(2023, 4, 25),
            indicators={"roe": Decimal("0.12"), "roa": Decimal("0.03"), "pe": Decimal("8")},
        ))
        result = p.get_indicator_pit("000001.SZ", date(2023, 5, 1), ["roe", "roa"])
        assert result is not None
        assert set(result.indicators.keys()) == {"roe", "roa"}

    def test_indicator_pit_none_before(self):
        p = InMemoryPITProvider()
        p.add_indicator(FinancialIndicator(
            symbol="000001.SZ",
            report_date=ReportDate(2023, ReportPeriod.Q1),
            announce_date=date(2023, 4, 25),
            indicators={"roe": Decimal("0.12")},
        ))
        assert p.get_indicator_pit("000001.SZ", date(2023, 3, 1), ["roe"]) is None

    def test_indicator_pit_wrong_symbol(self):
        p = InMemoryPITProvider()
        assert p.get_indicator_pit("999999.SZ", date(2023, 9, 1), ["roe"]) is None

    def test_statement_history(self):
        p = self._provider_with_statements()
        result = p.get_statement_history(
            "000001.SZ", date(2023, 1, 1), date(2023, 12, 31), "income"
        )
        assert len(result) == 2

    def test_statement_history_filtered(self):
        p = self._provider_with_statements()
        # 只取 Q1 公告日 ～ Q2 公告日之前
        result = p.get_statement_history(
            "000001.SZ", date(2023, 4, 1), date(2023, 5, 31), "income"
        )
        assert len(result) == 1
        assert result[0].report_date.period == ReportPeriod.Q1

    def test_statement_history_empty(self):
        p = InMemoryPITProvider()
        assert p.get_statement_history("X", date(2020, 1, 1), date(2020, 12, 31), "income") == []

    def test_multiple_symbols_isolated(self):
        p = InMemoryPITProvider()
        for sym in ("000001.SZ", "600036.SH"):
            p.add_statement(FinancialStatement(
                symbol=sym,
                report_date=ReportDate(2023, ReportPeriod.Q1),
                announce_date=date(2023, 4, 25),
                statement_type="income",
                data={"revenue": Decimal("100" if sym.startswith("0") else "200")},
            ))
        s1 = p.get_statement_pit("000001.SZ", date(2023, 5, 1), "income")
        s2 = p.get_statement_pit("600036.SH", date(2023, 5, 1), "income")
        assert s1 is not None and s2 is not None
        assert s1.get("revenue") == Decimal("100")
        assert s2.get("revenue") == Decimal("200")


# ── PITValidator ───────────────────────────────────────────


class TestPITValidator:
    def test_no_violations(self):
        v = PITValidator()
        v.log_access(date(2023, 9, 1), date(2023, 8, 28), "revenue", "000001.SZ")
        assert v.check_lookahead() == []

    def test_detects_lookahead(self):
        v = PITValidator()
        # data_date 在 query_date 之后 → 前视偏差
        v.log_access(date(2023, 5, 1), date(2023, 8, 28), "revenue", "000001.SZ")
        violations = v.check_lookahead()
        assert len(violations) == 1
        assert violations[0]["is_lookahead"] is True

    def test_clear(self):
        v = PITValidator()
        v.log_access(date(2023, 5, 1), date(2023, 8, 28), "revenue", "000001.SZ")
        v.clear()
        assert v.check_lookahead() == []

    def test_report_clean(self):
        v = PITValidator()
        v.log_access(date(2023, 9, 1), date(2023, 8, 28), "revenue", "000001.SZ")
        rpt = v.get_report()
        assert rpt["is_clean"] is True
        assert rpt["total_accesses"] == 1
        assert rpt["violation_count"] == 0

    def test_report_with_violations(self):
        v = PITValidator()
        for i in range(15):
            v.log_access(date(2023, 1, 1), date(2023, 12, 31), f"f{i}", "X")
        rpt = v.get_report()
        assert rpt["is_clean"] is False
        assert rpt["violation_count"] == 15
        assert len(rpt["violations"]) == 10  # 截取前 10

    def test_mixed_accesses(self):
        v = PITValidator()
        v.log_access(date(2023, 9, 1), date(2023, 8, 1), "a", "X")  # ok
        v.log_access(date(2023, 5, 1), date(2023, 8, 1), "b", "X")  # violation
        rpt = v.get_report()
        assert rpt["total_accesses"] == 2
        assert rpt["violation_count"] == 1


# ── PITAwareFactorMixin ────────────────────────────────────


class TestPITAwareFactorMixin:
    """使用 InMemoryPITProvider 驱动真实实现（非 mock）。"""

    def _make_mixin(self):
        class Factor(PITAwareFactorMixin):
            pass
        f = Factor()
        provider = InMemoryPITProvider()
        provider.add_statement(FinancialStatement(
            symbol="000001.SZ",
            report_date=ReportDate(2023, ReportPeriod.Q1),
            announce_date=date(2023, 4, 25),
            statement_type="income",
            data={"revenue": Decimal("100")},
        ))
        f.set_pit_provider(provider)
        return f

    def test_no_provider_raises(self):
        class Factor(PITAwareFactorMixin):
            pass
        f = Factor()
        with pytest.raises(RuntimeError, match="PIT provider not set"):
            f.get_financial_value("X", date(2023, 6, 1), "revenue")

    def test_get_value_available(self):
        f = self._make_mixin()
        val = f.get_financial_value("000001.SZ", date(2023, 5, 1), "revenue", "income")
        assert val == Decimal("100")

    def test_get_value_not_yet_announced(self):
        f = self._make_mixin()
        val = f.get_financial_value("000001.SZ", date(2023, 3, 1), "revenue", "income")
        assert val is None

    def test_get_value_missing_field(self):
        f = self._make_mixin()
        val = f.get_financial_value("000001.SZ", date(2023, 5, 1), "bogus", "income")
        assert val is None

    def test_validator_logs_access(self):
        f = self._make_mixin()
        v = PITValidator()
        f.set_pit_validator(v)
        f.get_financial_value("000001.SZ", date(2023, 5, 1), "revenue", "income")
        rpt = v.get_report()
        assert rpt["total_accesses"] == 1
        assert rpt["is_clean"] is True


# ── FinancialFields ────────────────────────────────────────


class TestFinancialFields:
    def test_revenue_constant(self):
        assert FinancialFields.REVENUE == "revenue"

    def test_roe_constant(self):
        assert FinancialFields.ROE == "roe"

    def test_has_all_expected(self):
        expected = {"REVENUE", "NET_PROFIT", "ROE", "ROA", "PE", "PB", "PS",
                    "GROSS_MARGIN", "NET_MARGIN", "TOTAL_ASSETS", "TOTAL_LIABILITIES",
                    "TOTAL_EQUITY", "CASH", "OPERATING_PROFIT", "NET_PROFIT_DEDUCTED",
                    "CASH_FROM_OPERATIONS", "CASH_FROM_INVESTING", "CASH_FROM_FINANCING"}
        actual = {k for k in dir(FinancialFields) if k.isupper() and not k.startswith("_")}
        assert expected.issubset(actual)
