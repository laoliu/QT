"""Financial publication timing and report-period selection across the PIT chain."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from aipha.data.pit_financial import FinancialIndicator, InMemoryPITProvider, ReportDate, ReportPeriod
from aipha.data.tushare_pit_provider import TuSharePITProvider
from aipha.research.snapshots import financial_snapshots_from_provider, join_pit_financials


def _history():
    return pd.DataFrame([
        {"end_date": "20240331", "ann_date": "20240425", "f_ann_date": "20240502", "revenue": 100., "report_type": "1"},
        {"end_date": "20240331", "ann_date": "20240425", "f_ann_date": "20240506", "revenue": 110., "report_type": "4"},
        {"end_date": "20240630", "ann_date": "20240715", "f_ann_date": "20240720", "revenue": 200., "report_type": "1"},
        {"end_date": "20240331", "ann_date": "20240801", "f_ann_date": "20240805", "revenue": 999., "report_type": "4"},
        {"end_date": "20240930", "ann_date": "20241020", "f_ann_date": "20241101", "revenue": 300., "report_type": "1"},
    ]).assign(ts_code="600000.SH")


class RawProvider:
    def __init__(self, frame):
        self.frame = frame

    def load_fina_indicator(self, symbol):
        return self.frame.rename(columns={"revenue": "roe"})

    def load_income(self, symbol):
        return self.frame.copy()

    def load_balancesheet(self, symbol):
        return pd.DataFrame()

    def load_cashflow(self, symbol):
        return pd.DataFrame()


@pytest.mark.parametrize("query,expected", [
    (date(2024, 5, 1), None), (date(2024, 5, 2), 100), (date(2024, 5, 5), 100),
    (date(2024, 5, 6), 110), (date(2024, 7, 19), 110), (date(2024, 7, 20), 200),
    (date(2024, 8, 6), 200), (date(2024, 10, 31), 200), (date(2024, 11, 1), 300),
])
def test_actual_publication_and_latest_report_period(query, expected):
    provider = TuSharePITProvider(RawProvider(_history().sample(frac=1., random_state=2)))
    statement = provider.get_statement_pit("600000.SH", query, "income")
    indicator = provider.get_indicator_pit("600000.SH", query, ["roe"])
    if expected is None:
        assert statement is indicator is None
    else:
        assert statement.get("revenue") == Decimal(expected)
        assert indicator.indicators["roe"] == Decimal(expected)
        assert statement.announce_date <= query
        assert not {"f_ann_date", "report_type", "ann_date"} & set(statement.data)


def test_statement_history_keeps_actual_announcement_and_late_old_revision():
    provider = TuSharePITProvider(RawProvider(_history()))
    history = provider.get_statement_history("600000.SH", date(2024, 5, 1), date(2024, 8, 31), "income")
    assert [row.announce_date for row in history] == [date(2024, 5, 2), date(2024, 5, 6), date(2024, 7, 20), date(2024, 8, 5)]
    # Current-value selection does not delete the old revision from the history.
    assert history[-1].get("revenue") == Decimal(999)


def test_materialization_retains_old_facts_seeds_window_and_filters_future():
    facts = financial_snapshots_from_provider(
        TuSharePITProvider(RawProvider(_history())), ["600000.SH"],
        start_date=date(2024, 7, 1), end_date=date(2024, 8, 31),
    )
    assert facts.announce_date.min() == date(2024, 5, 2)
    assert facts.announce_date.max() == date(2024, 8, 5)
    panel = pd.DataFrame({
        "trade_date": ["2024-08-06", "2024-05-01", "2024-07-01", "2024-05-02", "2024-05-06", "2024-07-20"],
        "symbol": ["600000.SH"] * 6,
    }, index=[4, 3, 2, 1, 5, 0])
    output = join_pit_financials(panel, facts)
    pd.testing.assert_frame_equal(output[panel.columns], panel)
    assert output.revenue.iloc[[0, 2, 3, 4, 5]].tolist() == [200., 110., 100., 110., 200.]
    assert pd.isna(output.revenue.iloc[1])
    assert output.roe.equals(output.revenue)
    revised = facts.copy()
    revised.loc[revised.announce_date > date(2024, 7, 1), "value"] *= 10
    before = panel.loc[pd.to_datetime(panel.trade_date) <= "2024-07-01"]
    pd.testing.assert_frame_equal(join_pit_financials(before, facts), join_pit_financials(before, revised))


def test_parent_and_single_quarter_filings_do_not_replace_consolidated():
    base = _history().iloc[:1]
    other = base.assign(report_type="6", f_ann_date="20240507", revenue=1.)
    quarter = base.assign(report_type="2", f_ann_date="20240508", revenue=2.)
    provider = TuSharePITProvider(RawProvider(pd.concat([base, other, quarter])))
    assert provider.get_statement_pit("600000.SH", date(2024, 5, 10), "income").get("revenue") == Decimal(100)


@pytest.mark.parametrize("changes,error", [
    ({"f_ann_date": "bad-date"}, "f_ann_date"),
    ({"end_date": "20240215"}, "quarter end"),
    ({"ann_date": "20240101", "f_ann_date": "20240102"}, "precede report"),
    ({"report_type": None}, "report_type"),
])
def test_invalid_source_financial_dates_and_form_fail(changes, error):
    provider = TuSharePITProvider(RawProvider(_history().iloc[:1].assign(**changes)))
    with pytest.raises(ValueError, match=error):
        provider.get_statement_pit("600000.SH", date(2024, 12, 31), "income")


@pytest.mark.parametrize("actual", [None, "", "20240420"])
def test_null_or_earlier_actual_date_does_not_advance_nominal_publication(actual):
    provider = TuSharePITProvider(RawProvider(_history().iloc[:1].assign(f_ann_date=actual)))
    assert provider.get_statement_pit("600000.SH", date(2024, 4, 24), "income") is None
    assert provider.get_statement_pit("600000.SH", date(2024, 4, 25), "income").announce_date == date(2024, 4, 25)


def test_same_day_multiple_periods_are_order_independent_and_conflicts_fail():
    frame = _history().iloc[:3].assign(ann_date="20240720", f_ann_date="20240720")
    # Conflicting same-period same-date values cannot be ordered reliably.
    with pytest.raises(ValueError, match="conflicting financial revisions"):
        TuSharePITProvider(RawProvider(frame)).get_statement_pit("600000.SH", date(2024, 7, 20), "income")
    frame = frame.iloc[[0, 2]].sample(frac=1., random_state=3)
    provider = TuSharePITProvider(RawProvider(frame))
    assert provider.get_indicator_pit("600000.SH", date(2024, 7, 20), ["roe"]).indicators["roe"] == Decimal(200)
    facts = financial_snapshots_from_provider(provider, ["600000.SH"])
    panel = pd.DataFrame({"trade_date": ["2024-07-20"], "symbol": ["600000.SH"]})
    assert join_pit_financials(panel, facts).revenue.iloc[0] == 200.
    conflict = pd.concat([facts, facts.iloc[:1].assign(value=9999.)])
    with pytest.raises(ValueError, match="conflicting PIT"):
        join_pit_financials(panel, conflict)


def test_multiple_symbols_and_absent_financial_history_keep_market_row_identity():
    facts = financial_snapshots_from_provider(TuSharePITProvider(RawProvider(_history())), ["600000.SH"])
    panel = pd.DataFrame({"trade_date": ["2024-07-20"] * 3, "symbol": ["missing", "600000.SH", "other"]}, index=[7, 1, 8])
    output = join_pit_financials(panel, pd.concat([facts, facts]))
    pd.testing.assert_frame_equal(output[panel.columns], panel)
    assert output.revenue.iloc[1] == 200.
    assert output.revenue.iloc[[0, 2]].isna().all()
    assert join_pit_financials(panel.head(0), facts).empty
    with pytest.raises(ValueError, match="overwrite"):
        join_pit_financials(panel.assign(revenue=0.), facts)


def test_in_memory_provider_agrees_on_latest_visible_period_after_old_revision():
    provider = InMemoryPITProvider()
    provider.add_indicator(FinancialIndicator("x", ReportDate(2024, ReportPeriod.Q2), date(2024, 7, 20), {"roe": Decimal(20)}))
    provider.add_indicator(FinancialIndicator("x", ReportDate(2024, ReportPeriod.Q1), date(2024, 8, 20), {"roe": Decimal(99)}))
    assert provider.get_indicator_pit("x", date(2024, 9, 1), ["roe"]).indicators["roe"] == Decimal(20)


def test_quarantine_masks_only_conflicting_metric_and_inserts_null_event():
    frame = pd.DataFrame([
        {"end_date": "20231231", "ann_date": "20240401", "revenue": 80., "roa": 3.},
        {"end_date": "20240331", "ann_date": "20240425", "revenue": 100., "roa": 4.},
        {"end_date": "20240331", "ann_date": "20240425", "revenue": 110., "roa": 4.},
        {"end_date": "20240331", "ann_date": "20240425", "revenue": 100., "roa": 4.},
        {"end_date": "20240630", "ann_date": "20240720", "revenue": 200., "roa": 5.},
    ]).assign(ts_code="600000.SH", f_ann_date=None, report_type="1")
    before = frame.copy(deep=True)
    provider = TuSharePITProvider(RawProvider(frame), conflict_policy="quarantine")
    facts = financial_snapshots_from_provider(provider, ["600000.SH"])
    panel = pd.DataFrame({"trade_date": ["2024-04-24", "2024-04-25", "2024-06-01", "2024-07-20"],
                          "symbol": ["600000.SH"] * 4})
    output = join_pit_financials(panel, facts)
    assert output.roe.iloc[0] == 80.
    assert output.roe.iloc[1:3].isna().all()  # cannot forward-fill 80 or choose 100/110
    assert output.revenue.iloc[1:3].isna().all()
    assert output.roe.iloc[3] == 200.
    assert output.roa.tolist() == [3., 4., 4., 5.]
    assert len(provider.get_indicator_history("600000.SH")) == 3
    quality = provider.get_data_quality_report()
    assert quality["policy"] == "quarantine"
    assert quality["quarantined_metric_events"] == 2  # indicator and income scopes
    assert quality["affected_symbols"] == 1
    assert quality["events"][0]["source_values"] == ["100.0", "110.0"]
    assert provider.get_data_quality_report(end_date=date(2024, 4, 24))["events"] == []
    assert provider.get_data_quality_report() == quality  # cache reads do not duplicate audit events
    pd.testing.assert_frame_equal(frame, before)
    other = TuSharePITProvider(RawProvider(frame.sample(frac=1., random_state=7)), conflict_policy="quarantine")
    pd.testing.assert_frame_equal(financial_snapshots_from_provider(other, ["600000.SH"]), facts)
    assert other.get_data_quality_report() == quality
    quality["events"][0]["source_values"].append("tampered")
    assert "tampered" not in provider.get_data_quality_report()["events"][0]["source_values"]


def test_quarantine_keeps_missing_versus_present_conflict_missing():
    frame = _history().iloc[:1].assign(revenue=100., roa=1.)
    duplicate = frame.assign(roa=None)
    provider = TuSharePITProvider(RawProvider(pd.concat([frame, duplicate])), conflict_policy="quarantine")
    indicator = provider.get_indicator_pit("600000.SH", date(2024, 6, 1), ["roe", "roa"])
    assert indicator.indicators == {"roe": Decimal("100.0"), "roa": None}
    assert provider.get_data_quality_report()["by_metric"] == {"fina_indicator.roa": 1}


def test_invalid_quarantine_policy_is_rejected():
    with pytest.raises(ValueError, match="conflict_policy"):
        TuSharePITProvider(RawProvider(_history()), conflict_policy="last")


def test_bounded_history_uses_distinct_provider_cache_requests():
    class BoundedRaw(RawProvider):
        def load_fina_indicator(self, symbol, **kwargs):
            assert kwargs == {"start_date": "20210101", "end_date": "20241231"}
            return super().load_fina_indicator(symbol)
        def load_income(self, symbol, **kwargs):
            assert kwargs == {"start_date": "20210101", "end_date": "20241231"}
            return super().load_income(symbol)
    provider = TuSharePITProvider(BoundedRaw(_history()), history_start="20210101", history_end="20241231")
    assert provider.get_indicator_history("600000.SH")
    assert provider.get_statement_history("600000.SH", date.min, date.max, "income")
    assert provider.get_data_quality_report()["history_request"] == {"start_date": "20210101", "end_date": "20241231"}


@pytest.mark.parametrize("start,end", [("20241301", None), ("2024-01-01", None), (12345678, None), ("20240101", "20230101")])
def test_invalid_financial_history_bounds_fail_before_io(start, end):
    with pytest.raises(ValueError, match="financial history"):
        TuSharePITProvider(RawProvider(_history()), history_start=start, history_end=end)


def test_quality_disclosure_is_signed_and_legacy_signatures_stay_verifiable():
    from aipha.research.snapshots import sign_snapshot_metadata, verify_snapshot_signature
    metadata = dict(provider="tushare_pit", data_snapshot_id="fixture", feature_version="v1",
                    start_date="2024-01-01", end_date="2024-07-20", snapshot_schema_version=1,
                    generator_version="fixture", snapshot_content_sha256="a" * 64, row_count=1, column_count=1)
    key = b"quarantine-signature-test-key-12345"
    legacy = sign_snapshot_metadata(metadata, key=key)
    verify_snapshot_signature(legacy, key=key)
    report = TuSharePITProvider(RawProvider(_history()), conflict_policy="quarantine").get_data_quality_report()
    current = sign_snapshot_metadata({**metadata, "financial_data_quality": report}, key=key)
    verify_snapshot_signature(current, key=key)
    current["financial_data_quality"]["policy"] = "raise"
    with pytest.raises(ValueError, match="signature"):
        verify_snapshot_signature(current, key=key)
