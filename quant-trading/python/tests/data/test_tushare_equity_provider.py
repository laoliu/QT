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


class TestHistoricalListingDates:
    @staticmethod
    def market():
        return pd.DataFrame({"ts_code": ["600000.SH", "300114.SZ", "300114.SZ"],
                             "trade_date": ["20231229", "20231228", "20231229"]})

    def test_renamed_symbol_uses_dated_source_and_keeps_original_cache(self, provider):
        original = pd.DataFrame({"ts_code": ["600000.SH"], "list_date": ["19991110"]})
        stock_path = provider.cache_dir / "stock_basic.parquet"
        original.to_parquet(stock_path, index=False)
        before = stock_path.read_bytes()
        provider.api.bak_basic.return_value = pd.DataFrame({
            "ts_code": ["300114.SZ"], "trade_date": ["20231229"], "list_date": ["20100827"],
        })
        result = provider.load_historical_listing_dates(self.market()).set_index("ts_code")
        assert result.loc["300114.SZ", "list_date"] == "20100827"
        assert result.loc["300114.SZ", "list_date_source"] == "tushare.bak_basic"
        assert result.loc["300114.SZ", "list_date_observation_date"] == "20231229"
        assert result.loc["600000.SH", "list_date_source"] == "tushare.stock_basic"
        assert stock_path.read_bytes() == before
        provider.api.bak_basic.assert_called_once_with(
            ts_code="300114.SZ", trade_date="20231229", fields="ts_code,trade_date,list_date",
        )
        provider.api.bak_basic.reset_mock()
        cached = provider.load_historical_listing_dates(self.market()).set_index("ts_code")
        pd.testing.assert_frame_equal(result, cached)
        provider.api.bak_basic.assert_not_called()

    @pytest.mark.parametrize("change", ["empty", "wrong_symbol", "wrong_day", "duplicate", "invalid_date", "too_late"])
    def test_unproven_listing_is_rejected_without_creating_cache(self, provider, change):
        pd.DataFrame({"ts_code": ["600000.SH"], "list_date": ["19991110"]}).to_parquet(
            provider.cache_dir / "stock_basic.parquet", index=False,
        )
        row = {"ts_code": "300114.SZ", "trade_date": "20231229", "list_date": "20100827"}
        if change == "wrong_symbol": row["ts_code"] = "302132.SZ"
        if change == "wrong_day": row["trade_date"] = "20240101"
        if change == "invalid_date": row["list_date"] = "not-a-date"
        if change == "too_late": row["list_date"] = "20231229"
        records = [] if change == "empty" else [row] * (2 if change == "duplicate" else 1)
        provider.api.bak_basic.return_value = pd.DataFrame(records)
        with pytest.raises(ValueError, match="historical listing"):
            provider.load_historical_listing_dates(self.market())
        assert not list(provider.cache_dir.glob("listing_bak_basic_*.parquet"))

    def test_null_date_for_known_symbol_is_filled_from_historical_record(self, provider):
        pd.DataFrame({"ts_code": ["600000.SH", "300114.SZ"], "list_date": ["19991110", None]}).to_parquet(
            provider.cache_dir / "stock_basic.parquet", index=False,
        )
        provider.api.bak_basic.return_value = pd.DataFrame({
            "ts_code": ["300114.SZ"], "trade_date": ["20231229"], "list_date": ["20100827"],
        })
        result = provider.load_historical_listing_dates(self.market())
        assert not result.ts_code.duplicated().any()
        assert result.list_date.notna().all()


class TestMonthlyIndexHistory:
    @staticmethod
    def history():
        return pd.DataFrame([
            {"index_code": "000905.SH", "con_code": f"{600000 + symbol:06}.SH", "trade_date": day, "weight": .2}
            for day in ["20231229", "20240131"] for symbol in range(500)
        ])

    def test_actual_dates_seed_and_cache_are_preserved(self, provider):
        provider.api.index_weight.return_value = self.history()
        result = provider.load_index_membership_history("000905.SH", "20240102", "20240202")
        assert list(result) == ["20231229", "20240131"]
        provider.api.index_weight.assert_called_once_with(
            index_code="000905.SH", start_date="20231201", end_date="20240202", limit=5000, offset=0,
        )
        provider.api.index_weight.reset_mock()
        assert provider.load_index_membership_history("000905.SH", "20240102", "20240202") == result
        provider.api.index_weight.assert_not_called()

    @pytest.mark.parametrize("bad", ["no_seed", "missing_month", "duplicate", "wrong_index", "future", "weights", "missing_member", "empty"])
    def test_incomplete_membership_never_becomes_a_cache(self, provider, bad):
        rows = self.history()
        if bad == "no_seed": rows = rows.loc[rows.trade_date.eq("20240131")]
        if bad == "missing_month": rows = rows.loc[rows.trade_date.eq("20231229")]
        if bad == "duplicate": rows = pd.concat([rows, rows.iloc[:1]])
        if bad == "wrong_index": rows["index_code"] = "000300.SH"
        if bad == "future": rows.loc[rows.trade_date.eq("20240131"), "trade_date"] = "20240229"
        if bad == "weights": rows["weight"] = 10.
        if bad == "missing_member": rows = rows.iloc[1:]
        if bad == "empty": rows = rows.head(0)
        provider.api.index_weight.return_value = rows
        with pytest.raises(ValueError, match="historical index"):
            provider.load_index_membership_history("000905.SH", "20240102", "20240202")
        assert not list(provider.cache_dir.glob("index_weights_v1_*.parquet"))

    def test_pagination_keeps_all_history(self, provider):
        rows = pd.DataFrame([
            {"index_code": "000905.SH", "con_code": f"{symbol:06}.SH", "trade_date": day.strftime("%Y%m%d"), "weight": .2}
            for day in pd.date_range("2022-12-01", "2023-11-01", freq="BME") for symbol in range(500)
        ])
        provider.api.index_weight.side_effect = [rows.iloc[:5000], rows.iloc[5000:]]
        with patch.object(provider._gateway._limiter, "acquire"):
            result = provider.load_index_membership_history("000905.SH", "20230102", "20231102")
        assert len(result) == 11
        assert provider.api.index_weight.call_args_list[1].kwargs["offset"] == 5000


@pytest.mark.parametrize("endpoint", ["fina_indicator", "income", "balancesheet", "cashflow"])
def test_schema_complete_empty_financial_cache_stays_offline(provider, endpoint):
    columns = ["ts_code", "ann_date", "end_date", "f_ann_date", "report_type", "or_yoy", "roic",
               "ocf_to_or", "assets_turn", "netprofit_yoy", "c_pay_acq_const_fiolta"]
    path = provider.cache_dir / f"{endpoint}_pit_v2_600000_SH_20210101_20261016.parquet"
    pd.DataFrame(columns=columns).to_parquet(path, index=False)
    result = getattr(provider, f"load_{endpoint}")("600000.SH", start_date="20210101", end_date="20261016")
    assert result.empty
    getattr(provider.api, endpoint).assert_not_called()


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


class TestResearchDividendHistory:
    def test_fetch_and_cache_preserve_settlement_dates(self, provider):
        row = {"ts_code": "600000.SH", "end_date": "20231231", "ann_date": "20240101",
               "div_proc": "实施", "record_date": "20240201", "ex_date": "20240202",
               "cash_div_tax": 1.0, "stk_div": 0.1,
               "pay_date": "20240206", "div_listdate": "20240207", "imp_ann_date": "20240130"}
        provider.api.dividend.return_value = pd.DataFrame([row])
        downloaded = provider.load_dividend_history(["600000.SH"], use_cache=False)
        cached = provider.load_dividend_history(["600000.SH"])
        pd.testing.assert_frame_equal(downloaded, cached)
        assert cached.iloc[0]["pay_date"] == "20240206"
        assert cached.iloc[0]["div_listdate"] == "20240207"
        provider.api.dividend.assert_called_once()

    def test_endpoint_failure_or_none_cannot_mean_no_actions(self, provider):
        provider.api.dividend.side_effect = RuntimeError("unavailable")
        with patch("aipha.data.tushare_equity_provider.time.sleep"):
            with pytest.raises(RuntimeError, match="unavailable"):
                provider.load_dividend_history(["600000.SH"], use_cache=False)
            provider.api.dividend.side_effect = None
            provider.api.dividend.return_value = None
            with pytest.raises(ValueError, match="no result"):
                provider.load_dividend_history(["600000.SH"], use_cache=False)

    def test_empty_response_is_valid_but_wrong_symbol_is_rejected(self, provider):
        provider.api.dividend.return_value = pd.DataFrame()
        assert provider.load_dividend_history(["600000.SH"], use_cache=False).empty
        provider.api.dividend.return_value = pd.DataFrame({"ts_code": ["600001.SH"]})
        with pytest.raises(ValueError, match="different symbol"):
            provider.load_dividend_history(["600000.SH"], use_cache=False)

    def test_stale_dividend_cache_is_refreshed(self, provider):
        provider.api.dividend.return_value = pd.DataFrame({"ts_code": ["600000.SH"], "pay_date": ["20240206"]})
        provider.load_dividend_history(["600000.SH"], use_cache=False)
        provider.api.dividend.return_value = pd.DataFrame({"ts_code": ["600000.SH"], "pay_date": ["20240207"]})
        with patch("aipha.data.tushare_equity_provider.is_cache_fresh", return_value=False):
            refreshed = provider.load_dividend_history(["600000.SH"])
        assert refreshed.iloc[0]["pay_date"] == "20240207"


class TestResearchTradeCalendar:
    @staticmethod
    def calendar(start, end):
        dates = pd.date_range(start, end)
        return pd.DataFrame({"exchange": "SSE", "cal_date": dates.strftime("%Y%m%d"),
                             "is_open": ((dates.dayofweek < 5) & (dates != pd.Timestamp("2024-01-01"))).astype(int)})

    def test_single_range_download_preserves_closures_and_reuses_cache(self, provider):
        def fetch(**kwargs):
            assert kwargs["exchange"] == "SSE" and "is_open" not in kwargs
            return self.calendar(kwargs["start_date"], kwargs["end_date"])
        provider.api.trade_cal.side_effect = fetch
        result = provider.load_trade_calendar("20231229", "20240103", use_cache=False)
        assert provider.api.trade_cal.call_count == 1
        assert len(result) == 6 and result.is_open.tolist() == [1, 0, 0, 0, 1, 1]
        cached = provider.load_trade_calendar("20231229", "20240103")
        pd.testing.assert_frame_equal(result, cached)
        assert provider.api.trade_cal.call_count == 1

    def test_full_history_cache_serves_subset_without_another_api_call(self, provider):
        frame = self.calendar("19901219", "20261016")
        path = provider.cache_dir / "trade_cal_SSE_19901219_20261016.parquet"
        frame.to_parquet(path, index=False)
        before = path.read_bytes()
        result = provider.load_trade_calendar("20231229", "20240103")
        assert len(result) == 6
        assert result.is_open.tolist() == [1, 0, 0, 0, 1, 1]
        provider.api.trade_cal.assert_not_called()
        assert path.read_bytes() == before

    def test_legacy_annual_fragments_still_cover_the_requested_range(self, provider):
        self.calendar("20231229", "20231231").to_parquet(
            provider.cache_dir / "trade_cal_SSE_20231229_20231231.parquet", index=False)
        self.calendar("20240101", "20240103").to_parquet(
            provider.cache_dir / "trade_cal_SSE_20240101_20240103.parquet", index=False)
        result = provider.load_trade_calendar("20231230", "20240102")
        assert len(result) == 4 and result.is_open.tolist() == [0, 0, 0, 1]
        provider.api.trade_cal.assert_not_called()

    def test_conflicting_cached_closure_flags_fail_without_network_or_rewrite(self, provider):
        frame = self.calendar("20240101", "20240103")
        paths = [provider.cache_dir / "trade_cal_SSE_20240101_20240103.parquet",
                 provider.cache_dir / "trade_cal_SSE_20240102_20240103.parquet"]
        frame.to_parquet(paths[0], index=False)
        changed = self.calendar("20240102", "20240103")
        changed.loc[0, "is_open"] = 0
        changed.to_parquet(paths[1], index=False)
        before = [p.read_bytes() for p in paths]
        with pytest.raises(ValueError, match="conflicting cached trade calendar"):
            provider.load_trade_calendar("20240101", "20240103")
        provider.api.trade_cal.assert_not_called()
        assert [p.read_bytes() for p in paths] == before

    @pytest.mark.parametrize("bad", ["empty", "truncated", "missing_closed"])
    def test_calendar_does_not_cache_incomplete_endpoint_result(self, provider, bad):
        result = self.calendar("20240101", "20240103")
        if bad == "empty":
            result = pd.DataFrame()
        elif bad == "truncated":
            result = result.iloc[:-1]
        else:
            result = result[result.is_open.eq(1)]
        provider.api.trade_cal.return_value = result
        with pytest.raises(ValueError, match="trade calendar"):
            provider.load_trade_calendar("20240101", "20240103")
        assert not list(provider.cache_dir.glob("trade_cal_*.parquet"))

    def test_incomplete_cache_is_refetched(self, provider):
        path = provider.cache_dir / "trade_cal_SSE_20240101_20240103.parquet"
        self.calendar("20240102", "20240103").to_parquet(path)
        provider.api.trade_cal.return_value = self.calendar("20240101", "20240103")
        result = provider.load_trade_calendar("20240101", "20240103")
        assert len(result) == 3
        provider.api.trade_cal.assert_called_once()


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


class TestLoadHistoricalIndustry:
    def test_keeps_effective_membership_dates(self, provider):
        provider.api.index_classify.return_value = pd.DataFrame({
            "index_code": ["801010.SI", "801020.SI"],
            "industry_name": ["农林牧渔", "采掘"],
        })
        provider.api.index_member.side_effect = [
            pd.DataFrame({"con_code": ["600000.SH"], "in_date": ["20200101"], "out_date": ["20240102"]}),
            pd.DataFrame({"con_code": ["600000.SH"], "in_date": ["20240103"], "out_date": [None]}),
        ]

        history = provider.load_sw_industry_history(use_cache=False)

        assert set(history.columns) == {
            "ts_code", "industry_code", "industry_name", "in_date", "out_date",
        }
        assert history["industry_name"].tolist() == ["农林牧渔", "采掘"]
        assert history["in_date"].tolist() == ["20200101", "20240103"]


# ── Financial statements ─────────────────────────────────


class TestLoadFinancials:
    def test_indicator_cache_retains_ambiguous_updates_for_pit_rejection(self, provider):
        from aipha.data.tushare_pit_provider import TuSharePITProvider
        from datetime import date

        row = pd.DataFrame({
            "ts_code": ["000001.SZ"], "ann_date": ["20230425"], "end_date": ["20230331"],
            "roe": [10.], "or_yoy": [1.], "roic": [2.], "ocf_to_or": [3.],
            "assets_turn": [4.], "netprofit_yoy": [5.],
        })
        legacy = provider.cache_dir / "fina_indicator_000001_SZ.parquet"
        row.to_parquet(legacy)
        original = legacy.read_bytes()
        provider.api.fina_indicator.return_value = row
        provider.load_fina_indicator("000001.SZ", use_cache=False)
        provider.api.fina_indicator.return_value = row.assign(roe=11.)
        assert len(provider.load_fina_indicator("000001.SZ", use_cache=False)) == 2
        with pytest.raises(ValueError, match="conflicting financial revisions"):
            TuSharePITProvider(provider).get_indicator_pit("000001.SZ", date(2023, 5, 1), ["roe"])
        assert legacy.read_bytes() == original

    @pytest.mark.parametrize("endpoint", ["income", "balancesheet", "cashflow"])
    def test_statement_cache_preserves_versions_and_legacy_file(self, provider, endpoint):
        original = pd.DataFrame({
            "ts_code": ["000001.SZ"], "ann_date": ["20230425"], "f_ann_date": ["20230425"],
            "end_date": ["20230331"], "report_type": ["1"], "revenue": [100.],
            "c_pay_acq_const_fiolta": [10.],
        })
        legacy = provider.cache_dir / f"{endpoint}_000001_SZ.parquet"
        original.drop(columns="f_ann_date").to_parquet(legacy)
        legacy_bytes = legacy.read_bytes()
        api = getattr(provider.api, endpoint)
        load = getattr(provider, "load_" + endpoint)
        api.return_value = original
        assert len(load("000001.SZ", use_cache=False)) == 1
        api.return_value = original.assign(f_ann_date="20230508", revenue=120.)
        merged = load("000001.SZ", use_cache=False)
        assert merged.revenue.tolist() == [100., 120.]
        assert merged.f_ann_date.tolist() == ["20230425", "20230508"]
        assert "f_ann_date" in api.call_args.kwargs["fields"].split(",")
        assert len(load("000001.SZ")) == 2
        assert api.call_count == 2
        assert legacy.read_bytes() == legacy_bytes

    @pytest.mark.parametrize("endpoint", ["income", "balancesheet", "cashflow"])
    def test_partial_statement_cache_is_not_reused_as_full_history(self, provider, endpoint):
        row = pd.DataFrame({
            "ts_code": ["000001.SZ"], "ann_date": ["20230425"], "f_ann_date": ["20230425"],
            "end_date": ["20230331"], "report_type": ["1"], "revenue": [100.],
            "c_pay_acq_const_fiolta": [10.],
        })
        api = getattr(provider.api, endpoint)
        load = getattr(provider, "load_" + endpoint)
        api.return_value = row
        load("000001.SZ", start_date="20230101", end_date="20231231")
        api.return_value = pd.concat([row, row.assign(ann_date="20220425", f_ann_date="20220425", end_date="20220331")])
        assert len(load("000001.SZ")) == 2
        assert api.call_count == 2

    def test_statement_api_must_return_requested_actual_date_field(self, provider):
        provider.api.income.return_value = pd.DataFrame({
            "ts_code": ["000001.SZ"], "ann_date": ["20230425"], "end_date": ["20230331"],
            "report_type": ["1"], "revenue": [100.],
        })
        with pytest.raises(ValueError, match="f_ann_date"):
            provider.load_income("000001.SZ", use_cache=False)

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
            "f_ann_date": ["20230425"],
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
            "f_ann_date": ["20230425"],
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
            "f_ann_date": ["20230425"],
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
