"""测试 _cb_data_utils.fetch_cb_daily_enriched 的列保障逻辑."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

# 将 scripts/ops 加入 sys.path
_OPS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "ops"
sys.path.insert(0, str(_OPS_DIR))

from _cb_data_utils import fetch_cb_daily_enriched


class _FakeGW:
    """模拟 TuShareGateway, 可配置返回内容."""

    def __init__(self, cb_daily_df=None, cb_basic_df=None, daily_df=None):
        self._cb_daily = cb_daily_df
        self._cb_basic = cb_basic_df
        self._daily = daily_df

    def call(self, api_name, **kwargs):
        if api_name == "cb_daily":
            return self._cb_daily
        if api_name == "cb_basic":
            return self._cb_basic
        if api_name == "daily":
            return self._daily
        return pd.DataFrame()


def test_cb_over_rate_column_missing_from_api():
    """API 完全不返回 cb_over_rate 列时, 函数应不报错且补算."""
    # 模拟 API 只返回 ts_code, close, vol, amount (无 cb_value/cb_over_rate)
    cb_daily = pd.DataFrame({
        "ts_code": ["113001.SH", "113002.SH"],
        "trade_date": ["20260320", "20260320"],
        "close": [105.0, 120.0],
        "vol": [100000, 200000],
        "amount": [1e7, 2e7],
    })
    cb_basic = pd.DataFrame({
        "ts_code": ["113001.SH", "113002.SH"],
        "stk_code": ["600000.SH", "600001.SH"],
        "conv_price": [10.0, 15.0],
        "maturity_date": ["20301231", "20301231"],
        "list_date": ["20200101", "20200101"],
    })
    daily = pd.DataFrame({
        "ts_code": ["600000.SH", "600001.SH"],
        "trade_date": ["20260320", "20260320"],
        "close": [12.0, 18.0],
        "open": [11.5, 17.5],
    })

    import _cb_data_utils
    _cb_data_utils._CB_BASIC_CACHE = None  # 清缓存

    gw = _FakeGW(cb_daily_df=cb_daily, cb_basic_df=cb_basic, daily_df=daily)
    result = fetch_cb_daily_enriched(gw, "20260320")

    assert not result.empty
    assert "cb_over_rate" in result.columns
    # 补算应成功: cb_value = stock_close / conv_price * 100
    # 113001: 12/10*100=120, cb_over_rate = (105/120-1)*100 = -12.5
    assert result["cb_over_rate"].notna().sum() == 2


def test_cb_over_rate_column_present_all_nan():
    """API 返回 cb_over_rate 列但全 NaN, 应触发手工补算."""
    cb_daily = pd.DataFrame({
        "ts_code": ["113001.SH"],
        "trade_date": ["20260320"],
        "close": [110.0],
        "vol": [100000],
        "amount": [1e7],
        "cb_value": [np.nan],
        "cb_over_rate": [np.nan],
    })
    cb_basic = pd.DataFrame({
        "ts_code": ["113001.SH"],
        "stk_code": ["600000.SH"],
        "conv_price": [10.0],
        "maturity_date": ["20301231"],
        "list_date": ["20200101"],
    })
    daily = pd.DataFrame({
        "ts_code": ["600000.SH"],
        "trade_date": ["20260320"],
        "close": [11.0],
        "open": [10.5],
    })

    import _cb_data_utils
    _cb_data_utils._CB_BASIC_CACHE = None

    gw = _FakeGW(cb_daily_df=cb_daily, cb_basic_df=cb_basic, daily_df=daily)
    result = fetch_cb_daily_enriched(gw, "20260320")

    assert "cb_over_rate" in result.columns
    assert result["cb_over_rate"].notna().sum() == 1


def test_cb_over_rate_normal():
    """正常情况: API 返回完整 cb_over_rate, 直接使用."""
    cb_daily = pd.DataFrame({
        "ts_code": ["113001.SH", "113002.SH"],
        "trade_date": ["20260320", "20260320"],
        "close": [105.0, 120.0],
        "vol": [100000, 200000],
        "amount": [1e7, 2e7],
        "cb_value": [115.0, 130.0],
        "cb_over_rate": [-8.7, -7.7],
    })

    gw = _FakeGW(cb_daily_df=cb_daily)
    result = fetch_cb_daily_enriched(gw, "20260320")

    assert "cb_over_rate" in result.columns
    assert result["cb_over_rate"].notna().sum() == 2
    assert abs(result.iloc[0]["cb_over_rate"] - (-8.7)) < 0.01


def test_empty_api_response():
    """API 返回空, 返回空 DataFrame 不报错."""
    gw = _FakeGW(cb_daily_df=pd.DataFrame())
    result = fetch_cb_daily_enriched(gw, "20260320")
    assert result.empty
