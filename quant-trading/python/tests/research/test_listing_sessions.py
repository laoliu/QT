from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from aipha.research import ResearchConfig, ResearchPipeline, load_research_config
from aipha.universe.historical import (
    LISTING_DAYS_BASIS, filter_historical_universe, listing_session_counts,
)


def _calendar(start="2023-01-03", end="2024-01-05"):
    days = pd.date_range(start, end)
    return pd.DataFrame({
        "cal_date": days, "is_open": ((days.dayofweek < 5) & (days != pd.Timestamp("2024-01-01"))).astype(int),
    })


def _universe():
    return pd.DataFrame({
        "trade_date": pd.to_datetime(["2024-01-03"] * 4),
        "symbol": ["new", "boundary", "under_custom", "custom"],
        "is_index_member": True, "is_st": False, "is_suspended": False,
        "listing_days": [251, 252, 299, 300], "listing_days_basis": LISTING_DAYS_BASIS,
        "feature_version": "cn_equity_v1", "factor": [1., 2., 3., 4.],
        "fwd_return": [0.01, 0.02, 0.03, 0.04], "fwd_return_rank": [1, 2, 3, 4],
    })


def test_listing_age_counts_exchange_sessions_before_tape_and_ignores_missing_stock_bars():
    calendar = _calendar()
    sessions = calendar.loc[calendar.is_open.eq(1), "cal_date"].reset_index(drop=True)
    # The research tape contains only two issuer observations, with a gap.
    # Its age still includes all exchange sessions since listing.
    dates = pd.Series([sessions[250], sessions[252]], index=[7, 9])
    ages = listing_session_counts(dates, pd.Series(["2023-01-03"] * 2, index=[7, 9]), calendar)
    assert ages.to_dict() == {7: 251, 9: 253}


def test_weekends_and_exchange_holidays_are_not_listing_sessions():
    calendar = _calendar("2023-12-29", "2024-01-03")
    ages = listing_session_counts(pd.Series(["2024-01-02", "2024-01-03"]),
                                  pd.Series(["2023-12-29"] * 2), calendar)
    assert ages.tolist() == [2, 3]
    extended = pd.concat([calendar, _calendar("2024-01-04", "2024-01-05")], ignore_index=True)
    pd.testing.assert_series_equal(ages, listing_session_counts(
        pd.Series(["2024-01-02", "2024-01-03"]), pd.Series(["2023-12-29"] * 2), extended,
    ))


@pytest.mark.parametrize("problem", ["missing_holiday", "truncated", "duplicates", "invalid_open", "wrong_exchange"])
def test_incomplete_or_ambiguous_calendar_cannot_approve_a_stock(problem):
    calendar = _calendar("2023-12-29", "2024-01-03")
    if problem == "missing_holiday":
        calendar = calendar[calendar.cal_date.ne("2024-01-01")]
    elif problem == "truncated":
        calendar = calendar.iloc[1:]
    elif problem == "duplicates":
        calendar = pd.concat([calendar, calendar.iloc[:1]])
    elif problem == "invalid_open":
        calendar.loc[0, "is_open"] = 2
    else:
        calendar["exchange"] = "OTHER"
    with pytest.raises(ValueError, match="trade calendar"):
        listing_session_counts(pd.Series(["2024-01-03"]), pd.Series(["2023-12-29"]), calendar)


@pytest.mark.parametrize("listed,day", [(None, "2024-01-03"), ("2024-01-04", "2024-01-03"),
                                         ("2023-12-29", "2024-01-01")])
def test_invalid_listing_or_closed_market_date_rejected(listed, day):
    with pytest.raises(ValueError):
        listing_session_counts(pd.Series([day]), pd.Series([listed]), _calendar())


def test_exact_threshold_and_string_false_flags_are_shared_by_all_callers():
    frame = _universe().assign(is_suspended="false", is_st="0", is_index_member="true")
    assert filter_historical_universe(frame).symbol.tolist() == ["boundary", "under_custom", "custom"]
    assert filter_historical_universe(frame, min_listing_days=300).symbol.tolist() == ["custom"]


@pytest.mark.parametrize("basis", [None, "calendar_days"])
def test_old_snapshot_age_requires_rebuilding(basis):
    frame = _universe()
    frame = frame.drop(columns="listing_days_basis") if basis is None else frame.assign(listing_days_basis=basis)
    with pytest.raises(ValueError, match="listing_days"):
        filter_historical_universe(frame)


@pytest.mark.parametrize("threshold", [251, 252.5, 300.0, True, "300"])
def test_listing_threshold_is_strict_integer_policy(threshold):
    with pytest.raises(ValueError, match="min_listing_days"):
        ResearchConfig(min_listing_days=threshold)


def test_yaml_threshold_is_kept_and_changes_reproducibility_hash(tmp_path):
    path = tmp_path / "research.yaml"
    path.write_text("data:\n  provider: tushare_pit\nuniverse:\n  min_listing_days: 300\n", encoding="utf-8")
    cfg = load_research_config(path)
    assert cfg.min_listing_days == 300
    assert cfg.model_config_hash() != replace(cfg, min_listing_days=252).model_config_hash()


def test_training_passes_only_the_configured_historical_universe(monkeypatch):
    from aipha.models.ml_stock_selector import MLStockSelector

    observed = {}
    def train(self, frame):
        observed["symbols"] = frame.symbol.tolist()
        return frame
    monkeypatch.setattr(MLStockSelector, "walk_forward_train", train)
    monkeypatch.setattr(MLStockSelector, "get_wf_summary", lambda self: {})
    ResearchPipeline(ResearchConfig(min_listing_days=300)).train_oos(_universe())
    assert observed["symbols"] == ["custom"]


def test_predict_bridge_applies_same_configured_age_before_ranking(monkeypatch):
    from aipha.engines import aipha_ml_engine as engine
    import aipha.research

    monkeypatch.setattr(engine, "_load_config", lambda _: ResearchConfig(min_listing_days=300))
    monkeypatch.setattr(engine, "_read_parquet", lambda _: _universe())
    calls = []
    def predict(frame):
        calls.append(frame.symbol.tolist())
        return pd.Series(np.ones(len(frame)), index=frame.index)
    monkeypatch.setattr(aipha.research, "load_verified_selector", lambda *a, **k: SimpleNamespace(predict=predict))
    result = engine.run({"action": "predict", "modelPath": "fixture", "featurePath": "fixture"})
    assert result["ok"] is True
    assert calls == [["custom"]]
    assert [row["symbol"] for row in result["data"]["rankings"]] == ["custom"]
