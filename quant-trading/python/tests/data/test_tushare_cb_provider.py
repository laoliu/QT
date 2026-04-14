"""tests/data/test_tushare_cb_provider.py – TuShareCBProvider 测试

通过 mock tushare 验证：缓存、加载逻辑、数据过滤。
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.fixture()
def provider(tmp_path):
    """创建 TuShareCBProvider，mock tushare。"""
    from aipha.data.tushare_gateway import reset_tushare_gateway

    reset_tushare_gateway()
    with patch.dict(os.environ, {"TUSHARE_TOKEN": "fake_token"}):
        with patch("tushare.pro_api") as mock_pro_api, \
             patch("tushare.set_token"):
            mock_api = MagicMock()
            mock_pro_api.return_value = mock_api
            from aipha.data.tushare_cb_provider import TuShareCBProvider

            p = TuShareCBProvider(
                token="fake_token",
                cache_dir=str(tmp_path / "cb_cache"),
            )
            p._mock_api = mock_api
            yield p
    reset_tushare_gateway()


def _basic_df():
    return pd.DataFrame([
        {
            "ts_code": "127075.SZ",
            "bond_short_name": "盛路转债",
            "stk_code": "002446.SZ",
            "stk_short_name": "盛路通信",
            "list_date": "20220315",
            "delist_date": None,
            "conv_price": 8.88,
            "remain_size": 3.0,
            "maturity_date": "20280630",
            "issue_size": 5.0,
            "rating": "AA",
        },
    ])


def _daily_df():
    return pd.DataFrame([
        {
            "ts_code": "127075.SZ",
            "trade_date": "20230901",
            "open": 105.0,
            "high": 108.0,
            "low": 103.0,
            "close": 106.5,
            "vol": 50000.0,
            "amount": 53000.0,
            "cb_value": 99.5,
            "cb_over_rate": 7.03,
        },
    ])


# ── load_cb_basic ─────────────────────────────────────────


class TestLoadCbBasic:
    def test_from_api(self, provider):
        provider.api.cb_basic.return_value = _basic_df()
        result = provider.load_cb_basic(use_cache=False)
        assert len(result) == 1
        assert result.iloc[0]["ts_code"] == "127075.SZ"

    def test_cache_hit(self, provider):
        cache_path = Path(provider.cache_dir) / "cb_basic.parquet"
        _basic_df().to_parquet(cache_path, index=False)

        result = provider.load_cb_basic(use_cache=True)
        assert len(result) == 1
        provider.api.cb_basic.assert_not_called()

    def test_empty_api(self, provider):
        provider.api.cb_basic.return_value = pd.DataFrame()
        result = provider.load_cb_basic(use_cache=False)
        assert result.empty


# ── load_cb_daily ─────────────────────────────────────────


class TestLoadCbDaily:
    def test_by_ts_code(self, provider):
        provider.api.cb_daily.return_value = _daily_df()
        result = provider.load_cb_daily(ts_code="127075.SZ", use_cache=False)
        assert len(result) == 1

    def test_by_trade_date(self, provider):
        provider.api.cb_daily.return_value = _daily_df()
        result = provider.load_cb_daily(trade_date="20230901", use_cache=False)
        assert len(result) == 1

    def test_cache_hit(self, provider):
        cache_path = Path(provider.cache_dir) / "cb_daily_20230901.parquet"
        _daily_df().to_parquet(cache_path, index=False)

        result = provider.load_cb_daily(trade_date="20230901", use_cache=True)
        assert len(result) == 1
        provider.api.cb_daily.assert_not_called()

    def test_empty_returns_empty_df(self, provider):
        provider.api.cb_daily.return_value = None
        result = provider.load_cb_daily(ts_code="999999.SZ", use_cache=False)
        assert result.empty

    def test_filters_invalid_close(self, provider):
        df = _daily_df().copy()
        df2 = df.copy()
        df2["close"] = 0.0
        combined = pd.concat([df, df2], ignore_index=True)
        provider.api.cb_daily.return_value = combined
        result = provider.load_cb_daily(ts_code="127075.SZ", use_cache=False)
        assert len(result) == 1  # close=0 被过滤


# ── load_cb_history_with_premium ──────────────────────────


class TestLoadCbHistory:
    def test_from_api(self, provider):
        provider.api.cb_daily.return_value = _daily_df()
        result = provider.load_cb_history_with_premium(
            "127075.SZ", "20230901", "20230901", use_cache=False
        )
        assert len(result) == 1

    def test_cache_hit(self, provider):
        cache_dir = Path(provider.cache_dir) / "history"
        cache_dir.mkdir(parents=True)
        cache_path = cache_dir / "127075_SZ_20230901_20230901.parquet"
        _daily_df().to_parquet(cache_path, index=False)

        result = provider.load_cb_history_with_premium(
            "127075.SZ", "20230901", "20230901", use_cache=True
        )
        assert len(result) == 1
        provider.api.cb_daily.assert_not_called()

    def test_empty_returns_empty(self, provider):
        provider.api.cb_daily.return_value = None
        result = provider.load_cb_history_with_premium(
            "999999.SZ", "20230901", "20230901", use_cache=False
        )
        assert result.empty


# ── _supplement_rating ────────────────────────────────────


class TestSupplementRating:
    """测试评级数据补充功能。"""

    def test_no_missing_rating_noop(self, provider):
        """所有评级已存在时不做任何补充。"""
        df = _basic_df()  # rating="AA"
        result = provider._supplement_rating(df)
        assert result.iloc[0]["rating"] == "AA"

    def test_add_rating_column_if_missing(self, provider):
        """DataFrame 无 rating 列时自动添加。"""
        df = pd.DataFrame([{"ts_code": "127075.SZ", "bond_short_name": "盛路转债"}])
        result = provider._supplement_rating(df)
        assert "rating" in result.columns

    def test_local_map_fills_missing(self, provider):
        """本地映射文件可以补充缺失评级。"""
        # 创建测试数据：rating 为空
        df = pd.DataFrame([
            {"ts_code": "127075.SZ", "bond_short_name": "盛路转债", "rating": ""},
            {"ts_code": "128001.SZ", "bond_short_name": "某某转债", "rating": ""},
        ])

        # 创建本地评级映射
        map_df = pd.DataFrame([
            {"ts_code": "127075.SZ", "rating": "AA"},
            {"ts_code": "128001.SZ", "rating": "A+"},
        ])
        map_path = Path(provider.cache_dir) / "cb_rating_map.parquet"
        map_df.to_parquet(map_path, index=False)

        result = provider._supplement_rating(df)
        assert result.iloc[0]["rating"] == "AA"
        assert result.iloc[1]["rating"] == "A+"

    def test_local_map_does_not_overwrite_existing(self, provider):
        """已有评级不被本地映射覆盖。"""
        df = pd.DataFrame([
            {"ts_code": "127075.SZ", "bond_short_name": "盛路转债", "rating": "AAA"},
        ])

        map_df = pd.DataFrame([
            {"ts_code": "127075.SZ", "rating": "AA"},  # 映射中不同
        ])
        map_path = Path(provider.cache_dir) / "cb_rating_map.parquet"
        map_df.to_parquet(map_path, index=False)

        result = provider._supplement_rating(df)
        assert result.iloc[0]["rating"] == "AAA"  # 保持原值

    def test_nan_treated_as_missing(self, provider):
        """NaN 应被视为缺失。"""
        df = pd.DataFrame([
            {"ts_code": "127075.SZ", "bond_short_name": "盛路转债", "rating": None},
        ])

        map_df = pd.DataFrame([{"ts_code": "127075.SZ", "rating": "AA-"}])
        map_path = Path(provider.cache_dir) / "cb_rating_map.parquet"
        map_df.to_parquet(map_path, index=False)

        result = provider._supplement_rating(df)
        assert result.iloc[0]["rating"] == "AA-"

    def test_empty_df_returns_empty(self, provider):
        """空 DataFrame 返回空。"""
        df = pd.DataFrame()
        result = provider._supplement_rating(df)
        assert result.empty

    def test_akshare_fallback(self, provider):
        """AKShare 集思录补充（当本地映射不存在时）。"""
        df = pd.DataFrame([
            {"ts_code": "113050.SH", "bond_short_name": "某某转债", "rating": ""},
        ])

        # Mock AKShare
        mock_jsl_df = pd.DataFrame([
            {"代码": "113050", "转债名称": "某某转债", "债券评级": "AA+"},
        ])

        with patch("akshare.bond_cb_jsl", return_value=mock_jsl_df):
            result = provider._supplement_rating(df)
            assert result.iloc[0]["rating"] == "AA+"

    def test_akshare_saves_rating_map(self, provider):
        """AKShare 补充成功后应保存评级映射到本地。"""
        df = pd.DataFrame([
            {"ts_code": "113050.SH", "bond_short_name": "某某转债", "rating": ""},
        ])

        mock_jsl_df = pd.DataFrame([
            {"代码": "113050", "转债名称": "某某转债", "债券评级": "AA+"},
        ])

        map_path = Path(provider.cache_dir) / "cb_rating_map.parquet"
        assert not map_path.exists()

        with patch("akshare.bond_cb_jsl", return_value=mock_jsl_df):
            provider._supplement_rating(df)

        assert map_path.exists()
        saved = pd.read_parquet(map_path)
        assert len(saved) == 1
        assert saved.iloc[0]["rating"] == "AA+"

    def test_akshare_import_error_graceful(self, provider):
        """AKShare 未安装时优雅降级。"""
        df = pd.DataFrame([
            {"ts_code": "113050.SH", "bond_short_name": "某某转债", "rating": ""},
        ])

        with patch.dict("sys.modules", {"akshare": None}):
            # 不应抛异常
            result = provider._supplement_rating(df)
            # rating 仍为空（无数据源可补充）
            assert result.iloc[0]["rating"] == ""
