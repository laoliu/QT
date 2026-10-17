import asyncio
import json

import pytest

from aipha.tracker.collectors.etoro import EtoroCollector
from aipha.tracker.evidence import EvidenceQuality


def _history():
    return {"customerId": 3378352, "monthly": [
        {"start": "2026-09-01T00:00:00Z", "gain": -.73, "isSimulation": False},
        {"start": "2026-10-01T00:00:00Z", "gain": -3.22, "isSimulation": False},
        {"start": "2013-01-01T00:00:00Z", "gain": 999, "isSimulation": True},
    ], "yearly": []}


def _fetcher(history=None, holdings=None, holdings_status=200):
    def fetch(url):
        if "/history?" in url:
            return 200, json.dumps(history if history is not None else _history()), url
        if "/portfolios?cid=3378352" in url:
            rows = holdings if holdings is not None else [
                {"InstrumentID": 96, "Direction": "Buy", "Invested": 1.151834,
                 "NetProfit": -9.193085, "Value": .994607},
            ]
            return holdings_status, json.dumps({"AggregatedPositions": rows}), url
        return 200, "<html>login</html>", url
    return fetch


def test_real_returns_and_current_holdings_are_separate_evidence():
    result = asyncio.run(EtoroCollector(_fetcher()).fetch_account_info("jaynemesis"))
    assert result.success
    assert result.evidence_quality == EvidenceQuality.PARTIAL
    assert [r["gain_pct"] for r in result.perf_summary["monthly_returns_pct"]] == [-.73, -3.22]
    assert not result.perf_summary["includes_simulation"]
    assert "total_return" not in result.perf_summary
    assert result.holdings[0]["instrument_id"] == 96
    assert result.holdings[0]["source_fields"]["Value"] == .994607
    evidence = result.to_evidence_snapshot("etoro_jaynemesis")
    assert evidence.parsed_fields["holdings"] == result.holdings
    assert set(evidence.parsed_fields["data_sources"]) == {"returns", "holdings"}


def test_private_holdings_do_not_erase_public_returns():
    result = asyncio.run(EtoroCollector(_fetcher(holdings_status=403)).fetch_account_info("jaynemesis"))
    assert result.success
    assert result.perf_summary["monthly_returns_pct"]
    assert result.holdings is None
    assert result.error_detail


def test_verified_empty_holdings_are_distinct_from_unavailable():
    result = asyncio.run(EtoroCollector(_fetcher(holdings=[])).fetch_account_info("jaynemesis"))
    assert result.holdings == []
    assert "holdings" in result.data_sources


@pytest.mark.parametrize("field,value", [("gain", float("nan")), ("gain", -101),
                                         ("isSimulation", None), ("start", 3)])
def test_invalid_history_never_becomes_performance(field, value):
    history = _history()
    history["monthly"][0][field] = value
    result = asyncio.run(EtoroCollector(_fetcher(history=history)).fetch_account_info("jaynemesis"))
    assert not result.success
    assert not result.perf_summary


def test_wrong_account_redirect_never_becomes_performance():
    def fetch(url):
        return 200, json.dumps(_history()), url.replace("jaynemesis", "other")
    result = asyncio.run(EtoroCollector(fetch).fetch_account_info("jaynemesis"))
    assert not result.success
    assert not result.perf_summary


def test_orchestrator_persists_returns_and_holdings_evidence(tmp_path, monkeypatch):
    from aipha.tracker import collection_orchestrator as orchestration

    class LocalEtoro(EtoroCollector):
        def __init__(self):
            super().__init__(_fetcher())

    monkeypatch.setitem(orchestration._COLLECTOR_REGISTRY, "etoro", LocalEtoro)
    orchestrator = orchestration.CollectionOrchestrator(str(tmp_path))
    try:
        asyncio.run(orchestrator.collect_account("etoro", "jaynemesis"))
        evidence = orchestrator._evidence.get_latest_snapshot("etoro_jaynemesis")
        assert evidence.parsed_fields["holdings"][0]["instrument_id"] == 96
        assert "holdings" in evidence.parsed_fields["data_sources"]
        account = orchestrator._storage.get_account("etoro_jaynemesis")
        assert len(account.perf_summary["monthly_returns_pct"]) == 2
    finally:
        orchestrator.close()
