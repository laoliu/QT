"""Operational evidence must come from daily commits, not replayed NAV rows."""
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3

import pandas as pd
import pytest

from aipha.research import paper_book
from aipha.research.paper_monitor import inspect_paper_book, inspect_paper_books
from aipha.research.pipeline import ResearchConfig
from aipha.research.snapshots import attest_snapshot_metadata


# Synthetic calendar intentionally contains a weekday closure.
DAYS = ["2024-01-30", "2024-01-31", "2024-02-02"]
NOW = datetime.fromisoformat("2024-02-02T17:00:00+08:00")


def _snapshot(end):
    frame = pd.DataFrame([
        dict(trade_date=day, symbol="600000.SH", open=10., high=10., low=10., close=10.,
             volume=1000., amount=10000., adj_factor=1., price_adjustment="point_in_time_factor",
             feature_version="cn_equity_v1", is_suspended=False, is_limit_up=False, is_limit_down=False,
             is_st=False, is_index_member=True, listing_days=400, listing_days_basis="exchange_open_sessions")
        for day in DAYS if day <= end
    ])
    frame.attrs["snapshot_metadata"] = attest_snapshot_metadata(frame, {
        "provider": "tushare_pit", "feature_version": "cn_equity_v1", "data_snapshot_id": f"monitor-fixture:{end}",
        "start_date": DAYS[0], "end_date": end, "index_code": "000905.SH",
        "listing_calendar": {"provider": "tushare", "endpoint": "trade_cal", "exchange": "SSE",
                             "start_date": DAYS[0], "end_date": DAYS[-1], "open_sessions": DAYS},
    })
    return frame


def _book(tmp_path, monkeypatch, *, commit_days=DAYS, timestamps=None):
    book = tmp_path / "paper" / "equity.sqlite3"
    for day in commit_days:
        stamp = (timestamps or {}).get(day, day + "T16:00:00+08:00")
        monkeypatch.setattr(paper_book, "_commit_time", lambda value=stamp: value)
        paper_book.advance_paper_book(book, _snapshot(day), ResearchConfig(), commit=True)
    return book


def test_daily_timely_commits_and_explicit_holiday_pass_without_writing(tmp_path, monkeypatch):
    book = _book(tmp_path, monkeypatch)
    before = book.read_bytes()
    report = inspect_paper_book(book, now=NOW, required_days=3)
    assert report["passed"] and report["status"] == "ok"
    assert report["total_valid_days"] == report["consecutive_valid_days"] == 3
    assert report["longest_streak"] == 3 and not report["gaps"]
    assert report["age_hours"] == 1 and report["latest_trade_date"] == DAYS[-1]
    assert book.read_bytes() == before


def test_backfilled_nav_rows_do_not_become_daily_operation_evidence(tmp_path, monkeypatch):
    book = _book(tmp_path, monkeypatch, timestamps={day: f"2024-02-02T16:00:0{i}+08:00" for i, day in enumerate(DAYS)})
    report = inspect_paper_book(book, now=NOW, required_days=3)
    assert not report["passed"]
    assert len(report["nav_history"]) == 3
    assert report["total_valid_days"] == 1 and report["consecutive_valid_days"] == 1
    assert [row["reason"] for row in report["invalid_days"]] == ["historical_backfill", "historical_backfill"]


def test_skipped_commit_breaks_streak_even_with_complete_replayed_market(tmp_path, monkeypatch):
    book = _book(tmp_path, monkeypatch, commit_days=[DAYS[0], DAYS[-1]])
    report = inspect_paper_book(book, now=NOW, required_days=2)
    assert not report["passed"] and report["total_valid_days"] == 2
    assert report["consecutive_valid_days"] == 1
    assert report["invalid_days"] == [{"date": DAYS[1], "reason": "missing_daily_commit"}]


def test_touching_or_copying_old_book_does_not_refresh_its_commit(tmp_path, monkeypatch):
    book = _book(tmp_path, monkeypatch)
    os.utime(book, None)
    report = inspect_paper_book(book, now=NOW + timedelta(days=3), required_days=3)
    assert report["status"] == "stale" and not report["passed"]
    assert report["age_hours"] == 73


@pytest.mark.parametrize("stamp,reason", [
    (None, "missing_or_invalid_commit_timestamp"), ("invalid", "missing_or_invalid_commit_timestamp"),
    ("2024-02-02T16:00:00", "missing_or_invalid_commit_timestamp"),
    ("2024-02-02T14:59:59+08:00", "committed_before_signal_close"),
    ("2024-02-02T18:00:00+08:00", "future_commit_timestamp"),
])
def test_unverified_or_noncausal_commit_times_do_not_count(tmp_path, monkeypatch, stamp, reason):
    book = _book(tmp_path, monkeypatch, timestamps={DAYS[-1]: stamp})
    report = inspect_paper_book(book, now=NOW, required_days=1)
    assert not report["passed"] and report["status"] == "invalid"
    assert report["consecutive_valid_days"] == 0
    assert report["invalid_days"][-1]["reason"] == reason


def test_old_receipts_without_timestamps_are_readable_but_unverified_for_s4(tmp_path, monkeypatch):
    book = _book(tmp_path, monkeypatch)
    with sqlite3.connect(book) as connection:
        for day, payload in connection.execute("SELECT day,payload FROM receipts").fetchall():
            receipt = json.loads(payload)
            receipt.pop("committed_at")
            connection.execute("UPDATE receipts SET payload=? WHERE day=?", (json.dumps(receipt), day))
    assert paper_book.replay_paper_book(book)["daily_returns"]
    report = inspect_paper_book(book, now=NOW, required_days=1)
    assert not report["passed"] and report["total_valid_days"] == 0


def test_preview_and_retry_do_not_create_new_operational_sessions(tmp_path, monkeypatch):
    book = _book(tmp_path, monkeypatch, commit_days=DAYS[:2])
    before = book.read_bytes()
    paper_book.advance_paper_book(book, _snapshot(DAYS[-1]), ResearchConfig())
    assert book.read_bytes() == before
    report = inspect_paper_book(book, now=NOW, required_days=3, threshold_hours=48)
    assert report["total_valid_days"] == 2 and not report["passed"]
    original = paper_book.advance_paper_book(book, _snapshot(DAYS[1]), ResearchConfig(), commit=True)
    assert original["committed_at"] == DAYS[1] + "T16:00:00+08:00"


@pytest.mark.parametrize("fault", ["receipt_nav", "signature", "missing_key"])
def test_bad_accounting_or_unverifiable_calendar_is_reported_as_failure(tmp_path, monkeypatch, fault):
    book = _book(tmp_path, monkeypatch)
    if fault == "missing_key":
        monkeypatch.delenv("AIPHA_PIT_SNAPSHOT_KEY")
    else:
        with sqlite3.connect(book) as connection:
            payload = json.loads(connection.execute("SELECT payload FROM receipts ORDER BY day DESC LIMIT 1").fetchone()[0])
            if fault == "receipt_nav":
                payload["nav"] += 1
            else:
                payload["calendar_metadata"]["source_signature"] = "bad"
            connection.execute("UPDATE receipts SET payload=? WHERE day=?", (json.dumps(payload), DAYS[-1]))
    before = book.read_bytes()
    report = inspect_paper_book(book, now=NOW, required_days=1)
    assert report["status"] == "invalid" and not report["passed"]
    assert "账本校验失败" in report["message"]
    assert not report["nav_history"] and book.read_bytes() == before


def test_ops_read_canonical_books_even_when_legacy_root_is_missing(tmp_path, monkeypatch):
    from scripts.ops.state_stale_checker import check_state_stale
    from scripts.ops.s4_validator import check_all_portfolios
    from scripts.ops.nav_monitor import run_nav_check
    from scripts.ops.daily_ops import _check_canonical_paper_books

    book = _book(tmp_path, monkeypatch)
    before = book.read_bytes()
    fresh, = check_state_stale(tmp_path / "missing", now=NOW, book_dir=book.parent)
    assert fresh["portfolio_id"] == "research/equity" and not fresh["stale"]
    s4, = check_all_portfolios(tmp_path / "missing", required_days=3, book_dir=book.parent, now=NOW)
    assert s4.passed and s4.source == "canonical_paper_book"
    assert not check_all_portfolios(tmp_path / "missing", portfolio_filter="other", book_dir=book.parent, now=NOW)
    nav, = run_nav_check(tmp_path / "missing", book_dir=book.parent)
    assert nav.passed and nav.current_nav == nav.baseline_nav == 1000000
    assert _check_canonical_paper_books(book.parent, now=NOW)["status"] == "OK"
    assert _check_canonical_paper_books(book.parent, now=NOW + timedelta(days=3))["status"] == "WARN"
    assert book.read_bytes() == before


def test_corrupt_book_is_not_skipped_by_ops_or_s4(tmp_path, monkeypatch, capsys):
    from scripts.ops import state_stale_checker, s4_validator, nav_monitor

    book = tmp_path / "broken.sqlite3"
    book.write_bytes(b"bad SQLite")
    row, = inspect_paper_books(book, now=NOW)
    assert row["status"] == "invalid"
    from scripts.ops.daily_ops import _check_canonical_paper_books

    assert _check_canonical_paper_books(book, now=NOW)["status"] == "FAIL"
    legacy = str(tmp_path / "missing")
    for module, option in ((state_stale_checker, "--state-dir"), (s4_validator, "--state-dir"), (nav_monitor, "--paper-state")):
        assert module.main([option, legacy, "--book-dir", str(book), "--json"]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert len(payload) == 1
    assert book.read_bytes() == b"bad SQLite"


def test_bad_policy_thresholds_fail_instead_of_passing_empty_evidence(tmp_path):
    for kwargs in ({"required_days": 0}, {"required_days": True}, {"threshold_hours": float("nan")}, {"max_daily_change": -1}):
        with pytest.raises(ValueError):
            inspect_paper_book(tmp_path / "missing", **kwargs)


def test_explicit_missing_book_is_a_failure_while_empty_directory_has_no_books(tmp_path):
    row, = inspect_paper_books(tmp_path / "missing.sqlite3", now=NOW)
    assert row["status"] == "invalid"
    assert inspect_paper_books(tmp_path / "missing-directory", now=NOW) == []
