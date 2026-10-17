import asyncio

from aipha.tracker.collectors.darwinex import DarwinexCollector


HTML = """
<html><div class='header-main__darwin-name'>THA</div>
<span class='js-return-total'>834.21</span>
<span class='js-return-annualized'>20.47</span>
<span>Return 12 years</span></html>
"""


def test_darwinex_public_performance_is_parsed_with_identity_check():
    def fetch(url):
        return 200, HTML, "https://www.darwinex.com/invest/THA"

    result = asyncio.run(DarwinexCollector(fetch).fetch_account_info("THA"))
    assert result.success
    assert result.perf_summary == {
        "cumulative_return_pct": 834.21,
        "annualized_return_pct": 20.47,
        "holding_period_label": "12 years",
    }
    assert result.holdings is None
    assert result.data_sources["performance"].endswith("/invest/THA")


def test_darwinex_wrong_redirect_does_not_become_metrics():
    def fetch(url):
        return 200, HTML, "https://www.darwinex.com/invest/OTHER"

    result = asyncio.run(DarwinexCollector(fetch).fetch_account_info("THA"))
    assert not result.success
    assert not result.perf_summary


def test_darwinex_malformed_metric_is_rejected():
    def fetch(url):
        return 200, HTML.replace("834.21", "not-a-number"), "https://www.darwinex.com/invest/THA"

    result = asyncio.run(DarwinexCollector(fetch).fetch_account_info("THA"))
    assert not result.success
    assert not result.perf_summary
