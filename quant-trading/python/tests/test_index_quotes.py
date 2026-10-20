from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from data_providers import akshare_provider as provider


@pytest.mark.parametrize("market,expected", [("index", "sh000001"), ("cn_stock", "sz000001")])
def test_sina_quote_keeps_instrument_identity_and_source_time(monkeypatch, market, expected):
    fields = ["0"] * 32
    fields[:6] = ["instrument", "10", "9", "11", "12", "8"]
    fields[30:32] = ["2026-10-16", "15:00:00"]
    seen = []

    def get(url, **kwargs):
        seen.append(url)
        return SimpleNamespace(text='var hq_str_test="' + ",".join(fields) + '";')

    monkeypatch.setattr(provider.requests, "get", get)
    monkeypatch.setattr(provider, "_xq_quote", lambda *args: pytest.fail("unnecessary fallback"))
    result = provider.get_quote("000001", market)
    assert seen == [f"https://hq.sinajs.cn/list={expected}"]
    assert result["symbol"]["market"] == market
    assert result["source"] == "sina"
    expected_time = datetime(2026, 10, 16, 15, tzinfo=timezone(timedelta(hours=8)))
    assert result["dataTimestamp"] == int(expected_time.timestamp() * 1000)
    assert result["retrievedAt"] > result["dataTimestamp"]


def test_sina_quote_without_source_time_remains_unknown(monkeypatch):
    fields = ["instrument", "10", "9", "11", "12", "8", "0", "0", "100", "1000"]
    monkeypatch.setattr(provider.requests, "get", lambda *args, **kwargs: SimpleNamespace(
        text='var hq_str_test="' + ",".join(fields) + '";'))
    assert provider.get_quote("399006", "index")["dataTimestamp"] is None
