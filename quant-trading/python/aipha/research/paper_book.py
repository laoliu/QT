"""Durable daily Paper accounting using the canonical portfolio replay engine."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import pandas as pd

from .backtest import run_portfolio_backtest
from .contracts import MARKET_COLUMNS, validate_market_frame
from .corporate_actions import attach_corporate_actions, CorporateAction, read_corporate_actions
from .evaluation import json_safe_metrics
from .execution_plan import ExecutionPlan, _execution_plan_from_dict, validate_execution_risk
from .pipeline import ResearchConfig, _load_snapshot_metadata
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


def replay_paper_book(path: str | Path) -> dict:
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
    request_hash = _hash({"snapshot": metadata["snapshot_content_sha256"], "config": config.model_config_hash(),
                          "plan": plan.to_dict() if plan is not None else None})
    columns = [column for column in (*MARKET_COLUMNS, "price_adjustment", "up_limit", "down_limit",
                                     "open_raw", "high_raw", "low_raw", "close_raw") if column in bars]
    tape = bars[columns].astype(object).where(pd.notna(bars[columns]), None)
    by_day = {}
    for record in tape.to_dict("records"):
        by_day.setdefault(str(record["trade_date"]), {})[record["symbol"]] = _dump(record)

    with _connection(Path(path), commit) as connection:
        frozen = connection.execute("SELECT value FROM settings WHERE key='config'").fetchone()
        if frozen is not None and _dump(asdict(config)) != frozen[0]:
            raise ValueError("Paper research config differs from this book; use a separate book or explicit migration")
        previous = connection.execute("SELECT day,request_hash,payload FROM receipts ORDER BY day DESC LIMIT 1").fetchone()
        if previous is not None:
            if _hash(_replay(connection, config)) != json.loads(previous[2])["replay_hash"]:
                raise ValueError("Paper book replay does not match its committed receipt")
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
        result = _replay(connection, config)
        last = result["daily_returns"][-1]
        receipt = {
            "schema_version": 1, "mode": "paper", "trade_date": day,
            "book_start": book_start,
            "data_snapshot_id": metadata["data_snapshot_id"], "model_config_hash": config.model_config_hash(),
            "accepted_plan_id": plan.plan_id if plan is not None else None,
            "nav": last["nav"], "cash": last["cash"], "positions": result["final_positions"],
            "new_trades": [trade for trade in result["trades"] if previous is None or trade["trade_date"] > previous[0]],
            "metrics": result["metrics"], "accounting": result["accounting"],
            "replay_hash": _hash(result),
        }
        connection.execute("INSERT INTO settings VALUES ('config',?) ON CONFLICT(key) DO NOTHING", (_dump(asdict(config)),))
        connection.execute("INSERT INTO receipts VALUES (?,?,?)", (day, request_hash, _dump(receipt)))
        return receipt
