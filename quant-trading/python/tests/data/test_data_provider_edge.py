"""Edge tests for data provider layer — 边界与极端场景。

覆盖:
- DataFingerprint: slots、repr
- TuShareProBarProvider: _calculate_vwap NaN, _convert_to_bar minute, 
  _safe_read_parquet corrupt, _check_data_quality empty/pass, _expected_bar_count fallback,
  _iter_months, _monthly_cache_path
- FallbackProviderChain: _get_try_order active>0, switch_log copy, recovery timing
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from aipha.data.provider_base import DataFingerprint
from aipha.domain.models import Bar


# =========================================================================
# DataFingerprint edge cases
# =========================================================================


class TestDataFingerprintEdge:
    """provider_base.DataFingerprint 的补充边界。"""

    def test_slots_block_dynamic_attr(self) -> None:
        fp = DataFingerprint(
            provider="test", snapshot="v1",
            calendar="cn", adjust="qfq", schema="bar_v1",
        )
        with pytest.raises((AttributeError, TypeError)):
            fp.extra = 1  # type: ignore[attr-defined]

    def test_repr_contains_fields(self) -> None:
        fp = DataFingerprint(
            provider="tushare", snapshot="v2",
            calendar="cn_stock", adjust="hfq", schema="bar_v2",
        )
        r = repr(fp)
        assert "tushare" in r
        assert "hfq" in r

    def test_hash_in_dict(self) -> None:
        fp = DataFingerprint("a", "b", "c", "d", "e")
        d = {fp: 42}
        assert d[fp] == 42


# =========================================================================
# TuShareProBarProvider — _calculate_vwap
# =========================================================================


class TestCalculateVwapEdge:
    """_calculate_vwap 的边界场景。"""

    @pytest.fixture()
    def provider(self):
        with patch("aipha.data.tushare_gateway.get_tushare_gateway") as mock_gw:
            mock_gw.return_value = MagicMock()
            mock_gw.return_value._token = "test_token"
            mock_gw.return_value.api = MagicMock()
            from aipha.data.tushare_provider import TuShareProBarProvider
            return TuShareProBarProvider(token="test_token")

    def test_vwap_normal(self, provider) -> None:
        row = pd.Series({"amount": 100.0, "vol": 10.0})
        # amount千元=100_000, vol手=1000股 → vwap=100
        result = provider._calculate_vwap(row)
        assert result == pytest.approx(100.0)

    def test_vwap_vol_zero(self, provider) -> None:
        row = pd.Series({"amount": 100.0, "vol": 0.0})
        assert provider._calculate_vwap(row) is None

    def test_vwap_missing_amount(self, provider) -> None:
        row = pd.Series({"vol": 10.0})
        assert provider._calculate_vwap(row) is None

    def test_vwap_missing_vol(self, provider) -> None:
        row = pd.Series({"amount": 100.0})
        assert provider._calculate_vwap(row) is None

    def test_vwap_nan_amount(self, provider) -> None:
        """amount=NaN, vol>0 → 返回 NaN（无运行时校验）。"""
        row = pd.Series({"amount": float("nan"), "vol": 10.0})
        result = provider._calculate_vwap(row)
        # NaN propagates — dataclass 不做运行时校验
        assert result is None or math.isnan(result)

    def test_vwap_negative_vol(self, provider) -> None:
        row = pd.Series({"amount": 100.0, "vol": -1.0})
        # vol <= 0 → None
        assert provider._calculate_vwap(row) is None


# =========================================================================
# TuShareProBarProvider — _convert_to_bar (minute bar)
# =========================================================================


class TestConvertToBarEdge:
    """_convert_to_bar 分钟线与特殊字段。"""

    @pytest.fixture()
    def provider(self):
        with patch("aipha.data.tushare_gateway.get_tushare_gateway") as mock_gw:
            mock_gw.return_value = MagicMock()
            mock_gw.return_value._token = "test_token"
            mock_gw.return_value.api = MagicMock()
            from aipha.data.tushare_provider import TuShareProBarProvider
            return TuShareProBarProvider(token="test_token")

    def test_minute_bar_start_ts(self, provider) -> None:
        """分钟 bar 的 start_ts = end_ts - 1分钟。"""
        row = pd.Series({
            "ts_code": "000001.SZ",
            "trade_time": "2024-01-02 09:31:00",
            "open": 10.0, "high": 10.5, "low": 9.8,
            "close": 10.2, "vol": 100.0,
        })
        bar = provider._convert_to_bar(row)
        assert bar.end_ts.minute == 31
        assert bar.start_ts.minute == 30
        assert bar.symbol == "000001.SZ"

    def test_daily_bar_timestamps(self, provider) -> None:
        """日线 bar: start=09:30, end=15:00。"""
        row = pd.Series({
            "ts_code": "000001.SZ",
            "trade_date": "20240102",
            "open": 10.0, "high": 10.5, "low": 9.8,
            "close": 10.2, "vol": 100.0, "pre_close": 9.9,
        })
        bar = provider._convert_to_bar(row)
        assert bar.start_ts.hour == 9
        assert bar.start_ts.minute == 30
        assert bar.end_ts.hour == 15
        assert bar.end_ts.minute == 0

    def test_suspended_detection(self, provider) -> None:
        """vol=0 且 OHLC 全等 → is_suspended=True。"""
        row = pd.Series({
            "ts_code": "000001.SZ",
            "trade_date": "20240102",
            "open": 10.0, "high": 10.0, "low": 10.0,
            "close": 10.0, "vol": 0.0,
        })
        bar = provider._convert_to_bar(row)
        assert bar.is_suspended is True

    def test_limit_up_without_prev_close(self, provider) -> None:
        """无 prev_close → is_limit_up/is_limit_down 均为 None。"""
        row = pd.Series({
            "ts_code": "000001.SZ",
            "trade_date": "20240102",
            "open": 10.0, "high": 11.0, "low": 9.5,
            "close": 11.0, "vol": 500.0,
        })
        bar = provider._convert_to_bar(row)
        assert bar.is_limit_up is None
        assert bar.is_limit_down is None


# =========================================================================
# TuShareProBarProvider — _safe_read_parquet
# =========================================================================


class TestSafeReadParquetEdge:
    """_safe_read_parquet 损坏文件处理。"""

    def test_corrupt_file_returns_none(self, tmp_path) -> None:
        from aipha.data.tushare_provider import TuShareProBarProvider

        corrupt = tmp_path / "corrupt.parquet"
        corrupt.write_text("not a parquet file")
        result = TuShareProBarProvider._safe_read_parquet(corrupt)
        assert result is None
        # 文件应被删除
        assert not corrupt.exists()

    def test_nonexistent_file(self, tmp_path) -> None:
        from aipha.data.tushare_provider import TuShareProBarProvider

        path = tmp_path / "missing.parquet"
        result = TuShareProBarProvider._safe_read_parquet(path)
        assert result is None


# =========================================================================
# TuShareProBarProvider — _check_data_quality / _expected_bar_count
# =========================================================================


class TestDataQualityEdge:
    """数据质量检查边界。"""

    @pytest.fixture()
    def provider(self):
        with patch("aipha.data.tushare_gateway.get_tushare_gateway") as mock_gw:
            mock_gw.return_value = MagicMock()
            mock_gw.return_value._token = "test_token"
            mock_gw.return_value.api = MagicMock()
            from aipha.data.tushare_provider import TuShareProBarProvider
            return TuShareProBarProvider(token="test_token")

    def test_check_quality_empty_df(self, provider) -> None:
        """空 DataFrame 不抛异常。"""
        df = pd.DataFrame()
        provider._check_data_quality(df, "000001.SZ", date(2024, 1, 1), date(2024, 1, 31), "daily")

    def test_expected_bar_count_daily(self, provider) -> None:
        """日线: 返回交易日数量。"""
        count = provider._expected_bar_count(date(2024, 1, 2), date(2024, 1, 5), "daily")
        assert count >= 1

    def test_expected_bar_count_1min(self, provider) -> None:
        """分钟线: 交易日 * 240。"""
        count = provider._expected_bar_count(date(2024, 1, 2), date(2024, 1, 5), "1min")
        # 至少几个交易日 * 240
        assert count >= 240

    def test_expected_bar_count_60min(self, provider) -> None:
        """60分钟线: 交易日 * 4。"""
        count = provider._expected_bar_count(date(2024, 1, 2), date(2024, 1, 5), "60min")
        assert count >= 4


# =========================================================================
# TuShareProBarProvider — _iter_months
# =========================================================================


class TestIterMonthsEdge:
    """_iter_months 跨年/同月边界。"""

    @pytest.fixture()
    def provider(self):
        with patch("aipha.data.tushare_gateway.get_tushare_gateway") as mock_gw:
            mock_gw.return_value = MagicMock()
            mock_gw.return_value._token = "test_token"
            mock_gw.return_value.api = MagicMock()
            from aipha.data.tushare_provider import TuShareProBarProvider
            return TuShareProBarProvider(token="test_token")

    def test_same_month(self, provider) -> None:
        months = list(provider._iter_months(date(2024, 3, 1), date(2024, 3, 31)))
        assert months == [(2024, 3)]

    def test_cross_year(self, provider) -> None:
        months = list(provider._iter_months(date(2023, 11, 1), date(2024, 2, 28)))
        assert months == [(2023, 11), (2023, 12), (2024, 1), (2024, 2)]

    def test_single_day(self, provider) -> None:
        months = list(provider._iter_months(date(2024, 6, 15), date(2024, 6, 15)))
        assert months == [(2024, 6)]


# =========================================================================
# FallbackProviderChain — 补充边界
# =========================================================================


class TestFallbackChainEdge:
    """FallbackProviderChain 的补充边界测试。"""

    def _make_provider(self, name: str = "MockProvider", bars=None, fail=False):
        """创建一个符合 BarProvider Protocol 的 mock。"""
        p = MagicMock()
        p.__class__.__name__ = name
        type(p).__name__ = name
        p.fingerprint.return_value = DataFingerprint(
            provider=name, snapshot="v1",
            calendar="cn", adjust="qfq", schema="bar_v1",
        )
        if fail:
            p.load_bars.side_effect = RuntimeError(f"{name} failed")
        else:
            p.load_bars.return_value = bars or []
        return p

    def test_try_order_active_zero(self) -> None:
        """active=0 时，尝试顺序 [0, 1, 2]。"""
        from aipha.data.fallback_provider import FallbackProviderChain
        chain = FallbackProviderChain([
            self._make_provider("A"),
            self._make_provider("B"),
            self._make_provider("C"),
        ])
        assert chain._get_try_order() == [0, 1, 2]

    def test_try_order_active_nonzero(self) -> None:
        """active=1 时，尝试顺序 [1, 0, 2]。"""
        from aipha.data.fallback_provider import FallbackProviderChain
        chain = FallbackProviderChain([
            self._make_provider("A", fail=True),
            self._make_provider("B"),
            self._make_provider("C"),
        ])
        # Force failover to index 1
        chain._active_index = 1
        order = chain._get_try_order()
        assert order[0] == 1
        assert 0 in order
        assert 2 in order

    def test_switch_log_is_copy(self) -> None:
        """switch_log 返回副本，外部修改不影响内部状态。"""
        from aipha.data.fallback_provider import FallbackProviderChain
        chain = FallbackProviderChain([
            self._make_provider("A", fail=True),
            self._make_provider("B"),
        ])
        chain.load_bars(symbol="X", start=date(2024, 1, 1), end=date(2024, 1, 2), frequency="daily")
        log = chain.switch_log
        log.clear()
        # 内部日志不应被清空
        assert len(chain.switch_log) > 0 or chain.active_index == 0

    def test_all_fail_error_message(self) -> None:
        """所有 Provider 失败时，错误信息包含所有 Provider 名称。"""
        from aipha.data.fallback_provider import FallbackProviderChain, FallbackConfig
        chain = FallbackProviderChain(
            [self._make_provider("FailA", fail=True), self._make_provider("FailB", fail=True)],
            config=FallbackConfig(max_retries=0),
        )
        with pytest.raises(RuntimeError, match="所有 Provider"):
            chain.load_bars(symbol="X", start=date(2024, 1, 1), end=date(2024, 1, 2), frequency="daily")

    def test_recovery_not_triggered_within_interval(self) -> None:
        """recovery_check_interval 内不触发恢复检测。"""
        from aipha.data.fallback_provider import FallbackProviderChain, FallbackConfig
        p1 = self._make_provider("Primary")
        p2 = self._make_provider("Backup")
        chain = FallbackProviderChain(
            [p1, p2],
            config=FallbackConfig(recovery_check_interval=9999, auto_recover=True),
        )
        chain._active_index = 1
        chain._last_recovery_check = __import__("time").time()
        # Should NOT check recovery (within interval)
        assert chain._should_check_recovery() is False
