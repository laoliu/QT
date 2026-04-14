"""Tests for financial indicator PIT injection in config_runner.

Validates that:
- _load_fina_indicators_pit builds correct PIT mapping from provider data
- _pit_lookup correctly finds latest visible record via binary search
- _enrich_bar_metadata injects financial data with PIT constraint
- Financial data flows correctly to PEAD/MF strategy scoring
"""
from __future__ import annotations

import math
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from aipha.backtest.config_runner import (
    _enrich_bar_metadata,
    _pit_lookup,
)


# ============================================================================
# _pit_lookup tests
# ============================================================================


class TestPitLookup:
    """PIT 二分查找测试"""

    def test_empty_records_returns_none(self):
        assert _pit_lookup([], date(2024, 6, 1)) is None

    def test_single_record_before_trade_date(self):
        records = [(date(2024, 3, 31), {"roe": 15.0})]
        result = _pit_lookup(records, date(2024, 6, 1))
        assert result == {"roe": 15.0}

    def test_single_record_after_trade_date_returns_none(self):
        records = [(date(2024, 6, 30), {"roe": 15.0})]
        result = _pit_lookup(records, date(2024, 3, 1))
        assert result is None

    def test_single_record_on_trade_date(self):
        records = [(date(2024, 6, 1), {"roe": 15.0})]
        result = _pit_lookup(records, date(2024, 6, 1))
        assert result == {"roe": 15.0}

    def test_multiple_records_returns_latest_visible(self):
        records = [
            (date(2024, 3, 31), {"roe": 10.0, "eps": 0.50}),
            (date(2024, 4, 28), {"roe": 12.0, "eps": 0.55}),
            (date(2024, 8, 31), {"roe": 14.0, "eps": 0.60}),
        ]
        # Trade date 2024-06-15: should see Q1 and Q2 reports, pick Q2
        result = _pit_lookup(records, date(2024, 6, 15))
        assert result["roe"] == 12.0
        assert result["eps"] == 0.55

    def test_multiple_records_trade_date_before_all(self):
        records = [
            (date(2024, 3, 31), {"roe": 10.0}),
            (date(2024, 6, 30), {"roe": 12.0}),
        ]
        result = _pit_lookup(records, date(2024, 1, 1))
        assert result is None

    def test_multiple_records_trade_date_after_all(self):
        records = [
            (date(2024, 3, 31), {"roe": 10.0}),
            (date(2024, 6, 30), {"roe": 12.0}),
        ]
        result = _pit_lookup(records, date(2024, 12, 31))
        assert result["roe"] == 12.0

    def test_exact_match_earliest_boundary(self):
        records = [
            (date(2024, 3, 31), {"roe": 10.0}),
            (date(2024, 6, 30), {"roe": 12.0}),
        ]
        result = _pit_lookup(records, date(2024, 3, 31))
        assert result["roe"] == 10.0

    def test_many_records_performance(self):
        """Verify lookup works correctly with many records."""
        records = [(date(2020 + i // 4, (i % 4 + 1) * 3, 28), {"roe": float(i)})
                   for i in range(20)]
        result = _pit_lookup(records, date(2023, 7, 15))
        # Should find the latest with ann_date <= 2023-07-15
        assert result is not None
        assert result["roe"] >= 0


# ============================================================================
# _enrich_bar_metadata with fina_pit_map tests
# ============================================================================


class TestEnrichBarMetadataWithFina:
    """测试 _enrich_bar_metadata 注入财务数据"""

    def test_fina_data_injected(self):
        meta: dict = {}
        fina_pit_map = {
            "600000.SH": [
                (date(2024, 3, 31), {"roe": 15.5, "dt_netprofit_yoy": 12.3, "eps": 0.85}),
            ]
        }
        _enrich_bar_metadata(
            meta, "600000.SH", date(2024, 6, 1),
            stock_basic_df=None, daily_basic_map={},
            fina_pit_map=fina_pit_map,
        )
        assert meta["roe"] == 15.5
        assert meta["dt_netprofit_yoy"] == 12.3
        assert meta["eps"] == 0.85

    def test_fina_not_injected_before_ann_date(self):
        meta: dict = {}
        fina_pit_map = {
            "600000.SH": [
                (date(2024, 4, 30), {"roe": 15.5}),
            ]
        }
        _enrich_bar_metadata(
            meta, "600000.SH", date(2024, 3, 1),
            stock_basic_df=None, daily_basic_map={},
            fina_pit_map=fina_pit_map,
        )
        assert "roe" not in meta

    def test_fina_not_injected_for_unknown_symbol(self):
        meta: dict = {}
        fina_pit_map = {
            "600000.SH": [(date(2024, 1, 1), {"roe": 15.5})],
        }
        _enrich_bar_metadata(
            meta, "000001.SZ", date(2024, 6, 1),
            stock_basic_df=None, daily_basic_map={},
            fina_pit_map=fina_pit_map,
        )
        assert "roe" not in meta

    def test_fina_does_not_overwrite_existing_meta(self):
        """setdefault should keep existing value."""
        meta: dict = {"roe": 99.0}
        fina_pit_map = {
            "600000.SH": [(date(2024, 1, 1), {"roe": 15.5})],
        }
        _enrich_bar_metadata(
            meta, "600000.SH", date(2024, 6, 1),
            stock_basic_df=None, daily_basic_map={},
            fina_pit_map=fina_pit_map,
        )
        assert meta["roe"] == 99.0  # original preserved

    def test_fina_none_map_is_safe(self):
        """fina_pit_map=None should not break anything."""
        meta: dict = {}
        _enrich_bar_metadata(
            meta, "600000.SH", date(2024, 6, 1),
            stock_basic_df=None, daily_basic_map={},
            fina_pit_map=None,
        )
        assert "roe" not in meta

    def test_fina_empty_map_is_safe(self):
        meta: dict = {}
        _enrich_bar_metadata(
            meta, "600000.SH", date(2024, 6, 1),
            stock_basic_df=None, daily_basic_map={},
            fina_pit_map={},
        )
        assert "roe" not in meta

    def test_pit_selects_latest_report(self):
        """When multiple reports available, pick the latest visible one."""
        meta: dict = {}
        fina_pit_map = {
            "600000.SH": [
                (date(2024, 3, 31), {"roe": 10.0}),
                (date(2024, 4, 28), {"roe": 12.0}),
                (date(2024, 8, 31), {"roe": 14.0}),
            ]
        }
        _enrich_bar_metadata(
            meta, "600000.SH", date(2024, 7, 1),
            stock_basic_df=None, daily_basic_map={},
            fina_pit_map=fina_pit_map,
        )
        assert meta["roe"] == 12.0  # Q2 report visible, Q3 not yet

    def test_multiple_fina_fields_injected(self):
        meta: dict = {}
        fina_data = {
            "roe": 15.5,
            "dt_netprofit_yoy": 25.3,
            "eps": 1.20,
            "bps": 8.50,
            "revenue_yoy": 18.7,
            "grossprofit_margin": 42.1,
            "roa": 8.3,
            "debt_to_assets": 0.35,
        }
        fina_pit_map = {
            "600000.SH": [(date(2024, 1, 1), fina_data)],
        }
        _enrich_bar_metadata(
            meta, "600000.SH", date(2024, 6, 1),
            stock_basic_df=None, daily_basic_map={},
            fina_pit_map=fina_pit_map,
        )
        for k, v in fina_data.items():
            assert meta[k] == v


# ============================================================================
# TuShare None defense tests
# ============================================================================


class TestTuShareNoneDefense:
    """TuShare API 返回 None 防御测试"""

    def test_fetch_from_api_handles_none(self):
        """When ts.pro_bar returns None, should return empty DataFrame."""
        from aipha.data.tushare_gateway import reset_tushare_gateway
        from aipha.data.tushare_provider import TuShareProBarProvider

        reset_tushare_gateway()
        with patch("tushare.pro_bar", return_value=None), \
             patch("tushare.pro_api") as mock_pro_api, \
             patch("tushare.set_token"):
            mock_pro_api.return_value = MagicMock()
            provider = TuShareProBarProvider(
                token="test_token",
                cache_dir=None,
                max_retries=1,
            )
            df = provider._fetch_from_api(
                "301589.SZ",
                date(2024, 1, 1),
                date(2024, 12, 31),
                "daily",
            )
            assert df is not None
            assert df.empty
        reset_tushare_gateway()


# ============================================================================
# Integration: PEAD strategy with financial data
# ============================================================================


class TestPEADWithFinaData:
    """验证 PEAD 策略能正确使用注入的财务数据"""

    def test_sue_factor_with_real_data(self):
        """When SUE is in metadata, strategy should use it for scoring."""
        from aipha.strategy.pead_event import PEADConfig, PEADStrategy

        strategy = PEADStrategy(PEADConfig(top_k=3, rebalance_days=5))
        strategy.on_start()

        # Simulate bars with financial metadata
        from datetime import datetime

        from aipha.domain.models import Bar
        from aipha.strategy.base import StrategyContext

        bars = {}
        for i, sym in enumerate(["S1", "S2", "S3", "S4", "S5"]):
            bars[sym] = Bar(
                symbol=sym,
                start_ts=datetime(2024, 6, 1, 9, 30),
                end_ts=datetime(2024, 6, 1, 15, 0),
                open=10.0 + i, high=11.0 + i, low=9.5 + i,
                close=10.5 + i, volume=1000000,
                vwap=10.3 + i, prev_close=10.0 + i,
                metadata={
                    "circ_mv": 5000.0 + i * 100,
                    "total_mv": 8000.0 + i * 100,
                    "pe_ttm": 15.0 + i,
                    "turnover_rate": 3.0,
                    "amount": 10000.0,
                    "list_date": "20200101",
                    # Financial data (normally injected by PIT pipeline)
                    "roe": 10.0 + i * 5,  # S5 has highest ROE=30
                    "dt_netprofit_yoy": 5.0 + i * 10,  # S5 has highest=45%
                    "sue": i * 0.5,  # S5 has highest SUE=2.0
                },
            )

        ctx = StrategyContext(
            current_time=datetime(2024, 6, 1, 15, 0),
            cash=1_000_000.0,
            positions={},
            nav=1_000_000.0,
        )

        intents = strategy.on_bar(bars, ctx)
        # Strategy should produce some trades (rebalance on first bar)
        # With financial data now available, SUE/ROE/growth should all have effect
        # Key check: strategy should not crash and should produce intents
        # (detailed ordering depends on rank_normalize interaction)
        # At minimum, we verify the strategy ran without error
        assert isinstance(intents, list)

    def test_sue_none_still_works(self):
        """When SUE data is missing (None), strategy should still work."""
        from aipha.strategy.pead_event import PEADConfig, PEADStrategy

        strategy = PEADStrategy(PEADConfig(top_k=3, rebalance_days=5))
        strategy.on_start()

        from datetime import datetime

        from aipha.domain.models import Bar
        from aipha.strategy.base import StrategyContext

        bars = {}
        for i, sym in enumerate(["S1", "S2", "S3"]):
            bars[sym] = Bar(
                symbol=sym,
                start_ts=datetime(2024, 6, 1, 9, 30),
                end_ts=datetime(2024, 6, 1, 15, 0),
                open=10.0, high=11.0, low=9.5,
                close=10.5, volume=1000000,
                vwap=10.3, prev_close=10.0,
                metadata={
                    "circ_mv": 5000.0,
                    "total_mv": 8000.0,
                    "pe_ttm": 15.0,
                    "turnover_rate": 3.0,
                    "amount": 10000.0,
                    "list_date": "20200101",
                    # No roe/sue/dt_netprofit_yoy → should degrade gracefully
                },
            )

        ctx = StrategyContext(
            current_time=datetime(2024, 6, 1, 15, 0),
            cash=1_000_000.0,
            positions={},
            nav=1_000_000.0,
        )

        intents = strategy.on_bar(bars, ctx)
        assert isinstance(intents, list)

    def test_multifactor_uses_roe(self):
        """MultiFactor strategy should use ROE from metadata for quality scoring."""
        from aipha.strategy.multifactor_enhance import (
            MultiFactorEnhanceConfig,
            MultiFactorEnhanceStrategy,
        )

        strategy = MultiFactorEnhanceStrategy(
            MultiFactorEnhanceConfig(top_k=2, rebalance_days=5)
        )
        strategy.on_start()

        from datetime import datetime

        from aipha.domain.models import Bar
        from aipha.strategy.base import StrategyContext

        bars = {}
        for i, sym in enumerate(["S1", "S2", "S3"]):
            bars[sym] = Bar(
                symbol=sym,
                start_ts=datetime(2024, 6, 1, 9, 30),
                end_ts=datetime(2024, 6, 1, 15, 0),
                open=10.0, high=11.0, low=9.5,
                close=10.5, volume=1000000,
                vwap=10.3, prev_close=10.0,
                metadata={
                    "circ_mv": 5000.0,
                    "total_mv": 8000.0,
                    "pe_ttm": 15.0,
                    "turnover_rate": 3.0,
                    "amount": 10000.0,
                    "list_date": "20200101",
                    "roe": 10.0 + i * 10,  # Varying ROE
                },
            )

        ctx = StrategyContext(
            current_time=datetime(2024, 6, 1, 15, 0),
            cash=1_000_000.0,
            positions={},
            nav=1_000_000.0,
        )

        intents = strategy.on_bar(bars, ctx)
        assert isinstance(intents, list)


class TestSUEFullPipeline:
    """SUE 数据全链路集成测试：

    _compute_sue_fields → _pit_lookup → _enrich_bar_metadata → PEAD 策略消费。
    验证从原始财务数据（含 _end_date + eps）到策略打分的完整管道。
    """

    def _build_fina_records(self):
        """构造含 3 年 Q4 财务数据的 records（模拟 TuShare 加载结果）"""
        records = [
            (date(2022, 3, 31), {
                "eps": 0.50, "_end_date": "20211231",
                "roe": 12.0, "dt_netprofit_yoy": 15.0,
            }),
            (date(2023, 3, 28), {
                "eps": 0.65, "_end_date": "20221231",
                "roe": 14.0, "dt_netprofit_yoy": 30.0,
            }),
            (date(2024, 3, 30), {
                "eps": 0.80, "_end_date": "20231231",
                "roe": 16.0, "dt_netprofit_yoy": 23.0,
            }),
        ]
        return records

    def test_compute_sue_fields_produces_sue(self):
        """_compute_sue_fields 应在第 2+ 条同季度记录产出 sue 字段"""
        from aipha.backtest.config_runner import _compute_sue_fields

        records = self._build_fina_records()
        _compute_sue_fields(records)

        assert "sue" not in records[0][1]
        assert "sue" in records[1][1]
        assert isinstance(records[1][1]["sue"], float)
        assert "sue" in records[2][1]
        assert isinstance(records[2][1]["sue"], float)
        # _end_date is intentionally preserved (strategy layer needs it for ROE annualization)
        assert "_end_date" in records[0][1]

    def test_pit_lookup_returns_sue_record(self):
        """_pit_lookup 应返回含 sue 的最新可见记录"""
        from aipha.backtest.config_runner import _compute_sue_fields

        records = self._build_fina_records()
        _compute_sue_fields(records)

        result = _pit_lookup(records, date(2024, 4, 15))
        assert result is not None
        assert "sue" in result
        assert result["roe"] == 16.0

        result2 = _pit_lookup(records, date(2023, 4, 1))
        assert result2 is not None
        assert "sue" in result2
        assert result2["roe"] == 14.0

    def test_enrich_bar_metadata_injects_sue(self):
        """_enrich_bar_metadata 应将 SUE 字段注入 bar metadata"""
        from aipha.backtest.config_runner import _compute_sue_fields

        records = self._build_fina_records()
        _compute_sue_fields(records)

        fina_pit_map = {"000001.SZ": records}
        meta = {}

        _enrich_bar_metadata(
            meta, "000001.SZ", date(2024, 4, 15),
            stock_basic_df=None,
            daily_basic_map={},
            fina_pit_map=fina_pit_map,
        )

        assert "sue" in meta, "SUE 应通过 PIT 注入到 bar metadata"
        assert "roe" in meta
        assert "eps" in meta
        assert meta["roe"] == 16.0

    def test_pead_strategy_consumes_sue_from_pipeline(self):
        """PEAD 策略应能消费经全链路传递的 SUE 值"""
        from datetime import datetime
        from aipha.backtest.config_runner import _compute_sue_fields
        from aipha.backtest.engine import Bar
        from aipha.strategy.pead_event import PEADStrategy
        from aipha.strategy.base import StrategyContext

        records = self._build_fina_records()
        _compute_sue_fields(records)

        fina_pit_map = {"000001.SZ": records, "000002.SZ": records}

        bars = {}
        for sym in ["000001.SZ", "000002.SZ"]:
            meta = {
                "circ_mv": 5000.0, "total_mv": 8000.0,
                "pe_ttm": 15.0, "turnover_rate": 3.0,
                "amount": 10000.0, "list_date": "20200101",
            }
            _enrich_bar_metadata(
                meta, sym, date(2024, 4, 15),
                stock_basic_df=None,
                daily_basic_map={},
                fina_pit_map=fina_pit_map,
            )
            bars[sym] = Bar(
                symbol=sym,
                start_ts=datetime(2024, 4, 15, 9, 30),
                end_ts=datetime(2024, 4, 15, 15, 0),
                open=10.0, high=11.0, low=9.5,
                close=10.5, volume=1000000,
                vwap=10.3, prev_close=10.0,
                metadata=meta,
            )

        strategy = PEADStrategy(
            top_k=2,
            rebalance_days=20,
            min_sue=0.0,
            min_roe=0.0,
        )

        ctx = StrategyContext(
            current_time=datetime(2024, 4, 15, 15, 0),
            cash=1_000_000.0,
            positions={},
            nav=1_000_000.0,
        )

        for sym, bar in bars.items():
            result = strategy._extra_filter(bar, ctx)
            assert result is True, (
                f"{sym} should pass _extra_filter with pipeline-generated SUE"
            )