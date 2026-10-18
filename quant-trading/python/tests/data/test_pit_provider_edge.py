"""Edge tests for data/tushare_pit_provider.py — 边界与极端场景。

覆盖:
- _end_date_to_report_date: Q1-Q4 + 未知月份
- _parse_date: None/空/短字符串/无效日期
- _to_decimal: None/NaN/非数值字符串/正常
- TuSharePITProvider: 空数据/无公告日期/PIT 过滤/质量指标
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd
import pytest

from aipha.data.tushare_pit_provider import (
    TuSharePITProvider,
    _end_date_to_report_date,
    _parse_date,
    _to_decimal,
)


# =========================================================================
# _end_date_to_report_date
# =========================================================================


class TestEndDateToReportDate:
    """TuShare end_date → ReportDate 映射。"""

    def test_q1(self) -> None:
        rd = _end_date_to_report_date("20240331")
        assert rd.year == 2024
        assert rd.period.value == "Q1"

    def test_q2(self) -> None:
        rd = _end_date_to_report_date("20240630")
        assert rd.year == 2024
        assert rd.period.value == "Q2"

    def test_q3(self) -> None:
        rd = _end_date_to_report_date("20240930")
        assert rd.year == 2024
        assert rd.period.value == "Q3"

    def test_q4(self) -> None:
        rd = _end_date_to_report_date("20241231")
        assert rd.year == 2024
        assert rd.period.value == "Q4"

    def test_unknown_mmdd_defaults_q4(self) -> None:
        rd = _end_date_to_report_date("20240115")
        assert rd.period.value == "Q4"


# =========================================================================
# _parse_date
# =========================================================================


class TestParseDate:
    """安全日期解析。"""

    def test_none(self) -> None:
        assert _parse_date(None) is None

    def test_empty_string(self) -> None:
        assert _parse_date("") is None

    def test_short_string(self) -> None:
        assert _parse_date("2024") is None

    def test_valid_date(self) -> None:
        assert _parse_date("20240315") == date(2024, 3, 15)

    def test_invalid_month(self) -> None:
        assert _parse_date("20241301") is None

    def test_non_string(self) -> None:
        assert _parse_date(12345678) is None  # type: ignore[arg-type]


# =========================================================================
# _to_decimal
# =========================================================================


class TestToDecimal:
    """安全 Decimal 转换。"""

    def test_none(self) -> None:
        assert _to_decimal(None) is None

    def test_nan(self) -> None:
        assert _to_decimal(float("nan")) is None

    def test_normal_float(self) -> None:
        assert _to_decimal(3.14) == Decimal("3.14")

    def test_int(self) -> None:
        assert _to_decimal(42) == Decimal("42")

    def test_string_number(self) -> None:
        assert _to_decimal("1.5") == Decimal("1.5")

    def test_invalid_string(self) -> None:
        assert _to_decimal("abc") is None


# =========================================================================
# TuSharePITProvider — 核心 PIT 查询
# =========================================================================


class TestPITProviderEdge:
    """TuSharePITProvider 的边界路径。"""

    @pytest.fixture()
    def provider(self):
        mock_eq = MagicMock()
        mock_eq.load_fina_indicator.return_value = pd.DataFrame({
            "ann_date": ["20240301", "20240801"],
            "end_date": ["20231231", "20240630"],
            "roe": [15.0, 16.0],
            "roa": [5.0, 5.5],
            "grossprofit_margin": [30.0, 32.0],
        })
        mock_eq.load_income.return_value = pd.DataFrame({
            "ann_date": ["20240301"],
            "end_date": ["20231231"],
            "report_type": ["1"],
            "revenue": [1e9],
            "n_income": [1e8],
        })
        mock_eq.load_balancesheet.return_value = pd.DataFrame()
        return TuSharePITProvider(mock_eq)

    def test_indicator_pit_before_announcement(self, provider) -> None:
        """query_date 在所有公告日之前 → None。"""
        result = provider.get_indicator_pit("000001.SZ", date(2024, 1, 1), ["roe"])
        assert result is None

    def test_indicator_pit_between_announcements(self, provider) -> None:
        """query_date 在两次公告之间 → 返回第一次。"""
        result = provider.get_indicator_pit("000001.SZ", date(2024, 5, 1), ["roe"])
        assert result is not None
        assert result.announce_date == date(2024, 3, 1)

    def test_indicator_pit_after_all(self, provider) -> None:
        """query_date 在最后公告之后 → 返回最新。"""
        result = provider.get_indicator_pit("000001.SZ", date(2024, 12, 1), ["roe"])
        assert result is not None
        assert result.announce_date == date(2024, 8, 1)

    def test_indicator_filters_requested_names(self, provider) -> None:
        """只返回请求的指标名。"""
        result = provider.get_indicator_pit("000001.SZ", date(2024, 12, 1), ["roe"])
        assert result is not None
        assert "roe" in result.indicators
        # Unrequested fields not in result
        assert "roa" not in result.indicators

    def test_indicator_empty_data(self) -> None:
        mock_eq = MagicMock()
        mock_eq.load_fina_indicator.return_value = pd.DataFrame()
        prov = TuSharePITProvider(mock_eq)
        result = prov.get_indicator_pit("EMPTY", date(2024, 1, 1), ["roe"])
        assert result is None

    def test_statement_pit_income(self, provider) -> None:
        result = provider.get_statement_pit("000001.SZ", date(2024, 12, 1), "income")
        assert result is not None
        assert result.statement_type == "income"

    def test_statement_unknown_type(self, provider) -> None:
        """未知报表类型 → 空缓存，返回 None。"""
        result = provider.get_statement_pit("000001.SZ", date(2024, 12, 1), "unknown_type")
        assert result is None

    def test_statement_history_date_filter(self, provider) -> None:
        stmts = provider.get_statement_history(
            "000001.SZ", date(2024, 1, 1), date(2024, 6, 30), "income",
        )
        assert isinstance(stmts, list)
        # ann_date=20240301 在范围内
        assert len(stmts) == 1

    def test_get_yoy_growth_none(self) -> None:
        mock_eq = MagicMock()
        mock_eq.load_fina_indicator.return_value = pd.DataFrame()
        prov = TuSharePITProvider(mock_eq)
        assert prov.get_yoy_growth("EMPTY", date(2024, 1, 1)) is None

    def test_get_quality_metrics_none(self) -> None:
        mock_eq = MagicMock()
        mock_eq.load_fina_indicator.return_value = pd.DataFrame()
        prov = TuSharePITProvider(mock_eq)
        qm = prov.get_quality_metrics("EMPTY", date(2024, 1, 1))
        assert all(v is None for v in qm.values())

    def test_preload_batch_skips_cached(self) -> None:
        mock_eq = MagicMock()
        mock_eq.load_fina_indicator.return_value = pd.DataFrame()
        prov = TuSharePITProvider(mock_eq)
        prov._indicator_cache["A"] = []  # already cached
        prov.preload_batch(["A", "B"], delay=0)
        # A skipped, B loaded
        mock_eq.load_fina_indicator.assert_called_once_with("B")
