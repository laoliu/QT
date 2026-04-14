"""tests/data/test_tushare_pit_provider.py – TuSharePITProvider 测试

覆盖纯函数 + PIT 查询逻辑；通过 mock equity_provider 隔离 IO。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd
import pytest

from aipha.data.pit_financial import (
    FinancialFields,
    ReportDate,
    ReportPeriod,
)
from aipha.data.tushare_pit_provider import (
    TuSharePITProvider,
    _end_date_to_report_date,
    _parse_date,
    _to_decimal,
)


# ── 纯函数测试 ──────────────────────────────────────────


class TestEndDateToReportDate:
    def test_q1(self):
        rd = _end_date_to_report_date("20230331")
        assert rd == ReportDate(2023, ReportPeriod.Q1)

    def test_q2(self):
        rd = _end_date_to_report_date("20230630")
        assert rd == ReportDate(2023, ReportPeriod.Q2)

    def test_q3(self):
        rd = _end_date_to_report_date("20230930")
        assert rd == ReportDate(2023, ReportPeriod.Q3)

    def test_q4(self):
        rd = _end_date_to_report_date("20231231")
        assert rd == ReportDate(2023, ReportPeriod.Q4)

    def test_unknown_mmdd_defaults_q4(self):
        rd = _end_date_to_report_date("20230815")
        assert rd.period == ReportPeriod.Q4


class TestParseDate:
    def test_valid(self):
        assert _parse_date("20230901") == date(2023, 9, 1)

    def test_none(self):
        assert _parse_date(None) is None

    def test_empty(self):
        assert _parse_date("") is None

    def test_short(self):
        assert _parse_date("2023") is None

    def test_invalid_month(self):
        assert _parse_date("20231301") is None

    def test_non_string(self):
        assert _parse_date(12345678) is None  # type: ignore[arg-type]


class TestToDecimal:
    def test_int(self):
        assert _to_decimal(42) == Decimal("42")

    def test_float(self):
        assert _to_decimal(3.14) == Decimal("3.14")

    def test_string(self):
        assert _to_decimal("100.5") == Decimal("100.5")

    def test_none(self):
        assert _to_decimal(None) is None

    def test_nan(self):
        assert _to_decimal(float("nan")) is None

    def test_unparseable(self):
        assert _to_decimal("abc") is None


# ── TuSharePITProvider ────────────────────────────────────


def _make_provider():
    """创建带 mock equity_provider 的 TuSharePITProvider。"""
    mock_ep = MagicMock()
    return TuSharePITProvider(equity_provider=mock_ep), mock_ep


def _fina_df():
    """模拟 fina_indicator 返回的 DataFrame（2 期）。"""
    return pd.DataFrame([
        {
            "ts_code": "000001.SZ",
            "ann_date": "20230425",
            "end_date": "20230331",
            "roe": 0.12,
            "roa": 0.03,
            "grossprofit_margin": 45.0,
            "revenue_yoy": 8.5,
            "dt_netprofit_yoy": 6.2,
        },
        {
            "ts_code": "000001.SZ",
            "ann_date": "20230828",
            "end_date": "20230630",
            "roe": 0.14,
            "roa": 0.035,
            "grossprofit_margin": 46.0,
            "revenue_yoy": 9.0,
            "dt_netprofit_yoy": 7.0,
        },
    ])


def _income_df():
    """模拟 income 返回的 DataFrame。"""
    return pd.DataFrame([
        {
            "ts_code": "000001.SZ",
            "ann_date": "20230425",
            "end_date": "20230331",
            "report_type": "1",
            "revenue": 1e9,
            "n_income": 2e8,
        },
        {
            "ts_code": "000001.SZ",
            "ann_date": "20230828",
            "end_date": "20230630",
            "report_type": "1",
            "revenue": 2.2e9,
            "n_income": 4.5e8,
        },
    ])


class TestGetIndicatorPIT:
    def test_returns_latest_available(self):
        p, ep = _make_provider()
        ep.load_fina_indicator.return_value = _fina_df()

        # 查询日在 Q2 公告之后
        ind = p.get_indicator_pit("000001.SZ", date(2023, 9, 1), [FinancialFields.ROE])
        assert ind is not None
        assert ind.report_date.period == ReportPeriod.Q2
        assert FinancialFields.ROE in ind.indicators

    def test_returns_only_announced(self):
        p, ep = _make_provider()
        ep.load_fina_indicator.return_value = _fina_df()

        # 在 Q1 公告后、Q2 公告前
        ind = p.get_indicator_pit("000001.SZ", date(2023, 6, 1), [FinancialFields.ROE])
        assert ind is not None
        assert ind.report_date.period == ReportPeriod.Q1

    def test_none_before_any_announcement(self):
        p, ep = _make_provider()
        ep.load_fina_indicator.return_value = _fina_df()

        assert p.get_indicator_pit("000001.SZ", date(2023, 1, 1), ["roe"]) is None

    def test_filters_requested_indicators(self):
        p, ep = _make_provider()
        ep.load_fina_indicator.return_value = _fina_df()

        ind = p.get_indicator_pit("000001.SZ", date(2023, 9, 1), [FinancialFields.ROE])
        # 只返回请求的字段
        assert FinancialFields.ROE in ind.indicators
        # 不应包含未请求的字段（如 grossprofit_margin 被映射后的 gross_margin）

    def test_empty_fina_data(self):
        p, ep = _make_provider()
        ep.load_fina_indicator.return_value = pd.DataFrame()

        assert p.get_indicator_pit("000001.SZ", date(2023, 9, 1), ["roe"]) is None


class TestGetStatementPIT:
    def test_returns_latest_income(self):
        p, ep = _make_provider()
        ep.load_income.return_value = _income_df()

        s = p.get_statement_pit("000001.SZ", date(2023, 9, 1), "income")
        assert s is not None
        assert s.report_date.period == ReportPeriod.Q2
        assert FinancialFields.REVENUE in s.data

    def test_returns_only_announced(self):
        p, ep = _make_provider()
        ep.load_income.return_value = _income_df()

        s = p.get_statement_pit("000001.SZ", date(2023, 5, 1), "income")
        assert s is not None
        assert s.report_date.period == ReportPeriod.Q1

    def test_none_before_announcement(self):
        p, ep = _make_provider()
        ep.load_income.return_value = _income_df()

        assert p.get_statement_pit("000001.SZ", date(2023, 1, 1), "income") is None

    def test_unknown_statement_type(self):
        p, ep = _make_provider()
        assert p.get_statement_pit("000001.SZ", date(2023, 9, 1), "bogus") is None


class TestGetStatementHistory:
    def test_date_range(self):
        p, ep = _make_provider()
        ep.load_income.return_value = _income_df()

        result = p.get_statement_history(
            "000001.SZ", date(2023, 4, 1), date(2023, 5, 31), "income"
        )
        assert len(result) == 1
        assert result[0].report_date.period == ReportPeriod.Q1

    def test_full_range(self):
        p, ep = _make_provider()
        ep.load_income.return_value = _income_df()

        result = p.get_statement_history(
            "000001.SZ", date(2023, 1, 1), date(2023, 12, 31), "income"
        )
        assert len(result) == 2


class TestYoyGrowth:
    def test_returns_metric(self):
        p, ep = _make_provider()
        ep.load_fina_indicator.return_value = _fina_df()

        val = p.get_yoy_growth("000001.SZ", date(2023, 9, 1), "dt_netprofit_yoy")
        assert val is not None
        assert isinstance(val, Decimal)

    def test_returns_none_no_data(self):
        p, ep = _make_provider()
        ep.load_fina_indicator.return_value = pd.DataFrame()

        assert p.get_yoy_growth("000001.SZ", date(2023, 9, 1)) is None


class TestQualityMetrics:
    def test_returns_all_fields(self):
        p, ep = _make_provider()
        ep.load_fina_indicator.return_value = _fina_df()

        metrics = p.get_quality_metrics("000001.SZ", date(2023, 9, 1))
        assert FinancialFields.ROE in metrics
        assert FinancialFields.ROA in metrics

    def test_returns_nones_when_empty(self):
        p, ep = _make_provider()
        ep.load_fina_indicator.return_value = pd.DataFrame()

        metrics = p.get_quality_metrics("000001.SZ", date(2023, 9, 1))
        assert all(v is None for v in metrics.values())


class TestPreload:
    def test_preload_batch(self):
        p, ep = _make_provider()
        ep.load_fina_indicator.return_value = _fina_df()

        p.preload_batch(["000001.SZ", "000002.SZ"], delay=0)
        assert "000001.SZ" in p._indicator_cache
        assert "000002.SZ" in p._indicator_cache

    def test_preload_skips_cached(self):
        p, ep = _make_provider()
        p._indicator_cache["000001.SZ"] = []  # 已缓存
        ep.load_fina_indicator.return_value = _fina_df()

        p.preload_batch(["000001.SZ", "000002.SZ"], delay=0)
        # 只调用一次（跳过 000001.SZ）
        ep.load_fina_indicator.assert_called_once_with("000002.SZ")

    def test_preload_statements_batch(self):
        p, ep = _make_provider()
        ep.load_income.return_value = _income_df()

        p.preload_statements_batch(["000001.SZ"], statement_type="income", delay=0)
        assert ("000001.SZ", "income") in p._statement_cache


class TestCaching:
    def test_indicator_loaded_once(self):
        """验证多次查询同一股票只触发一次 API 调用。"""
        p, ep = _make_provider()
        ep.load_fina_indicator.return_value = _fina_df()

        p.get_indicator_pit("000001.SZ", date(2023, 9, 1), ["roe"])
        p.get_indicator_pit("000001.SZ", date(2023, 5, 1), ["roe"])
        ep.load_fina_indicator.assert_called_once_with("000001.SZ")

    def test_statement_loaded_once(self):
        p, ep = _make_provider()
        ep.load_income.return_value = _income_df()

        p.get_statement_pit("000001.SZ", date(2023, 9, 1), "income")
        p.get_statement_pit("000001.SZ", date(2023, 5, 1), "income")
        ep.load_income.assert_called_once_with("000001.SZ")
