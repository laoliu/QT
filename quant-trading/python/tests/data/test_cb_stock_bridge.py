"""Tests for CBStockBridge — CB→正股数据桥接层。

测试覆盖（≥10 用例）：
- 静态方法：compute_ep, compute_volatility, compute_momentum
- 数据结构：StockFundamentals, StockDailyMetrics
- 核心查询：get_fundamentals, get_daily_metrics, get_fundamentals_batch
- 缓存行为：内存缓存命中
- 边界条件：空数据、异常值、None 处理
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from aipha.data.cb_stock_bridge import (
    CBStockBridge,
    StockDailyMetrics,
    StockFundamentals,
    _safe_float,
)


# ---------------------------------------------------------------------------
# Fixtures — 使用注入方式避免真实 API 调用
# ---------------------------------------------------------------------------

class FakePITProvider:
    """模拟 TuSharePITProvider，不触发任何网络请求。"""

    def __init__(self) -> None:
        # {stk_code: {indicator_name: Decimal}}
        self._data: dict[str, dict[str, Decimal]] = {}

    def set_data(self, stk_code: str, indicators: dict[str, float]) -> None:
        self._data[stk_code] = {k: Decimal(str(v)) for k, v in indicators.items()}

    def get_indicator_pit(
        self, symbol: str, query_date: date, indicator_names: list[str],
    ) -> Any:
        if symbol not in self._data:
            return None
        from dataclasses import dataclass, field as dc_field

        @dataclass
        class _FakeIndicator:
            symbol: str = ""
            report_date: Any = None
            announce_date: date = date(2024, 1, 1)
            indicators: dict[str, Decimal] = dc_field(default_factory=dict)

        filtered = {
            k: v for k, v in self._data[symbol].items() if k in indicator_names
        }
        return _FakeIndicator(
            symbol=symbol, indicators=filtered, announce_date=query_date,
        )

    def preload_batch(self, symbols: list[str], delay: float = 0.2) -> None:
        pass  # no-op


class FakeEquityProvider:
    """模拟 TuShareEquityProvider，返回预设的 daily_basic DataFrame。"""

    def __init__(self) -> None:
        import pandas as pd
        self._daily_basic_data: dict[str, list[dict]] = {}

    def add_daily_basic(
        self, trade_date: str, ts_code: str, **fields: float,
    ) -> None:
        if trade_date not in self._daily_basic_data:
            self._daily_basic_data[trade_date] = []
        row = {"ts_code": ts_code, "trade_date": trade_date, **fields}
        self._daily_basic_data[trade_date].append(row)

    def load_daily_basic(self, trade_date: str, use_cache: bool = True) -> Any:
        import pandas as pd
        rows = self._daily_basic_data.get(trade_date, [])
        return pd.DataFrame(rows)


@pytest.fixture()
def fake_pit() -> FakePITProvider:
    return FakePITProvider()


@pytest.fixture()
def fake_equity() -> FakeEquityProvider:
    return FakeEquityProvider()


@pytest.fixture()
def bridge(fake_pit: FakePITProvider, fake_equity: FakeEquityProvider) -> CBStockBridge:
    """构造注入 fake providers 的 CBStockBridge。"""
    return CBStockBridge(
        cache_dir="data/cache",
        pit_provider=fake_pit,
        equity_provider=fake_equity,
    )


# ---------------------------------------------------------------------------
# 1. _safe_float 工具函数
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_none(self) -> None:
        assert _safe_float(None) is None

    def test_nan(self) -> None:
        assert _safe_float(float("nan")) is None

    def test_inf(self) -> None:
        assert _safe_float(float("inf")) is None

    def test_normal(self) -> None:
        assert _safe_float(3.14) == pytest.approx(3.14)

    def test_decimal(self) -> None:
        assert _safe_float(Decimal("12.5")) == pytest.approx(12.5)

    def test_string_number(self) -> None:
        assert _safe_float("2.71") == pytest.approx(2.71)

    def test_invalid_string(self) -> None:
        assert _safe_float("abc") is None


# ---------------------------------------------------------------------------
# 2. StockFundamentals / StockDailyMetrics 数据结构
# ---------------------------------------------------------------------------

class TestDataStructures:
    def test_fundamentals_defaults(self) -> None:
        f = StockFundamentals(stk_code="000001.SZ", query_date=date(2024, 1, 1))
        assert f.roe is None
        assert f.ep is None
        assert f.pe_ttm is None
        assert f.pb is None

    def test_daily_metrics_defaults(self) -> None:
        m = StockDailyMetrics(stk_code="000001.SZ", query_date=date(2024, 1, 1))
        assert m.vol_20d is None
        assert m.mom_20d is None


# ---------------------------------------------------------------------------
# 3. 静态方法：compute_ep
# ---------------------------------------------------------------------------

class TestComputeEP:
    def test_positive_pe(self) -> None:
        assert CBStockBridge.compute_ep(20.0) == pytest.approx(0.05)

    def test_pe_none(self) -> None:
        assert CBStockBridge.compute_ep(None) is None

    def test_pe_zero(self) -> None:
        assert CBStockBridge.compute_ep(0.0) is None

    def test_pe_negative(self) -> None:
        assert CBStockBridge.compute_ep(-5.0) is None


# ---------------------------------------------------------------------------
# 4. 静态方法：compute_volatility
# ---------------------------------------------------------------------------

class TestComputeVolatility:
    def test_insufficient_data(self) -> None:
        assert CBStockBridge.compute_volatility([0.01, 0.02]) is None

    def test_constant_returns(self) -> None:
        # 每日都是 1%，方差为 0（ddof=1，全相同）
        rets = [0.01] * 10
        result = CBStockBridge.compute_volatility(rets)
        # 方差=0 -> None
        assert result is None or result == pytest.approx(0.0, abs=1e-10)

    def test_normal_returns(self) -> None:
        rets = [0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, 0.02, -0.01, 0.03]
        result = CBStockBridge.compute_volatility(rets)
        assert result is not None
        assert result > 0  # 正值
        # 年化后应该在合理范围 (~30% 左右)
        assert result < 2.0


# ---------------------------------------------------------------------------
# 5. 静态方法：compute_momentum
# ---------------------------------------------------------------------------

class TestComputeMomentum:
    def test_positive_momentum(self) -> None:
        prices = [100.0, 105.0, 110.0]
        assert CBStockBridge.compute_momentum(prices) == pytest.approx(0.10)

    def test_negative_momentum(self) -> None:
        prices = [100.0, 95.0, 90.0]
        assert CBStockBridge.compute_momentum(prices) == pytest.approx(-0.10)

    def test_single_price(self) -> None:
        assert CBStockBridge.compute_momentum([100.0]) is None

    def test_zero_start_price(self) -> None:
        assert CBStockBridge.compute_momentum([0.0, 100.0]) is None


# ---------------------------------------------------------------------------
# 6. get_fundamentals
# ---------------------------------------------------------------------------

class TestGetFundamentals:
    def test_full_data(
        self, bridge: CBStockBridge,
        fake_pit: FakePITProvider,
        fake_equity: FakeEquityProvider,
    ) -> None:
        fake_pit.set_data("000001.SZ", {"roe": 15.0})
        fake_equity.add_daily_basic(
            "20240115", "000001.SZ",
            pe_ttm=12.0, pb=1.5, total_mv=50000.0, turnover_rate=2.3,
        )
        result = bridge.get_fundamentals("000001.SZ", date(2024, 1, 15))

        assert result.stk_code == "000001.SZ"
        assert result.roe == pytest.approx(15.0)
        assert result.pe_ttm == pytest.approx(12.0)
        assert result.ep == pytest.approx(1.0 / 12.0)
        assert result.pb == pytest.approx(1.5)
        assert result.total_mv == pytest.approx(50000.0)

    def test_no_pit_data(
        self, bridge: CBStockBridge,
        fake_equity: FakeEquityProvider,
    ) -> None:
        fake_equity.add_daily_basic(
            "20240115", "600519.SH",
            pe_ttm=30.0, pb=8.0,
        )
        result = bridge.get_fundamentals("600519.SH", date(2024, 1, 15))
        assert result.roe is None
        assert result.pe_ttm == pytest.approx(30.0)
        assert result.ep == pytest.approx(1.0 / 30.0)

    def test_no_daily_basic(
        self, bridge: CBStockBridge,
        fake_pit: FakePITProvider,
    ) -> None:
        fake_pit.set_data("000002.SZ", {"roe": 10.0})
        result = bridge.get_fundamentals("000002.SZ", date(2024, 1, 15))
        assert result.roe == pytest.approx(10.0)
        assert result.pe_ttm is None
        assert result.ep is None

    def test_cache_hit(
        self, bridge: CBStockBridge,
        fake_pit: FakePITProvider,
    ) -> None:
        """第二次调用应命中缓存。"""
        fake_pit.set_data("000001.SZ", {"roe": 15.0})
        r1 = bridge.get_fundamentals("000001.SZ", date(2024, 1, 15))
        r2 = bridge.get_fundamentals("000001.SZ", date(2024, 1, 15))
        assert r1 is r2  # 同一对象引用


# ---------------------------------------------------------------------------
# 7. get_daily_metrics
# ---------------------------------------------------------------------------

class TestGetDailyMetrics:
    def test_no_history(self, bridge: CBStockBridge) -> None:
        result = bridge.get_daily_metrics("000001.SZ", date(2024, 1, 15))
        assert result.vol_20d is None
        assert result.mom_20d is None

    def test_short_history(self, bridge: CBStockBridge) -> None:
        result = bridge.get_daily_metrics(
            "000001.SZ", date(2024, 1, 15), close_history=[100.0, 101.0],
        )
        assert result.vol_20d is None

    def test_sufficient_20d_history(self, bridge: CBStockBridge) -> None:
        # 21 个价格 → 20 个收益率 → 可计算 20d vol 和 mom
        prices = [100.0 + i * 0.5 for i in range(22)]
        result = bridge.get_daily_metrics(
            "000001.SZ", date(2024, 1, 15), close_history=prices,
        )
        assert result.vol_20d is not None
        assert result.vol_20d > 0
        assert result.mom_20d is not None

    def test_full_60d_history(self, bridge: CBStockBridge) -> None:
        # 62 个价格 → 足够 60d vol 和 mom
        prices = [100.0 + i * 0.3 for i in range(65)]
        result = bridge.get_daily_metrics(
            "000001.SZ", date(2024, 1, 15), close_history=prices,
        )
        assert result.vol_60d is not None
        assert result.mom_60d is not None


# ---------------------------------------------------------------------------
# 8. get_fundamentals_batch
# ---------------------------------------------------------------------------

class TestGetFundamentalsBatch:
    def test_batch_multiple(
        self, bridge: CBStockBridge,
        fake_pit: FakePITProvider,
    ) -> None:
        fake_pit.set_data("000001.SZ", {"roe": 15.0})
        fake_pit.set_data("600519.SH", {"roe": 25.0})
        qd = date(2024, 1, 15)
        result = bridge.get_fundamentals_batch([
            ("000001.SZ", qd), ("600519.SH", qd),
        ])
        assert len(result) == 2
        assert result["000001.SZ"].roe == pytest.approx(15.0)
        assert result["600519.SH"].roe == pytest.approx(25.0)

    def test_batch_empty(self, bridge: CBStockBridge) -> None:
        result = bridge.get_fundamentals_batch([])
        assert result == {}


# ---------------------------------------------------------------------------
# 9. preload_pit
# ---------------------------------------------------------------------------

class TestPreloadPIT:
    def test_preload_calls_provider(
        self, bridge: CBStockBridge,
        fake_pit: FakePITProvider,
    ) -> None:
        # preload_batch is a no-op in FakePITProvider, ensure no crash
        bridge.preload_pit(["000001.SZ", "600519.SH"])

    def test_preload_empty(self, bridge: CBStockBridge) -> None:
        bridge.preload_pit([])  # should not crash


# ---------------------------------------------------------------------------
# 10. _lookup_daily_basic (internal but critical)
# ---------------------------------------------------------------------------

class TestLookupDailyBasic:
    def test_found(
        self, bridge: CBStockBridge,
        fake_equity: FakeEquityProvider,
    ) -> None:
        fake_equity.add_daily_basic("20240115", "000001.SZ", pe_ttm=12.0)
        row = bridge._lookup_daily_basic("000001.SZ", "20240115")
        assert row is not None
        assert row["pe_ttm"] == 12.0

    def test_not_found(self, bridge: CBStockBridge) -> None:
        row = bridge._lookup_daily_basic("UNKNOWN.SZ", "20240115")
        assert row is None
