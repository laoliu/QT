"""Durable daily Paper accounting using the canonical portfolio replay engine."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile

import pandas as pd

from .backtest import run_portfolio_backtest
from .contracts import MARKET_COLUMNS, validate_market_frame
from .corporate_actions import attach_corporate_actions, CorporateAction, read_corporate_actions
from .evaluation import json_safe_metrics
from .execution_plan import ExecutionPlan, _execution_plan_from_dict, validate_execution_risk
from .pipeline import ResearchConfig, _load_snapshot_metadata
from .snapshots import validate_market_calendar, verify_snapshot_signature
from aipha.universe.historical import filter_historical_universe
from aipha.universe.indices import validate_snapshot_universe


def _dump(value) -> str:
    return json.dumps(json_safe_metrics(value), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str, allow_nan=False)


def _hash(value) -> str:
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


@contextmanager
def _connection(path: Path, commit: bool):
    """Preview on a private database copy; commit under a SQLite writer lock."""
    if commit:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
    else:
        connection = sqlite3.connect(":memory:")
        if path.exists():
            source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                source.backup(connection)
            finally:
                source.close()
    try:
        connection.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables:
            if tables != {"settings", "market", "actions", "plans", "receipts"}:
                raise ValueError("not a canonical Paper book; use a dedicated book path")
            version = connection.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
            if version != ('1',):
                raise ValueError("unsupported Paper book schema_version")
        for statement in (
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS market (day TEXT, symbol TEXT, payload TEXT NOT NULL, PRIMARY KEY(day,symbol))",
            "CREATE TABLE IF NOT EXISTS actions (action_id TEXT PRIMARY KEY, payload TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS plans (month TEXT PRIMARY KEY, day TEXT UNIQUE, plan_id TEXT UNIQUE, payload TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS receipts (day TEXT PRIMARY KEY, request_hash TEXT NOT NULL, payload TEXT NOT NULL)",
        ):
            connection.execute(statement)
        connection.execute("INSERT INTO settings VALUES ('schema_version','1') ON CONFLICT(key) DO NOTHING")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _replay(connection, config: ResearchConfig) -> dict:
    rows = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM market ORDER BY day,symbol")]
    market = pd.DataFrame(rows)
    actions = [CorporateAction(**json.loads(row[0])) for row in connection.execute("SELECT payload FROM actions ORDER BY action_id")]
    if actions:
        market = attach_corporate_actions(market, actions)
    plans = [_execution_plan_from_dict(json.loads(row[0])) for row in connection.execute("SELECT payload FROM plans ORDER BY day")]
    targets = [target for plan in plans for target in plan.targets]
    # A Paper book's observation window begins when it opens in cash, even
    # when the first plan arrives later. Do not crop it to model OOS dates.
    result = run_portfolio_backtest(market, pd.DataFrame(), targets, config)
    plan_ids = {plan.rebalance_date.isoformat(): plan.plan_id for plan in plans}
    for trade in result["trades"]:
        trade["execution_plan_id"] = plan_ids[trade["signal_date"]]
    return json_safe_metrics(result)


def _verify_receipt_calendar(connection, receipt: dict) -> int:
    metadata = receipt.get("calendar_metadata")
    if not isinstance(metadata, dict):
        raise ValueError("Paper book has no verified session calendar; reconcile explicitly before appending")
    if metadata.get("provider") != "tushare_pit":
        raise ValueError("Paper book calendar evidence must have provider=tushare_pit")
    verify_snapshot_signature(metadata, required=True)
    days = [row[0] for row in connection.execute("SELECT DISTINCT day FROM market ORDER BY day")]
    sessions = validate_market_calendar(pd.DataFrame({"trade_date": days}), metadata,
                                        start_date=days[0], end_date=days[-1])
    return len(sessions)


def _market_records(bars: pd.DataFrame) -> dict[tuple[str, str], dict]:
    columns = [column for column in (*MARKET_COLUMNS, "price_adjustment", "up_limit", "down_limit",
                                     "open_raw", "high_raw", "low_raw", "close_raw") if column in bars]
    tape = bars[columns].astype(object).where(pd.notna(bars[columns]), None)
    return {(str(record["trade_date"]), record["symbol"]): record for record in tape.to_dict("records")}


def upgrade_paper_book_calendar(
    source: str | Path, destination: str | Path, snapshot: pd.DataFrame, *, commit: bool = False,
) -> dict:
    """Verify old book dates/prices against a rebuilt signed snapshot and copy.

    Only calendar evidence and the latest receipt's source identity change.
    Cash, targets, trades, corporate events and original files are preserved.
    A book that skipped an open session cannot be upgraded this way: it needs
    explicit reconciliation rather than relabeling a later fill as next-open.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.exists():
        raise FileExistsError(destination)
    metadata = _load_snapshot_metadata(snapshot)
    memory = sqlite3.connect(":memory:")
    original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    try:
        original.backup(memory)
    finally:
        original.close()
    try:
        config = ResearchConfig(**json.loads(memory.execute("SELECT value FROM settings WHERE key='config'").fetchone()[0]))
        validate_snapshot_universe(metadata, config.universe)
        if metadata["provider"] != config.data_provider or metadata["feature_version"] != config.feature_version:
            raise ValueError("calendar upgrade provider/feature_version differs from the book")
        receipt_row = memory.execute("SELECT day,payload FROM receipts ORDER BY day DESC LIMIT 1").fetchone()
        receipt = json.loads(receipt_row[1])
        if receipt["replay_hash"] != _hash(_replay(memory, config)):
            raise ValueError("Paper book replay does not match its committed receipt")
        if str(pd.Timestamp(metadata["end_date"]).date()) != receipt_row[0]:
            raise ValueError("calendar upgrade snapshot must end at the last committed close")
        records = _market_records(validate_market_frame(snapshot))
        stored = {(day, symbol): json.loads(payload) for day, symbol, payload in
                  memory.execute("SELECT day,symbol,payload FROM market ORDER BY day,symbol")}
        book_start = min(day for day, _ in stored)
        relevant = {key: value for key, value in records.items() if key[0] >= book_start}
        if relevant.keys() != stored.keys():
            raise ValueError("calendar upgrade cannot insert or remove frozen market observations")
        for key, old in stored.items():
            if _dump({name: relevant[key].get(name) for name in old}) != _dump(old):
                raise ValueError("calendar upgrade cannot change frozen market observations")
        stored_actions = {key: payload for key, payload in memory.execute("SELECT action_id,payload FROM actions")}
        symbols = {symbol for _, symbol in stored}
        actions = {action.action_id: _dump(action.to_wire()) for action in read_corporate_actions(snapshot)
                   if action.ex_date.isoformat() >= book_start and action.symbol in symbols}
        if stored_actions != actions:
            raise ValueError("calendar upgrade cannot change corporate events")
        receipt["calendar_metadata"] = metadata
        receipt["calendar_upgrade"] = {"source": str(source), "original_data_snapshot_id": receipt["data_snapshot_id"]}
        receipt["data_snapshot_id"] = metadata["data_snapshot_id"]
        count = _verify_receipt_calendar(memory, receipt)
        accepted = memory.execute("SELECT payload FROM plans WHERE day=?", (receipt_row[0],)).fetchone()
        request_hash = _hash({"snapshot": metadata["snapshot_content_sha256"], "source_signature": metadata["source_signature"],
                              "config": config.model_config_hash(), "plan": json.loads(accepted[0]) if accepted else None})
        memory.execute("UPDATE receipts SET request_hash=?,payload=? WHERE day=?", (request_hash, _dump(receipt), receipt_row[0]))
        memory.commit()
        if commit:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".paper-calendar-", dir=destination.parent) as temp:
                staged = Path(temp) / "book.sqlite3"
                copied = sqlite3.connect(staged)
                try:
                    memory.backup(copied)
                finally:
                    copied.close()
                replay_paper_book(staged)
                # Atomic publication fails if another process created the
                # destination after the initial check; never overwrite it.
                os.link(staged, destination)
        return {"source": str(source), "destination": str(destination), "committed": commit,
                "sessions": count, "last_close": receipt_row[0], "replay_hash": receipt["replay_hash"]}
    finally:
        memory.close()


def replay_paper_book(path: str | Path, *, allow_unverified_calendar: bool = False) -> dict:
    """Recompute the persisted book and verify its latest receipt digest."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        stored = connection.execute("SELECT value FROM settings WHERE key='config'").fetchone()
        if stored is None:
            raise ValueError("Paper book has no committed research config")
        result = _replay(connection, ResearchConfig(**json.loads(stored[0])))
        receipt = connection.execute("SELECT payload FROM receipts ORDER BY day DESC LIMIT 1").fetchone()
        if receipt is None or json.loads(receipt[0])["replay_hash"] != _hash(result):
            raise ValueError("Paper book replay does not match its committed receipt")
        payload = json.loads(receipt[0])
        if "calendar_metadata" not in payload and allow_unverified_calendar:
            result["calendar_verification"] = {"available": False, "reason": "legacy_book_without_calendar"}
        else:
            count = _verify_receipt_calendar(connection, payload)
            result["calendar_verification"] = {"available": True, "sessions": count}
        return result
    finally:
        connection.close()


def advance_paper_book(
    path: str | Path,
    snapshot: pd.DataFrame,
    config: ResearchConfig,
    *,
    plan: ExecutionPlan | None = None,
    commit: bool = False,
) -> dict:
    """Finalize one observed close and queue its optional next-open target plan.

    New books start in cash at this close. Subsequent snapshots must overlap
    the previous close and include every stored observation in their range.
    Historical rows are immutable. Corporate events are stored separately
    because snapshot builders attach their full event list to the first row.
    No broker is imported or contacted by this accounting path.
    """
    metadata = _load_snapshot_metadata(snapshot)
    validate_snapshot_universe(metadata, config.universe)
    if metadata["provider"] != config.data_provider or metadata["feature_version"] != config.feature_version:
        raise ValueError("Paper snapshot provider/feature_version does not match research config")
    bars = validate_market_frame(snapshot)
    if bars.empty or bars.trade_date.isna().any():
        raise ValueError("Paper snapshot requires non-empty dated market observations")
    if not math.isfinite(config.initial_capital) or config.initial_capital <= 0:
        raise ValueError("Paper initial_capital must be positive and finite")
    for name in ("commission_rate", "commission_min", "stamp_tax_rate", "transfer_fee_rate", "slippage_bps"):
        value = getattr(config, name)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Paper {name} must be finite and non-negative")
    first, day = str(bars.trade_date.min()), str(bars.trade_date.max())
    if first != str(pd.Timestamp(metadata["start_date"]).date()) or day != str(pd.Timestamp(metadata["end_date"]).date()):
        raise ValueError("Paper snapshot dates do not match its declared range")
    eligible = filter_historical_universe(snapshot, min_listing_days=config.min_listing_days)
    if plan is not None:
        plan.validate()
        if plan.mode != "paper" or plan.rebalance_date.isoformat() != day:
            raise ValueError("Paper plan must be mode=paper and belong to this signal close")
        if sum(target.target_weight > 0 for target in plan.targets) > config.top_k:
            raise ValueError("Paper plan exceeds the configured top_k")
        latest = bars.loc[bars.trade_date.astype(str).eq(day)].set_index("symbol")
        eligible_symbols = set(eligible.loc[pd.to_datetime(eligible.trade_date).dt.date.astype(str).eq(day), "symbol"])
        if any(target.target_weight > 0 and target.symbol not in eligible_symbols for target in plan.targets):
            raise ValueError("Paper plan contains a symbol outside the signal-date historical universe")
        from .corporate_actions import raw_accounting_bars

        raw = raw_accounting_bars(latest)
        validate_execution_risk(plan, prices=raw.close.to_dict(), account_value=config.initial_capital,
                                max_position_weight=config.max_position_weight)
        if config.max_industry_weight is not None and "industry" in latest:
            weights = {}
            for target in plan.targets:
                if target.target_weight == 0:
                    continue
                industry = str(latest.loc[target.symbol, "industry"])
                weights[industry] = weights.get(industry, 0.) + target.target_weight
            if any(weight > config.max_industry_weight + 1e-12 for weight in weights.values()):
                raise ValueError("Paper plan exceeds the configured industry weight limit")
    request_hash = _hash({"snapshot": metadata["snapshot_content_sha256"], "source_signature": metadata["source_signature"],
                          "config": config.model_config_hash(),
                          "plan": plan.to_dict() if plan is not None else None})
    by_day = {}
    for (record_day, symbol), record in _market_records(bars).items():
        by_day.setdefault(record_day, {})[symbol] = _dump(record)

    with _connection(Path(path), commit) as connection:
        frozen = connection.execute("SELECT value FROM settings WHERE key='config'").fetchone()
        if frozen is not None and _dump(asdict(config)) != frozen[0]:
            raise ValueError("Paper research config differs from this book; use a separate book or explicit migration")
        previous = connection.execute("SELECT day,request_hash,payload FROM receipts ORDER BY day DESC LIMIT 1").fetchone()
        if previous is not None:
            if _hash(_replay(connection, config)) != json.loads(previous[2])["replay_hash"]:
                raise ValueError("Paper book replay does not match its committed receipt")
            _verify_receipt_calendar(connection, json.loads(previous[2]))
            if day == previous[0]:
                if request_hash != previous[1]:
                    raise ValueError("Paper close already finalized with different snapshot or plan")
                return json.loads(previous[2])
            if day < previous[0] or first > previous[0]:
                raise ValueError("Paper snapshots must advance and overlap the previous close")
        start_day = previous[0] if previous else day
        stored_rows = connection.execute("SELECT day,symbol,payload FROM market WHERE day>=?", (first,)).fetchall()
        for old_day, symbol, payload in stored_rows:
            if by_day.get(old_day, {}).get(symbol) != payload:
                raise ValueError("Paper snapshot changed or removed a frozen market observation")
        old_keys = {(row[0], row[1]) for row in stored_rows}
        book_start = connection.execute("SELECT MIN(day) FROM market").fetchone()[0] or day
        for observed_day, rows in by_day.items():
            if previous and book_start <= observed_day <= previous[0]:
                if any((observed_day, symbol) not in old_keys for symbol in rows):
                    raise ValueError("Paper snapshot inserted historical market observations")
            if observed_day < start_day or (previous and observed_day == start_day):
                continue
            connection.executemany("INSERT INTO market VALUES (?,?,?)",
                                   [(observed_day, symbol, payload) for symbol, payload in rows.items()])
        for action in read_corporate_actions(snapshot):
            if action.ex_date.isoformat() < book_start:
                continue
            if action.announce_date.isoformat() > day:
                raise ValueError("Paper corporate event has not been announced at this close")
            payload = _dump(action.to_wire())
            existing = connection.execute("SELECT payload FROM actions WHERE action_id=?", (action.action_id,)).fetchone()
            if existing is not None:
                if existing[0] != payload:
                    raise ValueError("Paper corporate event changed after publication")
                continue
            if previous and action.record_date.isoformat() <= previous[0]:
                raise ValueError("Paper corporate event arrived after its record close; reconcile explicitly")
            if connection.execute("SELECT 1 FROM market WHERE symbol=? LIMIT 1", (action.symbol,)).fetchone():
                connection.execute("INSERT INTO actions VALUES (?,?)", (action.action_id, payload))
        if plan is not None:
            if connection.execute("SELECT 1 FROM plans WHERE month=?", (day[:7],)).fetchone():
                raise ValueError("Paper monthly policy already has a target plan for this month")
            connection.execute("INSERT INTO plans VALUES (?,?,?,?)", (day[:7], day, plan.plan_id, _dump(plan.to_dict())))
        # Validate the entire book against the new signed calendar, including
        # days preceding a rolling snapshot's first market row. Calendar
        # revisions cannot silently remove or insert an old session.
        book_days = [row[0] for row in connection.execute("SELECT DISTINCT day FROM market ORDER BY day")]
        validate_market_calendar(pd.DataFrame({"trade_date": book_days}), metadata,
                                 start_date=book_days[0], end_date=book_days[-1])
        result = _replay(connection, config)
        last = result["daily_returns"][-1]
        receipt = {
            "schema_version": 1, "mode": "paper", "trade_date": day,
            "book_start": book_start,
            "data_snapshot_id": metadata["data_snapshot_id"], "model_config_hash": config.model_config_hash(),
            "calendar_metadata": metadata,
            "accepted_plan_id": plan.plan_id if plan is not None else None,
            "nav": last["nav"], "cash": last["cash"], "positions": result["final_positions"],
            "new_trades": [trade for trade in result["trades"] if previous is None or trade["trade_date"] > previous[0]],
            "metrics": result["metrics"], "accounting": result["accounting"],
            "replay_hash": _hash(result),
        }
        connection.execute("INSERT INTO settings VALUES ('config',?) ON CONFLICT(key) DO NOTHING", (_dump(asdict(config)),))
        connection.execute("INSERT INTO receipts VALUES (?,?,?)", (day, request_hash, _dump(receipt)))
        return receipt
