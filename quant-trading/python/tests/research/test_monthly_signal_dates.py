from datetime import date

import pandas as pd

from aipha.research.pipeline import build_target_positions, monthly_rebalance_dates


def test_partial_prediction_month_does_not_invent_an_early_rebalance():
    dates = pd.bdate_range("2024-01-01", "2024-02-15")
    metadata = {"listing_calendar": {"end_date": "2024-02-15", "open_sessions": dates.strftime("%Y-%m-%d").tolist()}}
    signals = monthly_rebalance_dates(metadata)
    assert signals == [date(2024, 1, 31)]
    scores = pd.DataFrame({"trade_date": ["2024-01-15", "2024-01-31", "2024-02-15"],
                           "symbol": "600000.SH", "score": 1.})
    targets = build_target_positions(scores, rebalance_dates=signals)
    assert [target.rebalance_date for target in targets] == [date(2024, 1, 31)]
    # A label horizon removes Jan 31 from predictions. Jan 15 cannot stand in.
    assert build_target_positions(scores.iloc[[0, 2]], rebalance_dates=signals) == []


def test_month_end_uses_explicit_open_sessions_including_closures():
    metadata = {"listing_calendar": {"end_date": "2024-02-15", "open_sessions": ["2024-01-29", "2024-01-30", "2024-02-01"]}}
    assert monthly_rebalance_dates(metadata) == [date(2024, 1, 30)]
    assert monthly_rebalance_dates({}) is None  # standalone compatibility only
