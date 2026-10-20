"""Yahoo failures must be bounded and must not masquerade as empty signals."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from curl_cffi.requests import Session

from data_providers import yahoo_transport as transport
from engines import event_catalyst_engine as engine


@pytest.fixture(autouse=True)
def isolated_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "_state_path", lambda: tmp_path / "cooldown.json")
    monkeypatch.setattr(transport, "YAHOO_PROXY", "http://127.0.0.1:12345")


def test_request_caps_dependency_timeout_and_ignores_domestic_bypass(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    request = Mock(return_value=SimpleNamespace(status_code=200))
    monkeypatch.setattr(Session, "request", request)
    with transport.create_session() as session:
        session.get("https://query1.finance.yahoo.com/test", timeout=30)
        assert session.proxies["all"] == "http://127.0.0.1:12345"
    assert 0 < request.call_args.kwargs["timeout"] <= 5
    assert not transport.in_cooldown()


def test_rate_limit_prevents_later_requests_across_sessions(monkeypatch):
    request = Mock(return_value=SimpleNamespace(status_code=429))
    monkeypatch.setattr(Session, "request", request)
    with transport.create_session() as session:
        with pytest.raises(RuntimeError, match="429"):
            session.get("https://query1.finance.yahoo.com/test")
    with transport.create_session() as session:
        with pytest.raises(TimeoutError):
            session.get("https://query1.finance.yahoo.com/next")
    assert request.call_count == 1
    assert transport.in_cooldown()


def test_cooldown_expires_and_network_can_recover(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(transport.time, "time", lambda: clock[0])
    transport._start_cooldown()
    assert transport.in_cooldown()
    clock[0] += transport.COOLDOWN_SECONDS + 1
    request = Mock(return_value=SimpleNamespace(status_code=200))
    monkeypatch.setattr(Session, "request", request)
    with transport.create_session() as session:
        assert session.get("https://query1.finance.yahoo.com/test").status_code == 200
    request.assert_called_once()


def test_network_failure_stops_subsequent_ticker_attempts(monkeypatch):
    request = Mock(side_effect=TimeoutError("connection timed out"))
    monkeypatch.setattr(Session, "request", request)
    with transport.create_session() as session:
        with pytest.raises(TimeoutError):
            session.get("https://query1.finance.yahoo.com/test")
        with pytest.raises(TimeoutError):
            session.get("https://query1.finance.yahoo.com/next")
    assert request.call_count == 1


def test_batch_budget_prevents_any_more_network_calls(monkeypatch):
    request = Mock()
    monkeypatch.setattr(Session, "request", request)
    with transport.create_session() as session:
        session.deadline = 0
        with pytest.raises(TimeoutError):
            session.get("https://query1.finance.yahoo.com/test")
    request.assert_not_called()


def test_explicit_proxy_has_priority_over_cleared_system_proxy(monkeypatch):
    monkeypatch.setenv("QUANT_US_PROXY", "socks5h://127.0.0.1:12345")
    assert transport.discover_proxy() == "socks5h://127.0.0.1:12345"


def test_single_ticker_multiindex_download_is_parsed(monkeypatch):
    frame = pd.DataFrame({("QCOM", "Close"): [100.0, 106.0], ("QCOM", "Volume"): [10, 20]})
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=lambda *args, **kwargs: frame))
    quotes = engine._fetch_us_quotes(["QCOM"])
    assert quotes["QCOM"]["changePct"] == 6.0
    assert quotes["QCOM"]["price"] == 106.0


def test_missing_quotes_skip_news_and_never_claim_no_catalysts(monkeypatch):
    monkeypatch.setattr(engine, "_fetch_us_quotes", lambda symbols: {})
    news = Mock()
    monkeypatch.setattr(engine, "_fetch_us_news", news)
    result = engine._scan({"us_symbols": ["QCOM"]})
    assert result["data_status"] == "unavailable"
    assert "error" in result
    assert "triggered" not in result
    news.assert_not_called()
