"""Daily sync freshness must describe persisted data after synchronization."""

from datetime import datetime, timedelta, timezone

import pytest

from cache.stock_cache import StockCache
from scripts import batch_sync


def bars_ending(day, count=72):
    end = datetime.fromisoformat(day)
    return [{"timestamp": int((end - timedelta(days=i)).timestamp() * 1000),
             "open": 12, "high": 13, "low": 11, "close": 12, "volume": 100}
            for i in range(count)]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    instance = StockCache(tmp_path)
    monkeypatch.setattr("cache.stock_cache.StockCache", lambda: instance)
    monkeypatch.setattr(batch_sync, "latest_completed_trading_date", lambda: "2026-10-16")
    monkeypatch.setattr(batch_sync, "check_bridge", lambda: {"online": True})
    monkeypatch.setattr(batch_sync, "_populate_missing_cache", lambda cache: {})
    yield instance
    instance.close()


@pytest.mark.parametrize("now,expected", [
    (datetime(2026, 10, 18, 12), "2026-10-16"),
    (datetime(2026, 10, 19, 8), "2026-10-16"),
    (datetime(2026, 10, 19, 14, 59), "2026-10-16"),
    (datetime(2026, 10, 19, 15), "2026-10-19"),
    (datetime(2026, 10, 5, 16), "2026-09-30"),
    (datetime(2026, 10, 8, 9), "2026-09-30"),
    (datetime(2026, 10, 19, 0, tzinfo=timezone.utc), "2026-10-16"),
    (datetime(2026, 10, 19, 7, tzinfo=timezone.utc), "2026-10-19"),
])
def test_latest_completed_session_uses_calendar_and_cst(now, expected):
    assert batch_sync.latest_completed_trading_date(now) == expected


def test_check_distinguishes_outdated_and_short_history(cache):
    cache.save_klines("600519", bars_ending("2026-10-16"))
    cache.save_klines("600000", bars_ending("2026-10-15"))
    cache.save_klines("000002", bars_ending("2026-10-16", 58))
    result = batch_sync.do_check({"symbols": ["600519", "600000", "000002", "000001"]})
    assert (result["total"], result["fresh"], result["stale"], result["no_cache"],
            result["insufficient_history"], result["need_fetch"]) == (4, 1, 1, 1, 1, 3)


def test_sync_and_subsequent_check_report_the_same_persisted_state(cache, monkeypatch):
    cache.save_klines("600519", bars_ending("2026-10-15"))
    monkeypatch.setattr(batch_sync, "fetch_via_bridge",
                        lambda *args: ({"600519": bars_ending("2026-10-16")}, 0))
    config = {"symbols": ["600519"], "source": "bridge"}
    result = batch_sync.do_sync(config)
    assert result["before"]["fresh"] == 0
    assert (result["status"], result["fetched"], result["updated"], result["need_fetch"]) == (
        "ok", 1, 1, 0)
    check = batch_sync.do_check(config)
    for key in ("total", "fresh", "stale", "no_cache", "insufficient_history", "need_fetch", "target_date"):
        assert result[key] == check[key]


def test_returning_old_data_does_not_claim_update_succeeded(cache, monkeypatch):
    cache.save_klines("600519", bars_ending("2026-10-15"))
    monkeypatch.setattr(batch_sync, "fetch_via_bridge",
                        lambda *args: ({"600519": bars_ending("2026-10-15")}, 0))
    result = batch_sync.do_sync({"symbols": ["600519"], "source": "bridge"})
    assert (result["status"], result["fetched"], result["updated"], result["still_pending"],
            result["stale"]) == ("partial", 1, 0, 1, 1)


def test_omitted_bridge_symbols_are_counted_as_failures(cache, monkeypatch):
    monkeypatch.setattr(batch_sync, "fetch_via_bridge", lambda *args: ({}, 0))
    result = batch_sync.do_sync({"symbols": ["600519"], "source": "bridge"})
    assert (result["status"], result["failed"], result["need_fetch"]) == ("partial", 1, 1)


def test_max_limit_keeps_unattempted_symbols_in_remaining_count(cache, monkeypatch):
    monkeypatch.setattr(batch_sync, "fetch_via_bridge",
                        lambda *args: ({"600519": bars_ending("2026-10-16")}, 0))
    result = batch_sync.do_sync({"symbols": ["600519", "600000"], "source": "bridge", "max_stocks": 1})
    assert (result["attempted"], result["fresh"], result["updated"], result["need_fetch"]) == (1, 1, 1, 1)
