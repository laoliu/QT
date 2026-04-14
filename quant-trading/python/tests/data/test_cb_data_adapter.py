"""CBDataAdapter 单位转换测试"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from aipha.data.cb_data_adapter import CBDataAdapter


class TestCBDataAdapterUnitConversion:
    """验证 remain_size (元→万元) 和 vol (手→张) 的单位转换"""

    @patch.object(CBDataAdapter, "__init__", lambda self, **kwargs: None)
    def _make_adapter(self) -> CBDataAdapter:
        """构造不连接 TuShare 的 adapter"""
        adapter = CBDataAdapter.__new__(CBDataAdapter)
        adapter.provider = MagicMock()
        return adapter

    def test_remain_size_converted_from_yuan_to_wan(self) -> None:
        """remain_size 应从元转换为万元 (÷10000)"""
        adapter = self._make_adapter()

        # 模拟 TuShare 返回值（单位: 元）
        basic = pd.DataFrame({
            "ts_code": ["128085.SZ"],
            "bond_short_name": ["鸿路转债"],
            "stk_short_name": ["鸿路钢构"],
            "remain_size": [500_000_000.0],  # 5 亿元
            "delist_date": [None],
            "maturity_date": ["20270101"],
        })

        daily = pd.DataFrame({
            "ts_code": ["128085.SZ"],
            "trade_date": ["20240102"],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "vol": [50000.0],  # 5 万手
            "amount": [5100.0],  # 万元
            "cb_value": [95.0],
            "cb_over_rate": [6.3],
        })

        adapter.provider.load_cb_basic.return_value = basic
        adapter.provider.load_cb_daily.return_value = daily

        bars_by_date = adapter.load("20240102", "20240102")
        assert len(bars_by_date) == 1

        bars = list(bars_by_date.values())[0]
        bar = bars["128085.SZ"]

        # remain_size: 500_000_000 / 10000 = 50000 万元
        assert bar.metadata["remain_size"] == pytest.approx(50000.0)

    def test_vol_converted_from_shou_to_zhang(self) -> None:
        """vol 应从手转换为张 (×10)"""
        adapter = self._make_adapter()

        basic = pd.DataFrame({
            "ts_code": ["128085.SZ"],
            "bond_short_name": ["鸿路转债"],
            "stk_short_name": ["鸿路钢构"],
            "remain_size": [500_000_000.0],
            "delist_date": [None],
            "maturity_date": ["20270101"],
        })

        daily = pd.DataFrame({
            "ts_code": ["128085.SZ"],
            "trade_date": ["20240102"],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "vol": [12345.0],  # 12345 手
            "amount": [1250.0],
            "cb_value": [95.0],
            "cb_over_rate": [6.3],
        })

        adapter.provider.load_cb_basic.return_value = basic
        adapter.provider.load_cb_daily.return_value = daily

        bars_by_date = adapter.load("20240102", "20240102")
        bars = list(bars_by_date.values())[0]
        bar = bars["128085.SZ"]

        # vol: 12345 手 × 10 = 123450 张
        assert bar.volume == pytest.approx(123450.0)

    def test_amount_unchanged_in_wanyuan(self) -> None:
        """amount 单位保持万元不变"""
        adapter = self._make_adapter()

        basic = pd.DataFrame({
            "ts_code": ["128085.SZ"],
            "bond_short_name": ["鸿路转债"],
            "stk_short_name": ["鸿路钢构"],
            "remain_size": [500_000_000.0],
            "delist_date": [None],
            "maturity_date": ["20270101"],
        })

        daily = pd.DataFrame({
            "ts_code": ["128085.SZ"],
            "trade_date": ["20240102"],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "vol": [50000.0],
            "amount": [5100.5],  # 万元
            "cb_value": [95.0],
            "cb_over_rate": [6.3],
        })

        adapter.provider.load_cb_basic.return_value = basic
        adapter.provider.load_cb_daily.return_value = daily

        bars_by_date = adapter.load("20240102", "20240102")
        bars = list(bars_by_date.values())[0]
        bar = bars["128085.SZ"]

        # amount 不转换
        assert bar.metadata["amount"] == pytest.approx(5100.5)

    def test_cb_over_rate_unchanged_percent(self) -> None:
        """cb_over_rate 单位保持百分比不变"""
        adapter = self._make_adapter()

        basic = pd.DataFrame({
            "ts_code": ["128085.SZ"],
            "bond_short_name": ["鸿路转债"],
            "stk_short_name": ["鸿路钢构"],
            "remain_size": [500_000_000.0],
            "delist_date": [None],
            "maturity_date": ["20270101"],
        })

        daily = pd.DataFrame({
            "ts_code": ["128085.SZ"],
            "trade_date": ["20240102"],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "vol": [50000.0],
            "amount": [5100.0],
            "cb_value": [95.0],
            "cb_over_rate": [6.3158],  # 6.3158%
        })

        adapter.provider.load_cb_basic.return_value = basic
        adapter.provider.load_cb_daily.return_value = daily

        bars_by_date = adapter.load("20240102", "20240102")
        bars = list(bars_by_date.values())[0]
        bar = bars["128085.SZ"]

        assert bar.metadata["cb_over_rate"] == pytest.approx(6.3158)

    def test_remain_size_zero_stays_zero(self) -> None:
        """已摘牌的转债 remain_size=0 转换后仍为 0"""
        adapter = self._make_adapter()

        basic = pd.DataFrame({
            "ts_code": ["125002.SZ"],
            "bond_short_name": ["万科转债"],
            "stk_short_name": ["万科A"],
            "remain_size": [0.0],
            "delist_date": ["20200101"],
            "maturity_date": ["20200101"],
        })

        daily = pd.DataFrame({
            "ts_code": ["125002.SZ"],
            "trade_date": ["20190102"],
            "open": [110.0],
            "high": [112.0],
            "low": [109.0],
            "close": [111.0],
            "vol": [1000.0],
            "amount": [112.0],
            "cb_value": [105.0],
            "cb_over_rate": [5.7],
        })

        adapter.provider.load_cb_basic.return_value = basic
        adapter.provider.load_cb_daily.return_value = daily

        bars_by_date = adapter.load("20190102", "20190102")
        bars = list(bars_by_date.values())[0]
        bar = bars["125002.SZ"]

        assert bar.metadata["remain_size"] == 0.0


class TestQualityCheckAndClean:
    """测试 CBDataAdapter._quality_check_and_clean"""

    def _make_adapter(self):
        with patch("aipha.data.cb_data_adapter.TuShareCBProvider"):
            return CBDataAdapter(cache_dir="dummy")

    def test_dedup_removes_duplicates(self):
        """重复行应被去重"""
        adapter = self._make_adapter()
        df = pd.DataFrame({
            "trade_date": ["20240101", "20240101", "20240102"],
            "ts_code": ["A", "A", "B"],
            "close": [100, 101, 102],
            "vol": [1000, 2000, 3000],
        })
        result = adapter._quality_check_and_clean(df)
        # A on 20240101 should keep last (101)
        assert len(result) == 2
        a_row = result[result["ts_code"] == "A"]
        assert float(a_row["close"].iloc[0]) == 101.0

    def test_sort_by_trade_date(self):
        """DataFrame 应按 trade_date 排序"""
        adapter = self._make_adapter()
        df = pd.DataFrame({
            "trade_date": ["20240103", "20240101", "20240102"],
            "ts_code": ["A", "B", "C"],
            "close": [103, 101, 102],
        })
        result = adapter._quality_check_and_clean(df)
        dates = result["trade_date"].tolist()
        assert dates == ["20240101", "20240102", "20240103"]

    def test_missing_fields_logged(self, caplog):
        """缺失字段应被记录"""
        import logging
        adapter = self._make_adapter()
        df = pd.DataFrame({
            "trade_date": ["20240101"],
            "ts_code": ["A"],
            "close": [100.0],
            "cb_value": [float("nan")],
        })
        with caplog.at_level(logging.INFO):
            adapter._quality_check_and_clean(df)
        assert "字段缺失统计" in caplog.text

    def test_remain_size_pit_warning(self, caplog):
        """remain_size 存在时应发出 PIT 偏差提示（debug 级别，降噪）"""
        import logging
        adapter = self._make_adapter()
        df = pd.DataFrame({
            "trade_date": ["20240101"],
            "ts_code": ["A"],
            "close": [100.0],
            "remain_size": [1000.0],
        })
        with caplog.at_level(logging.DEBUG):
            adapter._quality_check_and_clean(df)
        assert "前视偏差" in caplog.text
