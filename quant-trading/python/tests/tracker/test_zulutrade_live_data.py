import asyncio
import json

from aipha.tracker.collectors.zulutrade import ZuluTradeCollector


def _payload(provider="413997"):
    return [{"result": [{"trader": {
        "providerId": int(provider),
        "profile": {"name": "Public Leader", "strategyDesc": "trend", "public": True},
        "overallStats": {"roiProfit": 12.5, "roiAnnualized": 20.0,
                          "overallDrawDownPercent": 8.0, "followers": 4, "weeks": 12},
        "timeframeStats": {"180": {"profit": 10.0, "maxDrawDownPercent": 6.0,
                                    "timeFrameSharpeRatio": .8}},
    }}]}]


def _fetch(payload):
    def fetch(url):
        return 200, json.dumps(payload), url
    return fetch


def test_public_leader_statistics_are_parsed():
    result = asyncio.run(ZuluTradeCollector(_fetch(_payload())).fetch_account_info("413997"))
    assert result.success
    assert result.perf_summary["roi_profit_pct"] == 12.5
    assert result.perf_summary["drawdown_pct"] == 8.0
    assert result.perf_summary["timeframe"]["sharpe"] == .8
    assert result.data_sources["performance"].endswith("topTraders/search")
    assert result.holdings is None


def test_missing_provider_never_uses_another_leaders_metrics():
    result = asyncio.run(ZuluTradeCollector(_fetch(_payload("1"))).fetch_account_info("413997"))
    assert not result.success
    assert not result.perf_summary


def test_invalid_public_metric_is_rejected():
    payload = _payload()
    payload[0]["result"][0]["trader"]["overallStats"]["roiProfit"] = float("nan")
    result = asyncio.run(ZuluTradeCollector(_fetch(payload)).fetch_account_info("413997"))
    assert not result.success
    assert not result.perf_summary
