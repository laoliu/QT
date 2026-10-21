"""A broken inherited proxy must trigger one direct retry in the daily worker."""
import os

import pytest
import requests

from aipha.research.daily_network import ProxyFallbackApi


class Api:
    _DataApi__http_url = "http://api.waditu.com/dataapi"
    def query(self, api_name, fields="", **kwargs):
        return api_name, fields, kwargs


@pytest.mark.parametrize("error", [requests.exceptions.ProxyError, requests.exceptions.ConnectTimeout,
                                    requests.exceptions.ReadTimeout])
def test_broken_proxy_retries_direct_and_stays_direct(monkeypatch, error):
    monkeypatch.setattr("aipha.research.daily_network.get_environ_proxies",
                        lambda url: {"http": "http://127.0.0.1:10808"})
    calls, events = [], []
    api = Api()
    original = api.query
    def query(endpoint, fields="", **kwargs):
        calls.append(endpoint)
        if len(calls) == 1:
            raise error("broken proxy")
        return original(endpoint, fields, **kwargs)
    api.query = query
    wrapper = ProxyFallbackApi(api, on_fallback=lambda *event: events.append(event))
    assert wrapper.daily(trade_date="20261020") == ("daily", "", {"trade_date": "20261020"})
    assert wrapper.stock_basic() == ("stock_basic", "", {})
    assert calls == ["daily", "daily", "stock_basic"]
    assert events == [("daily", error.__name__)]
    assert os.environ["NO_PROXY"] == os.environ["no_proxy"] == "*"


def test_healthy_proxy_is_retained(monkeypatch):
    monkeypatch.setattr("aipha.research.daily_network.get_environ_proxies",
                        lambda url: {"http": "http://127.0.0.1:10808"})
    events = []
    wrapper = ProxyFallbackApi(Api(), on_fallback=lambda *event: events.append(event))
    assert wrapper.daily() == ("daily", "", {})
    assert events == []


def test_direct_failure_propagates_without_extra_retry(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setattr("aipha.research.daily_network.get_environ_proxies", lambda url: {})
    api = Api()
    api.query = lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectionError("unavailable"))
    with pytest.raises(requests.ConnectionError):
        ProxyFallbackApi(api).daily()


def test_proxy_and_direct_failure_propagate(monkeypatch):
    monkeypatch.setattr("aipha.research.daily_network.get_environ_proxies",
                        lambda url: {"http": "http://127.0.0.1:10808"})
    calls = []
    api = Api()
    def fail(*args, **kwargs):
        calls.append(1)
        raise requests.ConnectionError("unavailable")
    api.query = fail
    with pytest.raises(requests.ConnectionError):
        ProxyFallbackApi(api).daily()
    assert len(calls) == 2
