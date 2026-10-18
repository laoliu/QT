"""Offline operating inputs never substitute weekdays or silently trust bad caches."""
import hashlib
from types import SimpleNamespace

import pandas as pd
import pytest

from aipha.research.daily_inputs import OperatingEquityProvider


@pytest.fixture
def provider(tmp_path):
    value = OperatingEquityProvider.__new__(OperatingEquityProvider)
    value.cache_dir = tmp_path / "cache"
    value.cache_dir.mkdir()
    value.archive_dir = tmp_path / "archive"
    value._names = None
    value.calendar = pd.DataFrame({"cal_date": pd.date_range("2024-01-01", "2024-01-04"),
                                   "is_open": [0, 1, 1, 0]})
    value._retry_call = lambda function, **kwargs: function(**kwargs)
    return value


def test_ranges_use_verified_calendar_and_reject_gaps(provider):
    called = []
    def bars(day, **kwargs):
        called.append(day)
        return pd.DataFrame({"trade_date": [day]})
    provider.load_daily_bars = bars
    provider.load_daily_bars_range("20240101", "20240104")
    assert called == ["20240102", "20240103"]
    provider.calendar = provider.calendar.iloc[1:]
    with pytest.raises(ValueError, match="incomplete"):
        provider.load_daily_bars_range("20240101", "20240104")


def test_overwritten_raw_capture_is_archived_by_hash(provider):
    path = provider.cache_dir / "sample.parquet"
    pd.DataFrame({"x": [1]}).to_parquet(path)
    before = path.read_bytes()
    provider._atomic_write_parquet(pd.DataFrame({"x": [2]}), path)
    backup = provider.archive_dir / hashlib.sha256(before).hexdigest() / path.name
    assert backup.read_bytes() == before
    assert pd.read_parquet(path).x.tolist() == [2]


def test_daily_cache_can_be_bypassed_and_invalid_cache_is_rejected(provider):
    calls = []
    def fetch(**kwargs):
        calls.append(kwargs["trade_date"])
        return pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": [kwargs["trade_date"]], "adj_factor": [2.]})
    provider.api = SimpleNamespace(adj_factor=fetch)
    pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20240102"], "adj_factor": [1.]}).to_parquet(
        provider.cache_dir / "adj_factor_202401.parquet")
    assert provider.load_adj_factor_bulk("20240102", "20240102").adj_factor.tolist() == [1.]
    assert calls == []
    assert provider.load_adj_factor_bulk("20240102", "20240102", use_cache=False).adj_factor.tolist() == [2.]
    pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20240103"]}).to_parquet(
        provider.cache_dir / "adj_factor_20240102.parquet")
    with pytest.raises(ValueError, match="incomplete"):
        provider.load_adj_factor_bulk("20240102", "20240102")


def test_name_history_fetches_pages_once_and_filters_symbols(provider):
    calls = []
    def names(**kwargs):
        calls.append(kwargs["offset"])
        n = 5000 if kwargs["offset"] == 0 else 1
        return pd.DataFrame({"ts_code": [str(kwargs["offset"] + i) for i in range(n)], "name": "A",
                             "start_date": "20200101", "end_date": None, "ann_date": "20191231"})
    provider.api = SimpleNamespace(namechange=names)
    assert len(provider.load_namechange()) == 5001
    assert len(provider.load_namechange("5000")) == 1
    assert calls == [0, 5000]


def test_repeated_name_history_page_with_new_index_fails(provider):
    def names(**kwargs):
        return pd.DataFrame({"ts_code": [str(i) for i in range(5000)]},
                            index=range(kwargs["offset"], kwargs["offset"] + 5000))
    provider.api = SimpleNamespace(namechange=names)
    with pytest.raises(ValueError, match="repeated"):
        provider.load_namechange()


def test_unterminated_name_history_fails(provider):
    provider.api = SimpleNamespace(namechange=lambda **kwargs: pd.DataFrame({"ts_code": [str(kwargs["offset"])] * 5000}))
    with pytest.raises(ValueError, match="did not terminate"):
        provider.load_namechange()
