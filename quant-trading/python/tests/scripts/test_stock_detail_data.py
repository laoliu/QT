"""Regressions for stock/index identity and redundant history downloads."""

from datetime import datetime

import pytest

from cache.stock_cache import StockCache
from data_providers import akshare_provider as provider
from data_providers import qmt_http
from data_providers.symbols import to_qmt_code


def bar(day, close=12):
    return {"timestamp": int(datetime.fromisoformat(day).timestamp() * 1000),
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 100, "amount": 1200}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    instance = StockCache(tmp_path)
    monkeypatch.setattr("cache.stock_cache.get_cache", lambda: instance)
    monkeypatch.setattr("cache.stock_cache._now_cst", lambda: datetime(2026, 10, 18, 12))
    yield instance
    instance.close()


def test_legacy_namespace_rows_are_preserved_outside_active_cache(tmp_path):
    cache = StockCache(tmp_path)
    cache.save_klines("000001", [bar("2026-10-16", 3716)])
    cache.save_klines("000001", [bar("2026-10-16", 3716)], "index")
    cache.save_klines("600519", [bar("2026-10-16", 1400)])
    cache._conn.execute("DELETE FROM cache_meta WHERE key = 'stock_index_namespace_v1'")
    cache._conn.commit()
    cache.close()
    cache = StockCache(tmp_path)
    assert cache.get_klines("000001")[0] == []
    assert cache.get_klines("000001", "index")[0][0]["close"] == 3716
    assert cache.get_klines("600519")[0][0]["close"] == 1400
    assert cache._conn.execute("SELECT close_price FROM kline_data_quarantine").fetchall() == [(3716,)]
    cache.save_klines("000001", [bar("2026-10-16", 12)])
    cache.close()
    cache = StockCache(tmp_path)
    assert cache.get_klines("000001")[0][0]["close"] == 12
    cache.close()


def test_holidays_weekends_and_cached_boundaries_are_not_missing(cache):
    days = ["2026-09-30", "2026-10-08", "2026-10-09", "2026-10-12",
            "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
    cache.save_klines("600000", [bar(d) for d in days])
    assert cache.get_klines_missing_range("600000", "2026-09-30", "2026-10-18") == []
    assert cache.get_klines_missing_range("600001", "2026-10-01", "2026-10-07") == []
    assert cache.get_klines_missing_range("600001", "2026-10-17", "2026-10-18") == []


def test_single_missing_trading_day_is_detected(cache):
    cache.save_klines("600000", [bar("2026-10-12"), bar("2026-10-14")])
    assert cache.get_klines_missing_range("600000", "2026-10-12", "2026-10-14") == [
        ("2026-10-13", "2026-10-13")]


def test_combined_parquet_and_sqlite_coverage_needs_no_network(cache, monkeypatch):
    cache.save_klines("600000", [bar("2026-10-16")])
    monkeypatch.setattr(provider, "_fetch_parquet_klines", lambda *a: [bar("2026-10-15")])
    def forbidden(*args):
        pytest.fail("Complete local history must not contact a remote source")
    monkeypatch.setattr(provider, "_fetch_qmt_klines", forbidden)
    monkeypatch.setattr(provider, "_fetch_tdx_klines", forbidden)
    result = provider.get_klines("600000", "1d", "20261015", "20261018")
    assert len(result) == 2


@pytest.mark.parametrize("market,expected", [("cn_stock", "000001.SZ"), ("index", "000001.SH")])
def test_qmt_codes_include_market_identity(market, expected):
    assert to_qmt_code("000001", market) == expected
    assert provider._to_qmt_code("000001", market) == expected


def test_stock_with_index_code_never_calls_index_api(cache, monkeypatch):
    import pandas as pd
    import akshare as ak
    index_calls = []
    monkeypatch.setattr(provider, "_fetch_parquet_klines", lambda *a: [])
    monkeypatch.setattr(provider, "_fetch_qmt_klines", lambda *a: [])
    monkeypatch.setattr(provider, "_fetch_tdx_klines", lambda *a: [])
    monkeypatch.setattr(provider, "_fetch_index_klines", lambda *a: index_calls.append(a) or [bar("2026-10-16", 3716)])
    monkeypatch.setattr(provider, "_eastmoney_circuit_open", False)
    monkeypatch.setattr(ak, "stock_zh_a_hist", lambda **kw: pd.DataFrame([
        {"日期": "2026-10-16", "开盘": 12, "最高": 13, "最低": 11, "收盘": 12}]))
    result = provider.get_klines("000001", "1d", "20261016", "20261018", "cn_stock")
    assert not index_calls
    assert [b["close"] for b in result] == [12]
    assert cache.get_klines("000001")[0][0]["close"] == 12


def test_index_failure_does_not_fall_back_to_stock_api(cache, monkeypatch):
    import akshare as ak
    calls = []
    for name in ("_fetch_parquet_klines", "_fetch_qmt_klines", "_fetch_tdx_klines", "_fetch_index_klines"):
        monkeypatch.setattr(provider, name, lambda *a: [])
    monkeypatch.setattr(ak, "stock_zh_a_hist", lambda **kw: calls.append(kw))
    assert provider.get_klines("000001", "1d", "20261016", "20261018", "index") == []
    assert not calls


def test_local_receiver_fallback_requires_an_active_bridge(monkeypatch):
    import io
    qmt_http._resolved.clear()
    calls = []
    def health(url, **kwargs):
        calls.append(url)
        if ":18888/" in url:
            raise ConnectionRefusedError()
        return io.BytesIO(b'{"status":"ok","bridge_connected":true}')
    monkeypatch.delenv("BRIDGE_RECEIVER_PORT", raising=False)
    monkeypatch.setattr(qmt_http.urllib.request, "urlopen", health)
    assert qmt_http.resolve_bridge_url("http://127.0.0.1:18888") == "http://127.0.0.1:18889"
    assert len(calls) == 2
    qmt_http._resolved.clear()
    monkeypatch.setattr(qmt_http.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(
        b'{"status":"ok","bridge_connected":false}'))
    assert qmt_http.resolve_bridge_url("http://127.0.0.1:18888") == ""
    qmt_http._resolved.clear()


def test_remote_bridge_does_not_switch_to_another_machine(monkeypatch):
    import io
    qmt_http._resolved.clear()
    calls = []
    def offline(url, **kwargs):
        calls.append(url)
        return io.BytesIO(b'{"status":"ok","xtdata":false}')
    monkeypatch.setattr(qmt_http.urllib.request, "urlopen", offline)
    assert qmt_http.resolve_bridge_url("http://192.0.2.1:18888") == ""
    assert calls == ["http://192.0.2.1:18888/health"]
    qmt_http._resolved.clear()


def test_core_response_does_not_fetch_optional_sections(cache, monkeypatch):
    from scripts import stock_detail
    monkeypatch.setattr(stock_detail, "_fetch_quote_bridge", lambda *a: None)
    quote = {"price": 12, "prevClose": 11, "open": 11, "high": 13, "low": 10,
             "volume": 100, "amount": 1200, "name": "Ping An", "bids": [], "asks": []}
    monkeypatch.setattr(stock_detail, "_fetch_sina_quote", lambda *a: dict(quote))
    monkeypatch.setattr(stock_detail, "_fetch_tencent_quote", lambda *a: None)
    monkeypatch.setattr(provider, "get_klines", lambda *a: [bar("2026-10-16")])
    def forbidden(*args, **kwargs):
        pytest.fail("Optional sections must not block the core response")
    monkeypatch.setattr(stock_detail, "get_stock_supplement", forbidden)
    result = stock_detail.get_stock_detail("000001", section="core")
    assert result["quote"]["price"] == 12
    assert len(result["klines"]) == 1


def test_local_snapshot_returns_available_bars_without_waiting_for_network(cache, monkeypatch):
    cache.save_klines("600000", [bar("2026-10-16")])
    monkeypatch.setattr(provider, "_fetch_parquet_klines", lambda *a: [])
    def forbidden(*args):
        pytest.fail("Local snapshot must not contact QMT")
    monkeypatch.setattr(provider, "_fetch_qmt_klines", forbidden)
    assert len(provider.get_klines("600000", "1d", "20261012", "20261018", local_only=True)) == 1


def test_old_short_gap_requests_enough_recent_qmt_bars(monkeypatch):
    import threading
    from types import SimpleNamespace
    calls = []
    entry = {"symbol": "000001", "start": "2025-07-25", "end": "2025-07-25", "market": "cn_stock",
             "event": threading.Event(), "result": []}
    monkeypatch.setattr(provider, "_qmt_batch_queue", {"000001": entry})
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return SimpleNamespace(json=lambda: {"status": "ok", "data": {
            "000001": [{"date": "20250725", "close": 12}]}})
    monkeypatch.setattr(provider.requests, "post", post)
    provider._flush_qmt_batch("http://bridge")
    assert calls[0]["count"] >= (datetime.now() - datetime(2025, 7, 25)).days
    assert entry["event"].is_set()
    assert entry["result"][0]["close"] == 12


def test_disjoint_gaps_share_one_qmt_request(cache, monkeypatch):
    cache.save_klines("600000", [bar("2026-10-13"), bar("2026-10-15")])
    monkeypatch.setattr(provider, "_fetch_parquet_klines", lambda *a: [])
    calls = []
    def fetch(*args):
        calls.append(args)
        return [bar("2026-10-12"), bar("2026-10-14"), bar("2026-10-16")]
    monkeypatch.setattr(provider, "_fetch_qmt_klines", fetch)
    result = provider.get_klines("600000", "1d", "20261012", "20261018")
    assert len(calls) == 1
    assert len(result) == 5


def test_weekly_chart_aggregates_daily_history(cache, monkeypatch):
    cache.save_klines("600000", [bar("2026-10-15", 11), bar("2026-10-16", 12)])
    monkeypatch.setattr(provider, "_fetch_parquet_klines", lambda *a: [])
    result = provider.get_klines("600000", "1w", "20261015", "20261018")
    assert len(result) == 1
    assert result[0]["open"] == 11
    assert result[0]["close"] == 12
    assert result[0]["volume"] == 200


def test_batch_keeps_stock_index_and_duplicate_waiters_separate(monkeypatch):
    import threading
    from types import SimpleNamespace
    entries = [{"symbol": "000001", "start": "2026-10-16", "end": "2026-10-16",
                "market": market, "event": threading.Event(), "result": []}
               for market in ("cn_stock", "index", "cn_stock")]
    monkeypatch.setattr(provider, "_qmt_batch_queue", dict(enumerate(entries)))
    calls = []
    def post(url, **kwargs):
        codes = kwargs["json"]["codes"]
        calls.append(codes)
        assert len(codes) == 1
        close = 12 if codes[0].endswith(".SZ") else 3716
        return SimpleNamespace(json=lambda: {"status": "ok", "data": {
            "000001": [{"date": "20261016", "close": close}]}})
    monkeypatch.setattr(provider.requests, "post", post)
    provider._flush_qmt_batch("http://bridge")
    assert len(calls) == 2
    assert [entry["result"][0]["close"] for entry in entries] == [12, 3716, 12]
    assert all(entry["event"].is_set() for entry in entries)


def test_core_reads_local_history_once_and_excludes_unfinished_session(cache, monkeypatch):
    from scripts import stock_detail
    from scripts import batch_sync
    monkeypatch.setattr(batch_sync, "latest_completed_trading_date", lambda: "2026-10-16")
    monkeypatch.setattr(stock_detail, "_fetch_quote_bridge", lambda *a: None)
    quote = {"price": 12, "prevClose": 11, "open": 11, "high": 13, "low": 10,
             "volume": 100, "amount": 1200, "name": "Ping An", "bids": [], "asks": []}
    monkeypatch.setattr(stock_detail, "_fetch_sina_quote", lambda *a: dict(quote))
    monkeypatch.setattr(stock_detail, "_fetch_tencent_quote", lambda *a: None)
    calls = []
    def history(*args):
        calls.append(args)
        assert args[5] is True, "Core must never download history"
        return [bar("2026-10-16")]
    monkeypatch.setattr(provider, "get_klines", history)
    result = stock_detail.get_stock_detail("000001", section="core")
    assert len(calls) == 1
    assert calls[0][3] == "20261016"
    assert result["klineSource"] == "local"
    assert result["klines"][0]["close"] == 12
    assert result["quote"]["price"] == 12


def test_ready_qmt_core_does_not_wait_for_public_quotes(cache, monkeypatch):
    import threading
    from scripts import stock_detail
    release = threading.Event()
    finished = threading.Event()
    def slow_quote(*args):
        release.wait(3)
        finished.set()
        return None
    monkeypatch.setattr(stock_detail, "_fetch_quote_bridge", lambda *a: {"price": 12, "prevClose": 11})
    monkeypatch.setattr(stock_detail, "_fetch_sina_quote", slow_quote)
    monkeypatch.setattr(stock_detail, "_fetch_tencent_quote", slow_quote)
    monkeypatch.setattr(provider, "get_klines", lambda *a: [bar("2026-10-16")])
    try:
        result = stock_detail.get_stock_detail("000001", section="core")
        assert result["quoteSource"] == "bridge"
        assert not finished.is_set()
        assert result["klines"]
    finally:
        release.set()


def test_quote_refresh_never_loads_history_or_supplements(cache, monkeypatch):
    from scripts import stock_detail
    monkeypatch.setattr(stock_detail, "_fetch_quote_bridge", lambda *a: {"price": 12, "prevClose": 11})
    monkeypatch.setattr(stock_detail, "_fetch_sina_quote", lambda *a: None)
    monkeypatch.setattr(stock_detail, "_fetch_tencent_quote", lambda *a: None)
    def forbidden(*args, **kwargs):
        pytest.fail("Quote refresh must not reload charts or optional data")
    monkeypatch.setattr(provider, "get_klines", forbidden)
    monkeypatch.setattr(stock_detail, "get_stock_supplement", forbidden)
    result = stock_detail.get_stock_detail("000001", section="quote")
    assert result["quote"]["price"] == 12
    assert "klines" not in result


def test_quote_refresh_supplements_missing_qmt_depth(cache, monkeypatch):
    from scripts import stock_detail
    monkeypatch.setattr(stock_detail, "_fetch_quote_bridge", lambda *a: {"price": 12, "prevClose": 11})
    quote = {"price": 12, "prevClose": 11, "open": 11, "high": 13, "low": 10,
             "volume": 100, "amount": 1200, "name": "Ping An",
             "bids": [{"price": 11.99, "volume": 100}], "asks": [{"price": 12.01, "volume": 200}]}
    monkeypatch.setattr(stock_detail, "_fetch_sina_quote", lambda *a: dict(quote))
    monkeypatch.setattr(stock_detail, "_fetch_tencent_quote", lambda *a: dict(quote))
    result = stock_detail.get_stock_detail("000001", section="quote")
    assert result["quote"]["price"] == 12
    assert result["depth"]["bids"][0]["price"] == 11.99
    assert "klines" not in result


@pytest.mark.parametrize("public_price,expected_levels", [(24.19, 5), (24.18, 1)])
def test_partial_qmt_book_still_gets_metrics_and_matching_full_book(cache, monkeypatch, public_price, expected_levels):
    import threading
    from scripts import stock_detail
    ready = threading.Event()
    original_wait = stock_detail.wait
    bridge = {"price": 24.19, "prevClose": 21.99,
              "bids": [{"price": 24.19, "volume": 13200000}], "asks": []}
    public = {"name": "Jida", "price": public_price, "prevClose": 21.99, "pe": -44.03,
              "pb": 4.67, "turnoverRate": 3.36, "volumeRatio": 6.3,
              "bids": [{"price": 24.19 - i * 0.01, "volume": 1000} for i in range(5)], "asks": []}
    def tencent(*args):
        assert ready.wait(2)
        return public
    def grace(futures, timeout):
        ready.set()
        return original_wait(futures, timeout=timeout)
    monkeypatch.setattr(stock_detail, "_fetch_quote_bridge", lambda *a: dict(bridge))
    monkeypatch.setattr(stock_detail, "_fetch_tencent_quote", tencent)
    monkeypatch.setattr(stock_detail, "_fetch_sina_quote", lambda *a: None)
    monkeypatch.setattr(stock_detail, "wait", grace)
    try:
        result = stock_detail.get_stock_detail("003029", section="quote")
        assert result["quote"]["pe"] == -44.03
        assert result["quote"]["pb"] == 4.67
        assert result["quote"]["turnoverRate"] == 3.36
        assert len(result["depth"]["bids"]) == expected_levels
        assert result["depth"]["asks"] == []
        assert result["depthSource"] == ("tencent" if expected_levels == 5 else "bridge")
    finally:
        ready.set()


@pytest.mark.parametrize("raw_tick", [True, False])
def test_bridge_stock_tick_volume_normalized_without_rescaling_legacy(monkeypatch, raw_tick):
    import io
    import json
    from scripts import stock_detail
    quote = {"lastPrice" if raw_tick else "price": 24.19, "volume": 100,
             "bids": [{"price": 24.19, "volume": 50}], "asks": []}
    body = json.dumps({"ok": True, "data": {"quotes": {"003029.SZ": quote}}}).encode()
    monkeypatch.setattr(stock_detail, "_bridge_url", lambda: "http://bridge")
    monkeypatch.setattr(stock_detail.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(body))
    result = stock_detail._fetch_quote_bridge("003029")
    scale = 100 if raw_tick else 1
    assert result["volume"] == 100 * scale
    assert result["bids"][0]["volume"] == 50 * scale


def test_background_history_can_recover_gaps_without_loading_quotes(monkeypatch):
    from scripts import stock_detail, batch_sync
    calls = []
    monkeypatch.setattr(batch_sync, "latest_completed_trading_date", lambda: "2026-10-16")
    def history(*args):
        calls.append(args)
        return [bar("2026-10-15", 11), bar("2026-10-16", 12)]
    monkeypatch.setattr(provider, "get_klines", history)
    result = stock_detail.get_stock_history("000001", "index", 120, "1d")
    assert calls[0][3:] == ("20261016", "index", False)
    assert len(result["klines"]) == 2
    assert "quote" not in result


@pytest.mark.parametrize("source", ["_fetch_sina_quote", "_fetch_tencent_quote"])
@pytest.mark.parametrize("market,prefix", [("cn_stock", "sz"), ("index", "sh")])
def test_quote_identity_matches_kline_identity(monkeypatch, source, market, prefix):
    import io
    from scripts import stock_detail
    calls = []
    def fetch(request, **kwargs):
        calls.append(request.full_url)
        return io.BytesIO(b'')
    monkeypatch.setattr(stock_detail.urllib.request, "urlopen", fetch)
    getattr(stock_detail, source)("000001", market)
    assert len(calls) == 1
    assert prefix + "000001" in calls[0]
