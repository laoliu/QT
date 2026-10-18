"""Source-calendar coverage, including gaps no per-symbol check can detect."""
from copy import deepcopy

import pandas as pd
import pytest

from aipha.research.pipeline import ResearchConfig, ResearchPipeline, _load_snapshot_metadata
from aipha.research.snapshots import (
    SnapshotSpec, assemble_tushare_snapshot, attest_snapshot_metadata,
    validate_market_calendar, verify_snapshot_signature,
)


def _metadata(sessions=None):
    return {"provider": "tushare_pit", "feature_version": "cn_equity_v1", "index_code": "000905.SH",
            "data_snapshot_id": "calendar-fixture", "start_date": "2024-01-30", "end_date": "2024-02-05",
            "listing_calendar": {"provider": "tushare", "endpoint": "trade_cal", "exchange": "SSE",
                                 "start_date": "2024-01-01", "end_date": "2024-02-05",
                                 "open_sessions": sessions if sessions is not None else
                                     ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02", "2024-02-05"]}}


def _frame(days=None):
    return pd.DataFrame([
        {"trade_date": day, "symbol": symbol, "open": 10., "high": 10., "low": 10., "close": 10.,
         "volume": 1000., "amount": 10000., "adj_factor": 1., "price_adjustment": "point_in_time_factor",
         "feature_version": "cn_equity_v1", "is_index_member": True, "is_st": False, "is_suspended": False,
         "is_limit_up": False, "is_limit_down": False, "listing_days": 400, "listing_days_basis": "exchange_open_sessions"}
        for day in (days if days is not None else _metadata()["listing_calendar"]["open_sessions"])
        for symbol in ("600000.SH", "600001.SH")
    ])


@pytest.mark.parametrize("day", ["2024-01-30", "2024-02-01", "2024-02-05"])
def test_training_rejects_missing_entire_exchange_session_before_fitting(tmp_path, monkeypatch, day):
    frame = _frame()
    frame = frame.loc[frame.trade_date.ne(day)].copy()
    frame.attrs["snapshot_metadata"] = attest_snapshot_metadata(frame, _metadata())
    pipeline = ResearchPipeline(ResearchConfig())
    monkeypatch.setattr(pipeline, "train_oos", lambda *a: pytest.fail("trained on an incomplete market calendar"))
    with pytest.raises(ValueError, match="do not match trade_cal"):
        pipeline.run(frame, tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_explicit_source_closure_is_valid_without_weekday_inference():
    # An intentional synthetic weekday closure; these are fixtures, not a
    # representation of the actual SSE 2024 calendar.
    sessions = ["2024-01-30", "2024-01-31", "2024-02-02", "2024-02-05"]
    frame = _frame(sessions)
    assert validate_market_calendar(frame, _metadata(sessions)) == sessions
    frame.attrs["snapshot_metadata"] = attest_snapshot_metadata(frame, _metadata(sessions))
    assert _load_snapshot_metadata(frame)["listing_calendar"]["open_sessions"] == sessions


@pytest.mark.parametrize("change", [
    {"open_sessions": []}, {"open_sessions": ["2024-01-31", "2024-01-30"]},
    {"open_sessions": ["2024-01-30", "2024-01-30"]}, {"open_sessions": [None]},
    {"open_sessions": ["20240130"]}, {"open_sessions": ["2024-01-30T00:00:00"]},
    {"open_sessions": ["2023-12-31"]}, {"exchange": "OTHER"},
    {"start_date": "2024-01-31"}, {"end_date": "2024-02-01"},
])
def test_invalid_calendar_contract_fails_closed(change):
    metadata = _metadata()
    metadata["listing_calendar"].update(change)
    with pytest.raises(ValueError):
        validate_market_calendar(_frame(), metadata)


def test_missing_calendar_and_signed_calendar_tampering_are_rejected():
    frame = _frame()
    metadata = _metadata()
    del metadata["listing_calendar"]
    frame.attrs["snapshot_metadata"] = attest_snapshot_metadata(frame, metadata)
    with pytest.raises(ValueError, match="rebuild legacy snapshots"):
        _load_snapshot_metadata(frame)
    metadata = attest_snapshot_metadata(frame, _metadata())
    changed = deepcopy(metadata)
    changed["listing_calendar"]["open_sessions"].remove("2024-02-01")
    with pytest.raises(ValueError, match="signature"):
        verify_snapshot_signature(changed)


@pytest.mark.parametrize("missing", [None, "2024-01-30", "2024-02-01", "2024-02-05"])
def test_tushare_builder_proves_requested_range_including_edges(missing):
    frame = _frame()
    if missing:
        frame = frame.loc[frame.trade_date.ne(missing)]
    raw = frame.rename(columns={"symbol": "ts_code", "volume": "vol"})[
        ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount", "adj_factor"]]
    calendar_days = pd.date_range("2024-01-01", "2024-02-05")
    # Explicit calendar fixture supplied by the provider; no production
    # calendar is reconstructed from the incomplete market frame.
    source_calendar = pd.DataFrame({"cal_date": calendar_days, "is_open": calendar_days.dayofweek < 5})
    kwargs = dict(daily_basic=None, stock_basic=pd.DataFrame({"ts_code": ["600000.SH", "600001.SH"],
                                                            "list_date": ["20240101", "20240101"]}),
                  index_snapshots={"20240101": ["600000.SH", "600001.SH"]},
                  st_status=frame.rename(columns={"symbol": "ts_code"})[["trade_date", "ts_code", "is_st"]],
                  limit_prices=raw[["trade_date", "ts_code"]].assign(up_limit=11., down_limit=9.),
                  trade_calendar=source_calendar, spec=SnapshotSpec("20240130", "20240205"))
    if missing:
        with pytest.raises(ValueError, match="do not match trade_cal"):
            assemble_tushare_snapshot(raw, **kwargs)
    else:
        snapshot = assemble_tushare_snapshot(raw, **kwargs)
        assert snapshot.metadata["generator_version"].endswith("/4")
        assert "2024-02-01" in snapshot.metadata["listing_calendar"]["open_sessions"]
        assert validate_market_calendar(snapshot.panel, snapshot.metadata) == _metadata()["listing_calendar"]["open_sessions"]

