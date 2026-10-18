"""Offline continuity and accounting tests; no provider or broker connection."""
from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import subprocess
import sys
from copy import deepcopy

import pandas as pd
import pytest
import yaml

from aipha.research.backtest import run_portfolio_backtest
from aipha.research.contracts import TargetPosition
from aipha.research.corporate_actions import CorporateAction, attach_corporate_actions
from aipha.research.execution_plan import build_execution_plan, validate_execution_risk
from aipha.research.paper_book import advance_paper_book, replay_paper_book, upgrade_paper_book_calendar
from aipha.research.pipeline import ResearchConfig
from aipha.research.snapshots import attest_snapshot_metadata, point_in_time_adjust_prices, write_attested_parquet


@pytest.fixture
def policy():
    return ResearchConfig(initial_capital=100000, max_position_weight=1, max_industry_weight=1,
                          max_daily_turnover=2, slippage_bps=0, commission_rate=0,
                          commission_min=0, transfer_fee_rate=0, stamp_tax_rate=0)


def _market():
    return pd.DataFrame([
        dict(trade_date=day, symbol=symbol, open=10., high=12., low=9., close=11.,
             volume=10000., amount=100000., adj_factor=3., is_suspended=False,
             is_limit_up=False, is_limit_down=False, up_limit=100., down_limit=.01,
             is_index_member=True, is_st=False, listing_days=400,
             listing_days_basis="exchange_open_sessions", industry="bank",
             feature_version="cn_equity_v1")
        for day in pd.to_datetime(["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02", "2024-02-05"])
        for symbol in ("600000.SH", "600001.SH")
    ])


def _attest(frame):
    frame = frame.copy()
    frame.attrs["snapshot_metadata"] = attest_snapshot_metadata(frame, {
        "provider": "tushare_pit", "feature_version": "cn_equity_v1",
        "data_snapshot_id": "paper-fixture:" + str(pd.to_datetime(frame.trade_date).max().date()),
        "start_date": str(pd.to_datetime(frame.trade_date).min().date()),
        "end_date": str(pd.to_datetime(frame.trade_date).max().date()), "index_code": "000905.SH",
        "listing_calendar": {"provider": "tushare", "endpoint": "trade_cal", "exchange": "SSE",
                             "start_date": "2024-01-30", "end_date": "2024-02-05",
                             "open_sessions": ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02", "2024-02-05"]},
    })
    return frame


def _snapshot(end, *, market=None, start=None, actions=()):
    frame = _market() if market is None else market.copy()
    frame = frame.loc[frame.trade_date.le(pd.Timestamp(end))]
    if start:
        frame = frame.loc[frame.trade_date.ge(pd.Timestamp(start))]
    frame = point_in_time_adjust_prices(frame.reset_index(drop=True))
    if actions:
        frame = attach_corporate_actions(frame, list(actions))
    return _attest(frame)


def _plan(day="2024-01-31", weights=None, **kwargs):
    return build_execution_plan([
        TargetPosition(pd.Timestamp(day).date(), symbol, weight, "fixture", run_id="run-paper",
                       model_id="model-paper", signal_id=f"{day}:{symbol}")
        for symbol, weight in (weights or {"600000.SH": .5}).items()
    ], **kwargs)


def test_next_open_raw_lots_costs_restart_and_readonly_replay(tmp_path, policy):
    book = tmp_path / "book.sqlite3"
    policy = replace(policy, commission_min=5, transfer_fee_rate=.00001, slippage_bps=10)
    first = advance_paper_book(book, _snapshot("2024-01-31"), policy, plan=_plan(), commit=True)
    assert first["new_trades"] == [] and first["positions"] == {}
    assert first["cash"] == first["nav"] == 100000
    # A fresh call reopens SQLite; no in-memory gateway state is involved.
    second = advance_paper_book(book, _snapshot("2024-02-01"), policy, commit=True)
    trade, = second["new_trades"]
    assert trade["trade_date"] == "2024-02-01" and trade["signal_date"] == "2024-01-31"
    assert trade["base_price"] == 10 and trade["price"] == pytest.approx(10.01)
    assert trade["qty"] == 5000 and trade["commission"] == 5
    assert trade["execution_plan_id"] == _plan().plan_id
    assert trade["run_id"] == "run-paper" and trade["model_id"] == "model-paper"
    assert second["cash"] == pytest.approx(100000 - 50050 - 5 - .5005)
    assert second["nav"] == pytest.approx(second["cash"] + 55000)
    before = book.read_bytes()
    replay = replay_paper_book(book)
    assert replay["trades"] == second["new_trades"]
    assert replay["daily_returns"][0]["trade_date"] == "2024-01-31"
    assert book.read_bytes() == before
    direct = run_portfolio_backtest(_snapshot("2024-02-01").query("trade_date >= '2024-01-31'"),
                                    pd.DataFrame(), list(_plan().targets), policy)
    assert replay["daily_returns"] == direct["daily_returns"]
    assert replay["metrics"] == direct["metrics"]


def test_preview_never_creates_or_changes_book(tmp_path, policy):
    book = tmp_path / "missing" / "book.sqlite3"
    preview = advance_paper_book(book, _snapshot("2024-01-31"), policy, plan=_plan())
    assert not book.parent.exists()
    actual = advance_paper_book(book, _snapshot("2024-01-31"), policy, plan=_plan(), commit=True)
    assert preview["committed_at"] is None and actual["committed_at"]
    assert {k: v for k, v in actual.items() if k != "committed_at"} == {k: v for k, v in preview.items() if k != "committed_at"}
    before = book.read_bytes()
    advance_paper_book(book, _snapshot("2024-02-01"), policy)
    assert book.read_bytes() == before
    assert replay_paper_book(book)["trades"] == []


def test_idempotency_and_conflicting_same_day_plan(tmp_path, policy):
    book = tmp_path / "book.sqlite3"
    snapshot = _snapshot("2024-01-31")
    receipt = advance_paper_book(book, snapshot, policy, plan=_plan(), commit=True)
    assert advance_paper_book(book, snapshot, policy, plan=_plan(), commit=True) == receipt
    with pytest.raises(ValueError, match="already finalized"):
        advance_paper_book(book, snapshot, policy, plan=_plan(weights={"600001.SH": .5}), commit=True)


def test_concurrent_retries_commit_only_one_plan_and_receipt(tmp_path, policy):
    book = tmp_path / "book.sqlite3"
    snapshot = _snapshot("2024-01-31")
    plan = _plan()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: advance_paper_book(book, snapshot, policy, plan=plan, commit=True), range(2)))
    assert results[0] == results[1]
    with sqlite3.connect(book) as conn:
        assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1


@pytest.mark.parametrize("change", ["price", "delete", "insert"])
def test_frozen_history_rejects_changes_transactionally(tmp_path, policy, change):
    book = tmp_path / "book.sqlite3"
    advance_paper_book(book, _snapshot("2024-01-31"), policy, plan=_plan(), commit=True)
    snapshot = _snapshot("2024-02-01")
    old = snapshot.trade_date.eq(pd.Timestamp("2024-01-31")) & snapshot.symbol.eq("600000.SH")
    if change == "price":
        snapshot.loc[old, "close"] += 1
    elif change == "delete":
        snapshot = snapshot.loc[~old]
    else:
        row = snapshot.loc[old].copy().assign(symbol="600002.SH")
        snapshot = pd.concat([snapshot, row], ignore_index=True)
    before = book.read_bytes()
    with pytest.raises(ValueError, match="frozen|inserted historical"):
        advance_paper_book(book, _attest(snapshot), policy, commit=True)
    assert book.read_bytes() == before
    assert replay_paper_book(book)["trades"] == []


def test_config_date_and_overlap_guards(tmp_path, policy):
    book = tmp_path / "book.sqlite3"
    advance_paper_book(book, _snapshot("2024-01-31"), policy, commit=True)
    with pytest.raises(ValueError, match="config differs"):
        advance_paper_book(book, _snapshot("2024-02-01"), replace(policy, top_k=20), commit=True)
    for snapshot in (_snapshot("2024-01-30"), _snapshot("2024-02-02", start="2024-02-01")):
        with pytest.raises(ValueError, match="advance and overlap"):
            advance_paper_book(book, snapshot, policy, commit=True)


def test_later_first_plan_does_not_drop_cash_only_book_days(tmp_path, policy):
    book = tmp_path / "book.sqlite3"
    advance_paper_book(book, _snapshot("2024-01-30"), policy, commit=True)
    advance_paper_book(book, _snapshot("2024-01-31"), policy, plan=_plan(), commit=True)
    receipt = advance_paper_book(book, _snapshot("2024-02-01", start="2024-01-31"), policy, commit=True)
    assert receipt["book_start"] == "2024-01-30"
    assert len(replay_paper_book(book)["daily_returns"]) == 3


def test_monthly_rotation_sell_first_and_turnover_cap(tmp_path, policy):
    book = tmp_path / "book.sqlite3"
    policy = replace(policy, max_daily_turnover=.3)
    advance_paper_book(book, _snapshot("2024-01-31"), policy, plan=_plan(weights={"600000.SH": 1}), commit=True)
    receipt = advance_paper_book(book, _snapshot("2024-02-01"), policy,
                                 plan=_plan("2024-02-01", {"600001.SH": 1}), commit=True)
    assert receipt["positions"] == {"600000.SH": 3000}
    receipt = advance_paper_book(book, _snapshot("2024-02-02"), policy, commit=True)
    assert [trade["side"] for trade in receipt["new_trades"]] == ["sell", "buy"]
    assert sum(t["notional"] for t in receipt["new_trades"]) <= .3 * 100000
    before = book.read_bytes()
    with pytest.raises(ValueError, match="already has a target plan"):
        advance_paper_book(book, _snapshot("2024-02-05"), policy, plan=_plan("2024-02-05"), commit=True)
    assert book.read_bytes() == before


@pytest.mark.parametrize("restriction,reason", [("suspended", "missing_or_suspended"), ("limit_up", "limit_up_buy_block")])
def test_next_open_blocks_and_no_automatic_late_fill(tmp_path, policy, restriction, reason):
    book = tmp_path / "book.sqlite3"
    market = _market()
    blocked = market.trade_date.eq("2024-02-01") & market.symbol.eq("600000.SH")
    if restriction == "suspended":
        market.loc[blocked, "is_suspended"] = True
    else:
        market.loc[blocked, "up_limit"] = 10
    advance_paper_book(book, _snapshot("2024-01-31", market=market), policy, plan=_plan(), commit=True)
    receipt = advance_paper_book(book, _snapshot("2024-02-02", market=market), policy, commit=True)
    assert receipt["positions"] == {} and receipt["new_trades"] == []
    assert receipt["metrics"]["unexecutable_reasons"][reason] == 1


def test_st_or_departed_holdings_remain_in_valuation_and_exits(tmp_path, policy):
    book = tmp_path / "book.sqlite3"
    market = _market()
    after = market.trade_date.ge("2024-02-01") & market.symbol.eq("600000.SH")
    market.loc[after, ["is_st", "is_index_member"]] = [True, False]
    advance_paper_book(book, _snapshot("2024-01-31", market=market), policy, plan=_plan(), commit=True)
    receipt = advance_paper_book(book, _snapshot("2024-02-01", market=market), policy,
                                 plan=_plan("2024-02-01", {"600001.SH": .5}), commit=True)
    assert receipt["positions"] == {"600000.SH": 5000}
    receipt = advance_paper_book(book, _snapshot("2024-02-02", market=market), policy, commit=True)
    assert receipt["new_trades"][0]["side"] == "sell"
    assert "600000.SH" not in receipt["positions"]


@pytest.mark.parametrize("invalid", ["live", "date", "weight", "industry", "universe", "top_k"])
def test_plan_risk_guards_before_commit(tmp_path, policy, invalid):
    plan = _plan()
    market = _market()
    if invalid == "live":
        plan = _plan(mode="live", allow_live=True)
    elif invalid == "date":
        plan = _plan("2024-01-30")
    elif invalid == "weight":
        policy = replace(policy, max_position_weight=.1)
    elif invalid == "industry":
        policy = replace(policy, max_industry_weight=.4)
    elif invalid == "top_k":
        policy = replace(policy, top_k=1)
        plan = _plan(weights={"600000.SH": .4, "600001.SH": .4})
    else:
        market["listing_days"] = 251
    book = tmp_path / "book.sqlite3"
    with pytest.raises(ValueError):
        advance_paper_book(book, _snapshot("2024-01-31", market=market), policy, plan=plan, commit=True)
    assert not book.exists()


def _action(**overrides):
    return CorporateAction(**dict(action_id="dividend", symbol="600000.SH", announce_date="2024-01-30",
                                  record_date="2024-02-01", ex_date="2024-02-02", cash_per_share=1.,
                                  pay_date="2024-02-05", **overrides))


def test_corporate_event_receivable_delivery_and_restatement_rollback(tmp_path, policy):
    book = tmp_path / "book.sqlite3"
    market = _market()
    market["close"] = 10.
    after = market.symbol.eq("600000.SH") & market.trade_date.ge("2024-02-02")
    market.loc[after, ["open", "high", "low", "close"]] = 9.
    market.loc[after, "adj_factor"] = 3 * 10 / 9
    action = _action()
    advance_paper_book(book, _snapshot("2024-01-31", market=market, actions=[action]), policy, plan=_plan(), commit=True)
    receipt = advance_paper_book(book, _snapshot("2024-02-02", market=market, actions=[action]), policy, commit=True)
    assert receipt["nav"] == 100000 and receipt["cash"] == 50000
    before = book.read_bytes()
    bad = replace(action, cash_per_share=2)
    with pytest.raises(ValueError, match="changed after publication"):
        advance_paper_book(book, _snapshot("2024-02-05", market=market, actions=[bad]), policy, commit=True)
    assert book.read_bytes() == before
    receipt = advance_paper_book(book, _snapshot("2024-02-05", market=market, actions=[action]), policy, commit=True)
    assert receipt["cash"] == 55000 and receipt["nav"] == 100000
    assert receipt["accounting"]["cash_dividends_paid"] == 5000


def test_late_event_requires_reconciliation(tmp_path, policy):
    book = tmp_path / "book.sqlite3"
    advance_paper_book(book, _snapshot("2024-02-01"), policy, commit=True)
    with pytest.raises(ValueError, match="arrived after its record close"):
        advance_paper_book(book, _snapshot("2024-02-02", actions=[_action()]), policy, commit=True)


def test_duplicate_symbols_and_nonfinite_execution_inputs_fail_closed():
    plan = _plan()
    duplicate = replace(plan.targets[0], signal_id="other")
    with pytest.raises(ValueError, match="duplicate symbols"):
        build_execution_plan([plan.targets[0], duplicate])
    for invalid in (float("nan"), float("inf"), -1):
        with pytest.raises(ValueError, match="account_value"):
            validate_execution_risk(plan, prices={"600000.SH": 10}, account_value=invalid)
        with pytest.raises(ValueError, match="positive price"):
            validate_execution_risk(plan, prices={"600000.SH": invalid}, account_value=100000)


def test_unrelated_sqlite_and_tampered_book_fail_closed(tmp_path, policy):
    other = tmp_path / "old.sqlite3"
    with sqlite3.connect(other) as conn:
        conn.execute("CREATE TABLE old_trades (id INTEGER)")
    before = other.read_bytes()
    with pytest.raises(ValueError, match="dedicated book path"):
        advance_paper_book(other, _snapshot("2024-01-31"), policy, commit=True)
    assert other.read_bytes() == before
    book = tmp_path / "book.sqlite3"
    advance_paper_book(book, _snapshot("2024-01-31"), policy, commit=True)
    with sqlite3.connect(book) as conn:
        conn.execute("UPDATE receipts SET payload=?", (json.dumps({"replay_hash": "changed"}),))
    with pytest.raises(ValueError, match="does not match"):
        replay_paper_book(book)
    with pytest.raises(ValueError, match="does not match"):
        advance_paper_book(book, _snapshot("2024-02-01"), policy, commit=True)


def test_unsigned_or_modified_snapshot_fails_before_creating_book(tmp_path, policy):
    frame = _snapshot("2024-01-31")
    frame.loc[0, "close"] += 1
    book = tmp_path / "book.sqlite3"
    with pytest.raises(ValueError, match="does not match"):
        advance_paper_book(book, frame, policy, commit=True)
    assert not book.exists()


def test_missing_next_session_cannot_fill_at_a_later_open(tmp_path, policy):
    book = tmp_path / "book.sqlite3"
    advance_paper_book(book, _snapshot("2024-01-31"), policy, plan=_plan(), commit=True)
    frame = _snapshot("2024-02-02")
    frame = _attest(frame.loc[frame.trade_date.ne(pd.Timestamp("2024-02-01"))])
    before = book.read_bytes()
    with pytest.raises(ValueError, match="missing=.*2024-02-01"):
        advance_paper_book(book, frame, policy, commit=True)
    assert book.read_bytes() == before
    assert replay_paper_book(book)["trades"] == []


def test_rolling_snapshot_cannot_revise_an_older_calendar_session(tmp_path, policy):
    book = tmp_path / "book.sqlite3"
    advance_paper_book(book, _snapshot("2024-01-30"), policy, commit=True)
    advance_paper_book(book, _snapshot("2024-01-31"), policy, plan=_plan(), commit=True)
    frame = _snapshot("2024-02-01", start="2024-01-31")
    metadata = deepcopy(frame.attrs["snapshot_metadata"])
    metadata["listing_calendar"]["open_sessions"].remove("2024-01-30")
    frame.attrs["snapshot_metadata"] = attest_snapshot_metadata(frame, metadata)
    before = book.read_bytes()
    with pytest.raises(ValueError, match="unexpected=.*2024-01-30"):
        advance_paper_book(book, frame, policy, commit=True)
    assert book.read_bytes() == before


def test_old_book_remains_explicitly_readable_but_cannot_append_without_calendar(tmp_path, policy):
    book = tmp_path / "book.sqlite3"
    advance_paper_book(book, _snapshot("2024-01-31"), policy, plan=_plan(), commit=True)
    with sqlite3.connect(book) as conn:
        payload = json.loads(conn.execute("SELECT payload FROM receipts").fetchone()[0])
        del payload["calendar_metadata"]
        conn.execute("UPDATE receipts SET payload=?", (json.dumps(payload),))
    before = book.read_bytes()
    with pytest.raises(ValueError, match="no verified session calendar"):
        replay_paper_book(book)
    replay = replay_paper_book(book, allow_unverified_calendar=True)
    assert replay["calendar_verification"]["available"] is False
    with pytest.raises(ValueError, match="reconcile explicitly"):
        advance_paper_book(book, _snapshot("2024-02-01"), policy, commit=True)
    assert book.read_bytes() == before


@pytest.mark.parametrize("change", ["provider", "sessions"])
def test_replay_rejects_calendar_tampering_even_with_legacy_opt_in(tmp_path, policy, change):
    book = tmp_path / "book.sqlite3"
    advance_paper_book(book, _snapshot("2024-01-31"), policy, plan=_plan(), commit=True)
    with sqlite3.connect(book) as conn:
        payload = json.loads(conn.execute("SELECT payload FROM receipts").fetchone()[0])
        if change == "provider":
            payload["calendar_metadata"]["provider"] = "akshare"
        else:
            payload["calendar_metadata"]["listing_calendar"]["open_sessions"].remove("2024-01-31")
        conn.execute("UPDATE receipts SET payload=?", (json.dumps(payload),))
    for allow in (False, True):
        with pytest.raises(ValueError, match="provider=tushare_pit|signature"):
            replay_paper_book(book, allow_unverified_calendar=allow)


def test_calendar_upgrade_previews_and_publishes_verified_copy_without_changing_source(tmp_path, policy, capsys):
    source, destination = tmp_path / "old.sqlite3", tmp_path / "new" / "book.sqlite3"
    snapshot = _snapshot("2024-02-01")
    advance_paper_book(source, _snapshot("2024-01-31"), policy, plan=_plan(), commit=True)
    advance_paper_book(source, snapshot, policy, commit=True)
    with sqlite3.connect(source) as conn:
        for day, encoded in conn.execute("SELECT day,payload FROM receipts").fetchall():
            receipt = json.loads(encoded)
            del receipt["calendar_metadata"]
            conn.execute("UPDATE receipts SET payload=? WHERE day=?", (json.dumps(receipt), day))
    before = source.read_bytes()
    result = upgrade_paper_book_calendar(source, destination, snapshot)
    assert not result["committed"] and not destination.parent.exists()
    assert source.read_bytes() == before
    from scripts.migrate_paper_calendar import main

    panel = tmp_path / "rebuilt.parquet"
    write_attested_parquet(snapshot, panel, snapshot.attrs["snapshot_metadata"])
    assert main([str(source), str(destination), "--snapshot", str(panel)]) == 0
    assert json.loads(capsys.readouterr().out)["committed"] is False
    assert not destination.parent.exists() and source.read_bytes() == before
    result = upgrade_paper_book_calendar(source, destination, snapshot, commit=True)
    assert result["committed"] and result["sessions"] == 2
    assert source.read_bytes() == before
    assert replay_paper_book(destination)["calendar_verification"] == {"available": True, "sessions": 2}
    # Same-close retry after migration is still idempotent under its new
    # attestation. Later closes continue from the copied cash/positions.
    receipt = advance_paper_book(destination, snapshot, policy, commit=True)
    assert receipt["positions"] == {"600000.SH": 5000}
    assert "calendar_upgrade" in receipt
    advance_paper_book(destination, _snapshot("2024-02-02"), policy, commit=True)
    with pytest.raises(FileExistsError):
        upgrade_paper_book_calendar(source, destination, snapshot, commit=True)


@pytest.mark.parametrize("change", ["price", "end_date", "missing_session"])
def test_calendar_upgrade_cannot_rewrite_frozen_accounting(tmp_path, policy, change):
    import aipha.research.paper_book as module

    source, destination = tmp_path / "old.sqlite3", tmp_path / "new.sqlite3"
    advance_paper_book(source, _snapshot("2024-01-30"), policy, plan=_plan("2024-01-30"), commit=True)
    advance_paper_book(source, _snapshot("2024-02-01"), policy, commit=True)
    snapshot = _snapshot("2024-02-01")
    if change == "price":
        snapshot.loc[snapshot.trade_date.eq(pd.Timestamp("2024-01-31")), "close"] += 1
        snapshot = _attest(snapshot)
    elif change == "end_date":
        snapshot = _snapshot("2024-02-02")
    with sqlite3.connect(source) as conn:
        if change == "missing_session":
            # Model an old book that filled at Feb 1 open because Jan 31
            # had been omitted. A migration must not bless that accounting.
            conn.execute("DELETE FROM market WHERE day='2024-01-31'")
        receipt = json.loads(conn.execute("SELECT payload FROM receipts ORDER BY day DESC LIMIT 1").fetchone()[0])
        del receipt["calendar_metadata"]
        receipt["replay_hash"] = module._hash(module._replay(conn, policy))
        conn.execute("UPDATE receipts SET payload=? WHERE day='2024-02-01'", (json.dumps(receipt),))
    before = source.read_bytes()
    with pytest.raises(ValueError, match="cannot change frozen|must end|cannot insert or remove"):
        upgrade_paper_book_calendar(source, destination, snapshot, commit=True)
    assert not destination.exists() and source.read_bytes() == before


def test_replay_survives_fresh_process_hash_order(tmp_path, policy):
    """Floating-point NAV and turnover must not depend on Python set order."""
    book = tmp_path / "book.sqlite3"
    base = _market().query("symbol == '600000.SH'")
    market = pd.concat([
        base.assign(symbol=f"{600000+i}.SH", close=10.13 + i * .017)
        for i in range(12)
    ], ignore_index=True)
    weights = {f"{600000+i}.SH": .073 for i in range(12)}
    advance_paper_book(book, _snapshot("2024-01-31", market=market), policy, plan=_plan(weights=weights), commit=True)
    advance_paper_book(book, _snapshot("2024-02-01", market=market), policy, commit=True)
    script = "from aipha.research.paper_book import replay_paper_book; import sys; replay_paper_book(sys.argv[1])"
    for seed in ("13", "72"):
        result = subprocess.run([sys.executable, "-c", script, str(book)], capture_output=True, text=True,
                                env={**os.environ, "PYTHONHASHSEED": seed}, timeout=30)
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("field", ["commission_rate", "commission_min", "stamp_tax_rate", "transfer_fee_rate", "slippage_bps", "initial_capital"])
def test_nonfinite_accounting_config_rejected(tmp_path, policy, field):
    with pytest.raises(ValueError, match="finite"):
        advance_paper_book(tmp_path / "book.sqlite3", _snapshot("2024-01-31"),
                           replace(policy, **{field: float("nan")}), commit=True)


def test_failure_during_replay_rolls_back_market_and_new_plan(tmp_path, policy, monkeypatch):
    import aipha.research.paper_book as module

    book = tmp_path / "book.sqlite3"
    advance_paper_book(book, _snapshot("2024-01-31"), policy, plan=_plan(), commit=True)
    before = book.read_bytes()
    original = module.run_portfolio_backtest
    def fail_after_existing_replay(market, *args):
        if pd.to_datetime(market.trade_date).max() > pd.Timestamp("2024-01-31"):
            raise ValueError("injected accounting failure")
        return original(market, *args)
    monkeypatch.setattr(module, "run_portfolio_backtest", fail_after_existing_replay)
    with pytest.raises(ValueError, match="accounting failure"):
        advance_paper_book(book, _snapshot("2024-02-01"), policy, plan=_plan("2024-02-01"), commit=True)
    assert book.read_bytes() == before
    monkeypatch.setattr(module, "run_portfolio_backtest", original)
    assert advance_paper_book(book, _snapshot("2024-02-01"), policy, plan=_plan("2024-02-01"), commit=True)["positions"]


def test_daily_cli_consumes_plan_and_full_tape_and_replays(tmp_path, monkeypatch, capsys):
    from scripts.ops import v2_ml_paper_daily as cli

    # The canonical path must not load or mutate the old JSON book or scorer.
    for name in ("load_state", "save_state", "execute_rebalance", "ensemble_predict"):
        monkeypatch.setattr(cli, name, lambda *a, **k: pytest.fail("legacy path called"))
    config = tmp_path / "research.yaml"
    config.write_text(yaml.safe_dump({"portfolio": {"max_position_weight": 1, "max_industry_weight": 1}}))
    panel = tmp_path / "snapshot.parquet"
    plan_path = _plan().write(tmp_path / "plan.json")
    book = tmp_path / "book.sqlite3"
    def write(day):
        frame = _snapshot(day)
        write_attested_parquet(frame, panel, frame.attrs["snapshot_metadata"])
    write("2024-01-31")
    args = ["--panel", str(panel), "--plan-path", str(plan_path), "--research-config", str(config),
            "--book-path", str(book), "--json"]
    assert cli.main(args) == 0
    assert not json.loads(capsys.readouterr().out)["committed"] and not book.exists()
    assert cli.main([*args, "--execute"]) == 0
    capsys.readouterr()
    write("2024-02-01")
    assert cli.main(["--panel", str(panel), "--research-config", str(config), "--book-path", str(book),
                     "--execute", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["new_trades"]) == 1
    assert cli.main(["--book-path", str(book), "--replay", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["trades"]) == 1
    assert cli.main(["--panel", str(panel), "--model-dir", "old-model", "--execute"]) == 2
    assert cli.main(["--panel", str(panel), "--date", "20240131", "--execute"]) == 2

