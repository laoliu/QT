"""SQLite storage layer for the Public Quantitative Portfolio Library.

Design:
- SQLite as primary index store (WAL mode, thread-safe)
- JSON files for evidence snapshots and raw data
- Compatible with existing TrackerStorage patterns
- Two tables: library_accounts (L1) + library_archives (L2)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .library_models import (
    ArchiveEntry,
    ArchiveStatus,
    EvidenceLevel,
    FetchStability,
    LibraryAccount,
    Platform,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_LIBRARY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS library_accounts (
    account_id        TEXT PRIMARY KEY,
    platform          TEXT NOT NULL,
    platform_uid      TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    profile_url       TEXT DEFAULT '',
    portfolio_name    TEXT DEFAULT '',
    portfolio_url     TEXT DEFAULT '',
    asset_classes     TEXT DEFAULT '[]',
    strategy_tags     TEXT DEFAULT '[]',
    requires_login    INTEGER DEFAULT 0,
    last_update_seen  TEXT DEFAULT '',
    perf_summary      TEXT DEFAULT '{}',
    followers_copiers INTEGER DEFAULT 0,
    fetch_stability   TEXT DEFAULT 'manual_only',
    l1_score          REAL DEFAULT 0.0,
    l2_promoted       INTEGER DEFAULT 0,
    notes             TEXT DEFAULT '',
    source_verified_at TEXT DEFAULT '',
    evidence_level    TEXT DEFAULT 'D',
    strategy_description TEXT DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lib_platform
    ON library_accounts(platform);

CREATE INDEX IF NOT EXISTS idx_lib_score
    ON library_accounts(l1_score DESC);

CREATE INDEX IF NOT EXISTS idx_lib_l2
    ON library_accounts(l2_promoted);

CREATE TABLE IF NOT EXISTS library_archives (
    archive_id           TEXT PRIMARY KEY,
    account_id           TEXT NOT NULL,
    archive_path         TEXT NOT NULL,
    methodology_summary  TEXT DEFAULT '',
    factor_exposure      TEXT DEFAULT '{}',
    replicability_score  REAL DEFAULT 0.0,
    last_reviewed        TEXT DEFAULT '',
    reviewer             TEXT DEFAULT '',
    status               TEXT DEFAULT 'active',
    FOREIGN KEY (account_id) REFERENCES library_accounts(account_id)
);

CREATE INDEX IF NOT EXISTS idx_archive_account
    ON library_archives(account_id);
"""


# ---------------------------------------------------------------------------
# LibraryStorage
# ---------------------------------------------------------------------------


class LibraryStorage:
    """Manages persistence for the portfolio library index.

    Usage:
        storage = LibraryStorage(data_dir="data/tracker")
        storage.upsert_account(account)
        results = storage.search_accounts(platform="xueqiu", min_score=3.0)
    """

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self._db_path = self.data_dir / "library.db"
        self.data_dir.mkdir(parents=True, exist_ok=True)
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
        conn.executescript(_LIBRARY_SCHEMA_SQL)
        # Migration: add new columns if upgrading from earlier schema
        self._migrate_add_columns(conn)
        conn.commit()
        logger.info("LibraryStorage initialized at %s", self._db_path)
        return conn

    @staticmethod
    def _migrate_add_columns(conn: sqlite3.Connection) -> None:
        """Add columns that may be missing from older schema versions."""
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(library_accounts)").fetchall()
        }
        migrations = [
            ("source_verified_at", "TEXT DEFAULT ''"),
            ("evidence_level", "TEXT DEFAULT 'D'"),
            ("strategy_description", "TEXT DEFAULT ''"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing:
                conn.execute(
                    f"ALTER TABLE library_accounts ADD COLUMN {col_name} {col_def}"
                )
                logger.info("Migrated: added column %s", col_name)

    def _ensure_open(self) -> None:
        if self._conn is None:
            raise RuntimeError("LibraryStorage is already closed")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> LibraryStorage:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # -- Account CRUD --------------------------------------------------------

    def upsert_account(self, account: LibraryAccount) -> None:
        """Insert or update an L1 library account."""
        self._ensure_open()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO library_accounts
                (account_id, platform, platform_uid, display_name,
                 profile_url, portfolio_name, portfolio_url,
                 asset_classes, strategy_tags, requires_login,
                 last_update_seen, perf_summary, followers_copiers,
                 fetch_stability, l1_score, l2_promoted, notes,
                 source_verified_at, evidence_level, strategy_description,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account.account_id,
                account.platform.value,
                account.platform_uid,
                account.display_name,
                account.profile_url,
                account.portfolio_name,
                account.portfolio_url,
                json.dumps(list(account.asset_classes), ensure_ascii=False),
                json.dumps(list(account.strategy_tags), ensure_ascii=False),
                int(account.requires_login),
                account.last_update_seen,
                json.dumps(account.perf_summary, ensure_ascii=False),
                account.followers_copiers,
                account.fetch_stability.value,
                account.l1_score,
                int(account.l2_promoted),
                account.notes,
                account.source_verified_at,
                account.evidence_level.value,
                account.strategy_description,
                account.created_at.isoformat(),
                account.updated_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_account(self, account_id: str) -> LibraryAccount | None:
        """Get a single account by ID."""
        self._ensure_open()
        row = self._conn.execute(
            "SELECT * FROM library_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_account(row)

    def search_accounts(
        self,
        platform: str | None = None,
        min_score: float = 0.0,
        l2_only: bool = False,
        limit: int = 100,
    ) -> list[LibraryAccount]:
        """Search accounts with filters."""
        self._ensure_open()
        conditions = ["l1_score >= ?"]
        params: list[Any] = [min_score]
        if platform:
            conditions.append("platform = ?")
            params.append(platform)
        if l2_only:
            conditions.append("l2_promoted = 1")
        where_clause = " AND ".join(conditions)
        params.append(limit)
        rows = self._conn.execute(
            f"SELECT * FROM library_accounts WHERE {where_clause} ORDER BY l1_score DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_account(r) for r in rows]

    def list_all_accounts(self) -> list[LibraryAccount]:
        """List all accounts in the index."""
        self._ensure_open()
        rows = self._conn.execute(
            "SELECT * FROM library_accounts ORDER BY l1_score DESC"
        ).fetchall()
        return [self._row_to_account(r) for r in rows]

    def delete_account(self, account_id: str) -> bool:
        """Delete an account from the index. Returns True if deleted."""
        self._ensure_open()
        cursor = self._conn.execute(
            "DELETE FROM library_accounts WHERE account_id = ?",
            (account_id,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    # -- Archive CRUD --------------------------------------------------------

    def upsert_archive(self, entry: ArchiveEntry) -> None:
        """Insert or update an L2 archive entry."""
        self._ensure_open()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO library_archives
                (archive_id, account_id, archive_path,
                 methodology_summary, factor_exposure,
                 replicability_score, last_reviewed, reviewer, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.archive_id,
                entry.account_id,
                entry.archive_path,
                entry.methodology_summary,
                json.dumps(entry.factor_exposure, ensure_ascii=False),
                entry.replicability_score,
                entry.last_reviewed,
                entry.reviewer,
                entry.status.value,
            ),
        )
        self._conn.commit()

    def get_archives_for_account(self, account_id: str) -> list[ArchiveEntry]:
        """Get all archive entries for an account."""
        self._ensure_open()
        rows = self._conn.execute(
            "SELECT * FROM library_archives WHERE account_id = ? ORDER BY last_reviewed DESC",
            (account_id,),
        ).fetchall()
        return [self._row_to_archive(r) for r in rows]

    # -- Stats ---------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Get summary statistics of the library."""
        self._ensure_open()
        total = self._conn.execute("SELECT COUNT(*) FROM library_accounts").fetchone()[0]
        l2_count = self._conn.execute(
            "SELECT COUNT(*) FROM library_accounts WHERE l2_promoted = 1"
        ).fetchone()[0]
        by_platform = {}
        for row in self._conn.execute(
            "SELECT platform, COUNT(*) as cnt FROM library_accounts GROUP BY platform"
        ).fetchall():
            by_platform[row["platform"]] = row["cnt"]
        return {
            "total_accounts": total,
            "l2_promoted": l2_count,
            "by_platform": by_platform,
        }

    # -- Internal helpers ----------------------------------------------------

    @staticmethod
    def _row_to_account(row: sqlite3.Row) -> LibraryAccount:
        """Convert a database row to a LibraryAccount instance."""
        return LibraryAccount(
            account_id=row["account_id"],
            platform=Platform(row["platform"]),
            platform_uid=row["platform_uid"],
            display_name=row["display_name"],
            profile_url=row["profile_url"] or "",
            portfolio_name=row["portfolio_name"] or "",
            portfolio_url=row["portfolio_url"] or "",
            asset_classes=tuple(json.loads(row["asset_classes"] or "[]")),
            strategy_tags=tuple(json.loads(row["strategy_tags"] or "[]")),
            requires_login=bool(row["requires_login"]),
            last_update_seen=row["last_update_seen"] or "",
            perf_summary=json.loads(row["perf_summary"] or "{}"),
            followers_copiers=row["followers_copiers"] or 0,
            fetch_stability=FetchStability(row["fetch_stability"] or "manual_only"),
            l1_score=row["l1_score"] or 0.0,
            l2_promoted=bool(row["l2_promoted"]),
            notes=row["notes"] or "",
            source_verified_at=row["source_verified_at"] or "",
            evidence_level=EvidenceLevel(row["evidence_level"] or "D"),
            strategy_description=row["strategy_description"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_archive(row: sqlite3.Row) -> ArchiveEntry:
        """Convert a database row to an ArchiveEntry instance."""
        return ArchiveEntry(
            archive_id=row["archive_id"],
            account_id=row["account_id"],
            archive_path=row["archive_path"],
            methodology_summary=row["methodology_summary"] or "",
            factor_exposure=json.loads(row["factor_exposure"] or "{}"),
            replicability_score=row["replicability_score"] or 0.0,
            last_reviewed=row["last_reviewed"] or "",
            reviewer=row["reviewer"] or "",
            status=ArchiveStatus(row["status"] or "active"),
        )
