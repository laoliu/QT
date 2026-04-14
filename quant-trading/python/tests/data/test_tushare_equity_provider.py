"""tests/data/test_tushare_equity_provider.py – TuShareEquityProvider 测试

通过 mock tushare API 验证：重试、缓存、数据加载逻辑。
不依赖真实 API Token。
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# 需要在 import 前 mock 掉 tushare 模块级调用


@pytest.fixture()
def provider(tmp_path):
    """创建 TuShareEquityProvider，mock 掉 tushare 依赖。"""
    from aipha.data.tushare_gateway import reset_tushare_gateway

    reset_tushare_gateway()
    with patch.dict(os.environ, {"TUSHARE_TOKEN": "fake_token_123"}):
        with patch("tushare.pro_api") as mock_pro_api, \
             patch("tushare.set_token"):
            mock_api = MagicMock()
            mock_pro_api.return_value = mock_api
            from aipha.data.tushare_equity_provider import TuShareEquityProvider

            p = TuShareEquityProvider(
                token="fake_token_123",
                cache_dir=str(tmp_path / "cache"),
                max_retries=3,
            )
            p._mock_api = mock_api  # 暴露用于断言
            yield p
    reset_tushare_gateway()


# ── _retry_call ───────────────────────────────────────────


class TestRetryCall:
    def test_success_first_try(self, provider):
        fn = MagicMock(return_value=pd.DataFrame({"a": [1]}))
        result = provider._retry_call(fn, x=1)
        assert len(result) == 1
        fn.assert_called_once_with(x=1)

    def test_retry_on_failure(self, provider):
        fn = MagicMock(side_effect=[RuntimeError("err"), pd.DataFrame({"a": [1]})])
        with patch("aipha.data.tushare_equity_provider.time.sleep"):
            result = provider._retry_call(fn, x=1)
        assert len(result) == 1
        assert fn.call_count == 2

    def test_exhausted_retries_raises(self, provider):
        fn = MagicMock(side_effect=RuntimeError("persistent"))
        with patch("aipha.data.tushare_equity_provider.time.sleep"):
            with pytest.raises(RuntimeError, match="persistent"):
                provider._retry_call(fn, x=1)
        assert fn.call_count == 3

    def test_none_result_returns_empty_df(self, provider):
        fn = MagicMock(return_value=None)
        result = provider._retry_call(fn)
        assert isinstance(result, pd.DataFrame)
        assert result.empty


# ── load_stock_basic ──────────────────────────────────────


class TestLoadStockBasic:
    def test_no_cache(self, provider):
        df_l = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安"], "industry": ["银行"]})
        df_d = pd.DataFrame({"ts_code": ["000999.SZ"], "name": ["退市"], "industry": ["其它"]})
        df_p = pd.DataFrame()

        provider.api.stock_basic.side_effect = [df_l, df_d, df_p]
        result = provider.load_stock_basic(use_cache=False)
        assert len(result) == 2  # L + D merged
        assert set(result["ts_code"]) == {"000001.SZ", "000999.SZ"}

    def test_cache_hit(self, provider, tmp_path):
        cache_path = Path(provider.cache_dir) / "stock_basic.parquet"
        df = pd.DataFrame({"ts_code": ["600036.SH"], "name": ["招行"]})
        df.to_parquet(cache_path, index=False)

        result = provider.load_stock_basic(use_cache=True)
        assert len(result) == 1
        provider.api.stock_basic.assert_not_called()


# ── load_daily_basic ─────────────────────────────────────


class TestLoadDailyBasic:
    def test_download(self, provider):
        df = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "trade_date": ["20230901"],
            "close": [10.5],
            "pe_ttm": [8.0],
        })
        provider.api.daily_basic.return_value = df
        result = provider.load_daily_basic("20230901", use_cache=False)
        assert len(result) == 1

    def test_cache_hit(self, provider):
        cache_path = Path(provider.cache_dir) / "daily_basic_202309.parquet"
        df = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "trade_date": ["20230901"],
            "close": [10.5],
        })
        df.to_parquet(cache_path, index=False)

        result = provider.load_daily_basic("20230901", use_cache=True)
        assert len(result) == 1
        provider.api.daily_basic.assert_not_called()

    def test_empty_api_response(self, provider):
        provider.api.daily_basic.return_value = pd.DataFrame()
        result = provider.load_daily_basic("20230901", use_cache=False)
        assert result.empty


# ── load_daily_bars ──────────────────────────────────────


class TestLoadDailyBars:
    def test_download(self, provider):
        df = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "trade_date": ["20230901"],
            "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
            "vol": [1000.0], "amount": [10000.0],
            "pre_close": [10.0], "change": [0.5], "pct_chg": [5.0],
        })
        provider.api.daily.return_value = df
        result = provider.load_daily_bars("20230901", use_cache=False)
        assert len(result) == 1

    def test_none_api_response(self, provider):
        provider.api.daily.return_value = None
        result = provider.load_daily_bars("20230901", use_cache=False)
        assert result.empty


# ── load_adj_factor ──────────────────────────────────────


class TestLoadAdjFactor:
    def test_download(self, provider):
        df = pd.DataFrame({
            "ts_code": ["000001.SZ", "000001.SZ"],
            "trade_date": ["20230901", "20230904"],
            "adj_factor": [120.5, 120.8],
        })
        provider.api.adj_factor.return_value = df
        result = provider.load_adj_factor("000001.SZ", start_date="20230901", end_date="20230904")
        assert len(result) == 2

    def test_empty_download(self, provider):
        provider.api.adj_factor.return_value = None
        result = provider.load_adj_factor("000001.SZ")
        assert result.empty


# ── load_industry_mapping ────────────────────────────────


class TestLoadIndustryMapping:
    def test_builds_dict(self, provider):
        df = pd.DataFrame({
            "ts_code": ["000001.SZ", "600036.SH"],
            "name": ["平安", "招行"],
            "industry": ["银行", "银行"],
        })
        provider.api.stock_basic.side_effect = [df, pd.DataFrame(), pd.DataFrame()]
        mapping = provider.load_industry_mapping(use_cache=False)
        assert mapping["000001.SZ"] == "银行"
        assert mapping["600036.SH"] == "银行"


# ── load_index_members ───────────────────────────────────


class TestLoadIndexMembers:
    def test_download(self, provider):
        df = pd.DataFrame({
            "index_code": ["000300.SH", "000300.SH"],
            "con_code": ["000001.SZ", "000002.SZ"],
        })
        provider.api.index_weight.return_value = df
        result = provider.load_index_members("000300.SH", use_cache=False)
        assert len(result) == 2

    def test_cache_hit_latest(self, provider):
        """trade_date=None 时缓存文件名带 _latest 后缀。"""
        cache_path = Path(provider.cache_dir) / "index_members_000300_SH_latest.parquet"
        df = pd.DataFrame({"con_code": ["000001.SZ"]})
        df.to_parquet(cache_path, index=False)

        result = provider.load_index_members("000300.SH", use_cache=True)
        assert len(result) == 1

    def test_cache_by_trade_date(self, provider):
        """传入 trade_date 时缓存 key 包含日期，防止不同日期缓存交叉。"""
        cache_path = Path(provider.cache_dir) / "index_members_000300_SH_20240101.parquet"
        df = pd.DataFrame({"con_code": ["600000.SH", "600036.SH"]})
        df.to_parquet(cache_path, index=False)

        result = provider.load_index_members("000300.SH", trade_date="20240101", use_cache=True)
        assert len(result) == 2

    def test_different_trade_dates_separate_cache(self, provider):
        """不同 trade_date 应有不同缓存文件。"""
        df1 = pd.DataFrame({"con_code": ["000001.SZ"]})
        df2 = pd.DataFrame({"con_code": ["000001.SZ", "000002.SZ"]})
        path1 = Path(provider.cache_dir) / "index_members_000300_SH_20240101.parquet"
        path2 = Path(provider.cache_dir) / "index_members_000300_SH_20240601.parquet"
        df1.to_parquet(path1, index=False)
        df2.to_parquet(path2, index=False)

        r1 = provider.load_index_members("000300.SH", trade_date="20240101", use_cache=True)
        r2 = provider.load_index_members("000300.SH", trade_date="20240601", use_cache=True)
        assert len(r1) == 1
        assert len(r2) == 2


# ── Financial statements ─────────────────────────────────


class TestLoadFinancials:
    def test_load_fina_indicator(self, provider):
        df = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "ann_date": ["20230425"],
            "end_date": ["20230331"],
            "roe": [0.12],
        })
        provider.api.fina_indicator.return_value = df
        result = provider.load_fina_indicator("000001.SZ", use_cache=False)
        assert len(result) == 1

    def test_load_income(self, provider):
        df = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "ann_date": ["20230425"],
            "end_date": ["20230331"],
            "report_type": ["1"],
            "revenue": [1e9],
        })
        provider.api.income.return_value = df
        result = provider.load_income("000001.SZ", use_cache=False)
        assert len(result) == 1

    def test_load_balancesheet(self, provider):
        df = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "ann_date": ["20230425"],
            "end_date": ["20230331"],
            "report_type": ["1"],
            "total_assets": [1e12],
        })
        provider.api.balancesheet.return_value = df
        result = provider.load_balancesheet("000001.SZ", use_cache=False)
        assert len(result) == 1

    def test_load_cashflow(self, provider):
        df = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "ann_date": ["20230425"],
            "end_date": ["20230331"],
            "report_type": ["1"],
            "n_cashflow_act": [5e8],
        })
        provider.api.cashflow.return_value = df
        result = provider.load_cashflow("000001.SZ", use_cache=False)
        assert len(result) == 1

    def test_load_income_empty(self, provider):
        provider.api.income.return_value = None
        result = provider.load_income("000001.SZ", use_cache=False)
        assert result.empty


class TestSafeReadParquetEquity:
    """验证 TuShareEquityProvider._safe_read_parquet 缓存损坏保护"""

    def test_corrupted_parquet_returns_none(self, tmp_path):
        """损坏的 parquet 文件应返回 None 并自动删除"""
        from aipha.data.tushare_equity_provider import TuShareEquityProvider

        bad_file = tmp_path / "bad.parquet"
        bad_file.write_bytes(b"not a real parquet file")
        result = TuShareEquityProvider._safe_read_parquet(bad_file)
        assert result is None
        assert not bad_file.exists(), "损坏文件应被自动删除"

    def test_valid_parquet_returns_dataframe(self, tmp_path):
        """正确的 parquet 文件应正常返回 DataFrame"""
        from aipha.data.tushare_equity_provider import TuShareEquityProvider

        valid_file = tmp_path / "valid.parquet"
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        df.to_parquet(valid_file)
        result = TuShareEquityProvider._safe_read_parquet(valid_file)
        assert result is not None
        assert len(result) == 2

    def test_load_stock_basic_corrupted_cache_fallback(self, provider, tmp_path):
        """stock_basic 缓存损坏时应自动回退到 API 下载"""
        # 写入损坏缓存
        bad_file = provider.cache_dir / "stock_basic.parquet"
        bad_file.write_bytes(b"corrupted")

        # 配置 API mock 返回正常数据
        provider.api.stock_basic.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "name": ["平安银行"],
            "list_date": ["19910403"],
            "list_status": ["L"],
        })

        result = provider.load_stock_basic(use_cache=True)
        assert len(result) >= 1
        assert not bad_file.read_bytes().startswith(b"corrupted"), "缓存应已被刷新"
