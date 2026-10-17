import asyncio

import pytest

from aipha.tracker import collection_orchestrator as orchestration
from aipha.tracker.collectors.base import CollectorResult
from aipha.tracker.collectors.etoro import EtoroCollector
from aipha.tracker.collectors.xueqiu import XueqiuAuthenticatedCollector
from aipha.tracker.evidence import EvidenceQuality, FailureType
from aipha.tracker.models import Holding, PortfolioSnapshot, PortfolioSource
from datetime import datetime
from decimal import Decimal


def test_public_profile_is_partial_evidence_without_invented_performance():
    html = '<meta property="og:type" content="profile"><meta property="og:title" content="Alice">'
    result = asyncio.run(EtoroCollector(lambda url: (200, html, url)).fetch_account_info("alice"))
    assert result.success
    assert result.display_name == "Alice"
    assert result.evidence_quality == EvidenceQuality.PARTIAL
    assert result.perf_summary == {}


@pytest.mark.parametrize("status,expected", [(401, FailureType.LOGIN_REQUIRED), (403, FailureType.WAF_BLOCKED),
                                           (404, FailureType.LINK_DEAD), (429, FailureType.RATE_LIMITED)])
def test_http_failure_classification(status, expected):
    result = asyncio.run(EtoroCollector(lambda url: (status, "", url)).fetch_account_info("alice"))
    assert not result.success
    assert result.failure_type == expected


def test_generic_or_login_page_never_becomes_account_success():
    for final, body in [("https://www.etoro.com/login", "Alice"),
                        ("https://www.etoro.com/people/alice", '<meta property="og:title" content="Home">')]:
        result = asyncio.run(EtoroCollector(lambda url: (200, body, final)).fetch_account_info("alice"))
        assert not result.success


def test_redirect_to_another_account_is_not_misattributed():
    html = '<meta property="og:type" content="profile"><meta property="og:title" content="Bob">'
    collector = EtoroCollector(lambda url: (200, html, "https://www.etoro.com/people/bob"))
    result = asyncio.run(collector.fetch_account_info("alice"))
    assert not result.success
    assert result.failure_type == FailureType.FIELDS_MISSING


def test_registered_collector_persists_success_and_unknown_failure(tmp_path, monkeypatch):
    class LocalCollector:
        platform_name = "etoro"

        async def fetch_account_info(self, uid):
            return CollectorResult(platform_uid=uid, display_name="Alice", success=True,
                                   failure_type=FailureType.NONE, evidence_quality=EvidenceQuality.PARTIAL)

    monkeypatch.setattr(orchestration, "_COLLECTOR_REGISTRY", {})
    orchestration.register_collector(LocalCollector)
    orchestrator = orchestration.CollectionOrchestrator(str(tmp_path))
    try:
        snapshot = asyncio.run(orchestrator.collect_account("etoro", "alice"))
        assert snapshot.is_success
        account = orchestrator._storage.get_account("etoro_alice")
        assert account.display_name == "Alice"
        assert account.perf_summary == {}
        unknown = asyncio.run(orchestrator.collect_account("unknown", "x"))
        assert not unknown.is_success
        assert orchestrator.get_failure_summary()["parse_error"] == 1
    finally:
        orchestrator.close()


def test_all_builtin_collectors_construct_without_network():
    for platform in ("darwinex", "etoro", "zulutrade", "jisilu", "joinquant", "bigquant", "xueqiu"):
        assert orchestration.get_collector(platform).platform_name == platform


class _FakeAuthenticatedBrowserCollector:
    def __init__(self, session):
        self.session = session

    async def check_login(self):
        return self.session.get("logged_in", False)

    async def fetch_portfolio(self, portfolio_code, author_id, author_name):
        return PortfolioSnapshot(
            snapshot_id="snap-1",
            source=PortfolioSource.XUEQIU,
            author_id=author_id,
            author_name=author_name,
            portfolio_code=portfolio_code,
            snapshot_time=datetime(2026, 10, 17),
            fetch_time=datetime(2026, 10, 17),
            holdings=(Holding("600519.SH", "贵州茅台", Decimal("0.25")),),
        )


def test_authenticated_xueqiu_adapter_preserves_holdings_without_inventing_returns():
    adapter = XueqiuAuthenticatedCollector(
        {"logged_in": True},
        ["ZH123"],
        author_id="author-1",
        author_name="测试作者",
        collector_cls=_FakeAuthenticatedBrowserCollector,
    )
    result = asyncio.run(adapter.fetch_account_info("6342158729"))

    assert result.success
    assert result.failure_type == FailureType.NONE
    assert result.evidence_quality == EvidenceQuality.PARTIAL
    assert result.perf_summary == {}
    assert result.holdings[0]["symbol"] == "600519.SH"
    assert result.holdings[0]["portfolio_code"] == "ZH123"
    assert result.data_sources == {"holdings:ZH123": "https://xueqiu.com/P/ZH123"}


def test_authenticated_xueqiu_adapter_rejects_missing_login_without_fetching():
    class NeverFetch(_FakeAuthenticatedBrowserCollector):
        async def fetch_portfolio(self, *args, **kwargs):
            raise AssertionError("portfolio must not be fetched while logged out")

    adapter = XueqiuAuthenticatedCollector(
        {"logged_in": False}, ["ZH123"], collector_cls=NeverFetch
    )
    result = asyncio.run(adapter.fetch_account_info("u1"))

    assert not result.success
    assert result.failure_type == FailureType.LOGIN_REQUIRED
    assert result.holdings is None


def test_authenticated_xueqiu_adapter_requires_explicit_session():
    with pytest.raises(ValueError, match="BrowserSession"):
        XueqiuAuthenticatedCollector(None, ["ZH123"])


def test_authenticated_xueqiu_result_is_persisted_as_evidence(tmp_path):
    adapter = XueqiuAuthenticatedCollector(
        {"logged_in": True},
        ["ZH123"],
        author_id="author-1",
        author_name="测试作者",
        collector_cls=_FakeAuthenticatedBrowserCollector,
    )
    orchestrator = orchestration.CollectionOrchestrator(
        str(tmp_path), collector_factory=lambda platform: adapter
    )
    try:
        snapshot = asyncio.run(orchestrator.collect_account("xueqiu", "uid-1"))
        assert snapshot.is_success
        assert snapshot.platform == "xueqiu"
        assert snapshot.parsed_fields["perf_summary"] == {}
        assert snapshot.parsed_fields["holdings"][0]["portfolio_code"] == "ZH123"

        stored = orchestrator._evidence.get_history("xueqiu_uid-1", limit=1)
        assert len(stored) == 1
        assert stored[0].parsed_fields["holdings"][0]["symbol"] == "600519.SH"
    finally:
        orchestrator.close()
