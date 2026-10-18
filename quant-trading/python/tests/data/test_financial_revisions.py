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
