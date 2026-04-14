"""SQLite + JSON storage layer for the VIP portfolio tracker.

Design:
- SQLite as primary index / metadata store
- JSON files for full snapshot payloads (easy for debugging & archive)
- All writes are transactional
- Thread-safe via SQLite WAL mode
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import (
    PortfolioDiff,
    PortfolioSnapshot,
    TradePlan,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id   TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    author_id     TEXT NOT NULL,
    author_name   TEXT NOT NULL,
    portfolio_code TEXT,
    snapshot_time TEXT NOT NULL,
    fetch_time    TEXT NOT NULL,
    holding_count INTEGER NOT NULL,
    total_value   TEXT,
    json_path     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snap_author
    ON snapshots(author_id, snapshot_time DESC);

CREATE TABLE IF NOT EXISTS diffs (
    diff_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id       TEXT NOT NULL,
    from_snapshot_id TEXT NOT NULL,
    to_snapshot_id   TEXT NOT NULL,
    from_time       TEXT NOT NULL,
    to_time         TEXT NOT NULL,
    has_changes     INTEGER NOT NULL,
    change_count    INTEGER NOT NULL,
    json_path       TEXT NOT NULL,
    FOREIGN KEY (from_snapshot_id) REFERENCES snapshots(snapshot_id),
    FOREIGN KEY (to_snapshot_id)   REFERENCES snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_diff_author
    ON diffs(author_id, to_time DESC);

CREATE TABLE IF NOT EXISTS trade_plans (
    plan_id         TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    name            TEXT NOT NULL,
    direction       TEXT NOT NULL,
    status          TEXT NOT NULL,
    confidence      TEXT NOT NULL,
    stop_loss_price REAL NOT NULL,
    stop_loss_pct   REAL NOT NULL,
    target_position_pct REAL NOT NULL,
    created_at      TEXT NOT NULL,
    executed_at     TEXT,
    closed_at       TEXT,
    json_path       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plan_status
    ON trade_plans(status, created_at DESC);
"""


# ---------------------------------------------------------------------------
# TrackerStorage
# ---------------------------------------------------------------------------


class TrackerStorage:
    """Manages persistence of tracker data.

    Usage:
        storage = TrackerStorage(data_dir="data/tracker")
        storage.save_snapshot(snapshot)
        latest = storage.get_latest_snapshot("author_xzlx")
    """

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self._db_path = self.data_dir / "tracker.db"
        self._snapshots_dir = self.data_dir / "snapshots"
        self._diffs_dir = self.data_dir / "diffs"
        self._plans_dir = self.data_dir / "plans"

        # ensure directories
        for d in [self._snapshots_dir, self._diffs_dir, self._plans_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._conn = self._init_db()

    def _init_db(self) -> sqlite3.Connection:
        """Initialize SQLite database and schema."""
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level="DEFERRED",
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        logger.info("TrackerStorage initialized at %s", self._db_path)
        return conn

    def _ensure_open(self) -> None:
        """Raise if the storage has already been closed."""
        if self._conn is None:
            raise RuntimeError("TrackerStorage is already closed")

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("TrackerStorage closed")

    def __enter__(self) -> TrackerStorage:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # -- Snapshots -----------------------------------------------------------

    def save_snapshot(self, snapshot: PortfolioSnapshot) -> None:
        """Persist a portfolio snapshot."""
        self._ensure_open()
        # Write JSON payload
        json_path = self._snapshot_json_path(snapshot)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Insert metadata into SQLite
        self._conn.execute(
            """
            INSERT OR REPLACE INTO snapshots
                (snapshot_id, source, author_id, author_name, portfolio_code,
                 snapshot_time, fetch_time, holding_count, total_value, json_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.source.value,
                snapshot.author_id,
                snapshot.author_name,
                snapshot.portfolio_code,
                snapshot.snapshot_time.isoformat(),
                snapshot.fetch_time.isoformat(),
                len(snapshot.holdings),
                str(snapshot.total_value) if snapshot.total_value is not None else None,
                str(json_path.relative_to(self.data_dir)),
            ),
        )
        self._conn.commit()
        logger.info("Saved snapshot %s for %s", snapshot.snapshot_id, snapshot.author_name)

    def get_snapshot(self, snapshot_id: str) -> PortfolioSnapshot | None:
        """Load a snapshot by ID."""
        self._ensure_open()
        row = self._conn.execute(
            "SELECT json_path FROM snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        return self._load_snapshot_json(row["json_path"])

    def get_latest_snapshot(self, author_id: str) -> PortfolioSnapshot | None:
        """Get the most recent snapshot for an author."""
        self._ensure_open()
        row = self._conn.execute(
            """
            SELECT json_path FROM snapshots
            WHERE author_id = ?
            ORDER BY snapshot_time DESC
            LIMIT 1
            """,
            (author_id,),
        ).fetchone()
        if row is None:
            return None
        return self._load_snapshot_json(row["json_path"])

    def get_snapshots(
        self,
        author_id: str,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[PortfolioSnapshot]:
        """Get snapshots for an author, newest first."""
        self._ensure_open()
        if since:
            rows = self._conn.execute(
                """
                SELECT json_path FROM snapshots
                WHERE author_id = ? AND snapshot_time >= ?
                ORDER BY snapshot_time DESC
                LIMIT ?
                """,
                (author_id, since.isoformat(), limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT json_path FROM snapshots
                WHERE author_id = ?
                ORDER BY snapshot_time DESC
                LIMIT ?
                """,
                (author_id, limit),
            ).fetchall()

        return [s for r in rows if (s := self._load_snapshot_json(r["json_path"])) is not None]

    def list_snapshot_meta(
        self,
        author_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List snapshot metadata (without loading full payloads)."""
        self._ensure_open()
        if author_id:
            rows = self._conn.execute(
                """
                SELECT snapshot_id, source, author_id, author_name,
                       snapshot_time, fetch_time, holding_count
                FROM snapshots
                WHERE author_id = ?
                ORDER BY snapshot_time DESC
                LIMIT ?
                """,
                (author_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT snapshot_id, source, author_id, author_name,
                       snapshot_time, fetch_time, holding_count
                FROM snapshots
                ORDER BY snapshot_time DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [dict(r) for r in rows]

    # -- Diffs ---------------------------------------------------------------

    def save_diff(self, diff: PortfolioDiff) -> None:
        """Persist a portfolio diff."""
        self._ensure_open()
        json_path = self._diff_json_path(diff)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(diff.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        change_count = sum(1 for c in diff.changes if c.change_type.value != "UNCHANGED")

        self._conn.execute(
            """
            INSERT INTO diffs
                (author_id, from_snapshot_id, to_snapshot_id,
                 from_time, to_time, has_changes, change_count, json_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                diff.author_id,
                diff.from_snapshot_id,
                diff.to_snapshot_id,
                diff.from_time.isoformat(),
                diff.to_time.isoformat(),
                1 if diff.has_changes else 0,
                change_count,
                str(json_path.relative_to(self.data_dir)),
            ),
        )
        self._conn.commit()
        logger.info(
            "Saved diff for %s: %s → %s (%d changes)",
            diff.author_name,
            diff.from_snapshot_id[:8],
            diff.to_snapshot_id[:8],
            change_count,
        )

    def get_recent_diffs(
        self,
        author_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List recent diff metadata."""
        self._ensure_open()
        if author_id:
            rows = self._conn.execute(
                """
                SELECT * FROM diffs
                WHERE author_id = ?
                ORDER BY to_time DESC
                LIMIT ?
                """,
                (author_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM diffs
                ORDER BY to_time DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [dict(r) for r in rows]

    # -- Trade Plans ---------------------------------------------------------

    def save_trade_plan(self, plan: TradePlan) -> None:
        """Persist a trade plan."""
        self._ensure_open()
        json_path = self._plan_json_path(plan)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self._conn.execute(
            """
            INSERT OR REPLACE INTO trade_plans
                (plan_id, symbol, name, direction, status, confidence,
                 stop_loss_price, stop_loss_pct, target_position_pct,
                 created_at, executed_at, closed_at, json_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.plan_id,
                plan.symbol,
                plan.name,
                plan.direction.value,
                plan.status.value,
                plan.confidence.value,
                plan.stop_loss_price,
                plan.stop_loss_pct,
                plan.target_position_pct,
                plan.created_at.isoformat(),
                plan.executed_at.isoformat() if plan.executed_at else None,
                plan.closed_at.isoformat() if plan.closed_at else None,
                str(json_path.relative_to(self.data_dir)),
            ),
        )
        self._conn.commit()
        logger.info("Saved trade plan %s (%s %s)", plan.plan_id, plan.direction.value, plan.symbol)

    def get_trade_plan(self, plan_id: str) -> TradePlan | None:
        """Load a trade plan by ID."""
        self._ensure_open()
        row = self._conn.execute(
            "SELECT json_path FROM trade_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            return None
        return self._load_plan_json(row["json_path"])

    def get_trade_plans(
        self,
        status: str | None = None,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[TradePlan]:
        """Query trade plans with optional filters."""
        self._ensure_open()
        conditions: list[str] = []
        params: list[Any] = []

        if status:
            conditions.append("status = ?")
            params.append(status)
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = self._conn.execute(
            f"""
            SELECT json_path FROM trade_plans
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        return [p for r in rows if (p := self._load_plan_json(r["json_path"])) is not None]

    # -- Helpers -------------------------------------------------------------

    def _snapshot_json_path(self, snapshot: PortfolioSnapshot) -> Path:
        date_str = snapshot.snapshot_time.strftime("%Y%m%d")
        return self._snapshots_dir / snapshot.author_id / f"{date_str}_{snapshot.snapshot_id}.json"

    def _diff_json_path(self, diff: PortfolioDiff) -> Path:
        date_str = diff.to_time.strftime("%Y%m%d")
        return self._diffs_dir / diff.author_id / f"{date_str}_{diff.from_snapshot_id[:8]}_{diff.to_snapshot_id[:8]}.json"

    def _plan_json_path(self, plan: TradePlan) -> Path:
        month_str = plan.created_at.strftime("%Y%m")
        return self._plans_dir / month_str / f"{plan.plan_id}.json"

    def _load_snapshot_json(self, relative_path: str) -> PortfolioSnapshot | None:
        full_path = self.data_dir / relative_path
        try:
            data = json.loads(full_path.read_text(encoding="utf-8"))
            return PortfolioSnapshot.from_dict(data)
        except FileNotFoundError:
            logger.error("Snapshot JSON missing (DB record exists but file gone): %s", full_path)
            return None
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error("Failed to parse snapshot JSON %s: %s", full_path, e)
            return None

    def _load_plan_json(self, relative_path: str) -> TradePlan | None:
        full_path = self.data_dir / relative_path
        try:
            data = json.loads(full_path.read_text(encoding="utf-8"))
            return TradePlan.from_dict(data)
        except FileNotFoundError:
            logger.error("Plan JSON missing (DB record exists but file gone): %s", full_path)
            return None
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error("Failed to parse plan JSON %s: %s", full_path, e)
            return None

    # -- Stats ---------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return high-level storage statistics."""
        self._ensure_open()
        snapshot_count = self._conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        diff_count = self._conn.execute("SELECT COUNT(*) FROM diffs").fetchone()[0]
        plan_count = self._conn.execute("SELECT COUNT(*) FROM trade_plans").fetchone()[0]

        authors = self._conn.execute(
            "SELECT DISTINCT author_id, author_name FROM snapshots"
        ).fetchall()

        return {
            "snapshot_count": snapshot_count,
            "diff_count": diff_count,
            "plan_count": plan_count,
            "tracked_authors": [dict(a) for a in authors],
            "db_path": str(self._db_path),
            "data_dir": str(self.data_dir),
        }
