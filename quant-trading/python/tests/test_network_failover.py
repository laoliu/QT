"""Shared Requests proxy-to-direct failover contract."""
import requests
import pytest
import urllib.error
import urllib.request

from aipha import network_failover


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def send(self, session, request, **kwargs):
        self.calls.append(kwargs.get("proxies") or {})
        if len(self.calls) == 1:
            raise requests.exceptions.ReadTimeout("proxy timed out")
        response = requests.Response()
        response.status_code = 200
        response._content = b"ok"
        return response


def test_external_proxy_failure_retries_direct(monkeypatch):
    network_failover.reset_for_tests()
    adapter = FakeAdapter()
    monkeypatch.setattr(requests.Session, "send", adapter.send)
    network_failover.install_requests_failover()
    session = requests.Session()
    request = requests.Request("GET", "https://example.com/data").prepare()
    response = session.send(request, proxies={"https": "http://127.0.0.1:10808"})
    assert response.status_code == 200
    assert adapter.calls == [{"https": "http://127.0.0.1:10808"}, {}]


@pytest.mark.parametrize("url", ["http://127.0.0.1:18888/health", "http://localhost:18889/health", "http://192.168.3.30:18889/health"])
def test_local_links_are_never_fallback_wrapped(monkeypatch, url):
    network_failover.reset_for_tests()
    adapter = FakeAdapter()
    monkeypatch.setattr(requests.Session, "send", adapter.send)
    network_failover.install_requests_failover()
    session = requests.Session()
    request = requests.Request("GET", url).prepare()
    # The fake's first call fails; local traffic must propagate that failure.
    with pytest.raises(requests.exceptions.ReadTimeout):
        session.send(request, proxies={"http": "http://127.0.0.1:10808"})
    assert adapter.calls == [{"http": "http://127.0.0.1:10808"}]


def test_urllib_external_proxy_failure_retries_direct(monkeypatch):
    network_failover.reset_for_tests()
    calls = []
    def proxy_urlopen(url, data=None, timeout=None, **kwargs):
        calls.append("proxy")
        raise urllib.error.URLError("proxy unavailable")
    def direct_open(self, url, data=None, timeout=None):
        calls.append("direct")
        return object()
    monkeypatch.setattr(urllib.request, "urlopen", proxy_urlopen)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "http://127.0.0.1:10808"})
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", direct_open)
    network_failover.install_requests_failover()
    assert urllib.request.urlopen("https://example.com/data", timeout=3) is not None
    assert calls == ["proxy", "direct"]


def test_urllib_local_link_does_not_retry(monkeypatch):
    network_failover.reset_for_tests()
    calls = []
    def fail(url, data=None, timeout=None, **kwargs):
        calls.append(1)
        raise urllib.error.URLError("local unavailable")
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    network_failover.install_requests_failover()
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen("http://127.0.0.1:18888/health", timeout=1)
    assert calls == [1]
