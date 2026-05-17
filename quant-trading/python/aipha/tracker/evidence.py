"""Evidence snapshot storage for library collectors.

Each time a collector runs, it produces an EvidenceSnapshot that records:
- What was fetched (raw HTML/text excerpt, URL, timestamp)
- What was parsed (structured fields extracted)
- What went wrong (failure type and message)
- Whether the result is consistent with prior snapshots

This enables full audit trail for any performance claim in the library.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------


class FailureType(str, Enum):
    """Categorized failure modes for collector operations."""

    NONE = "none"  # Success — no failure
    LINK_DEAD = "link_dead"  # URL returns 404/410/DNS failure
    LOGIN_REQUIRED = "login_required"  # Page redirects to login
    LOGIN_EXPIRED = "login_expired"  # Stored credentials no longer valid
    WAF_BLOCKED = "waf_blocked"  # Cloudflare/WAF challenge page
    RATE_LIMITED = "rate_limited"  # HTTP 429 or equivalent
    FIELDS_MISSING = "fields_missing"  # Page loads but expected fields absent
    PARSE_ERROR = "parse_error"  # HTML structure changed, regex fails
    TIMEOUT = "timeout"  # Request timed out
    NETWORK_ERROR = "network_error"  # Connection refused / DNS / SSL errors
    ACCOUNT_DELETED = "account_deleted"  # Account explicitly removed
    ACCOUNT_STALE = "account_stale"  # Account exists but no updates > threshold


class EvidenceQuality(str, Enum):
    """Quality assessment of the collected evidence."""

    FULL = "full"  # All expected fields present and parseable
    PARTIAL = "partial"  # Some fields missing but core data available
    DEGRADED = "degraded"  # Only minimal data obtained
    FAILED = "failed"  # No usable data obtained


# ---------------------------------------------------------------------------
# EvidenceSnapshot model
# ---------------------------------------------------------------------------


@dataclass
class EvidenceSnapshot:
    """A single point-in-time evidence record from a collector run.

    Attributes:
        snapshot_id: Unique identifier for this snapshot.
        account_id: The library account this evidence belongs to.
        platform: Platform name (e.g. 'darwinex').
        platform_uid: Platform-specific identifier.
        collected_at: When the collection was performed.
        source_url: The URL that was fetched.
        raw_excerpt: First N chars of raw response (HTML/JSON/text).
        parsed_fields: Structured data extracted from the page.
        failure_type: Categorized failure mode (NONE if success).
        error_detail: Human-readable error description.
        evidence_quality: Quality assessment of this snapshot.
        response_status: HTTP status code (0 if no request made).
        response_time_ms: Response time in milliseconds.
        collector_version: Version string of the collector used.
        notes: Free-form notes about this snapshot.
    """

    snapshot_id: str = ""
    account_id: str = ""
    platform: str = ""
    platform_uid: str = ""
    collected_at: datetime = field(default_factory=datetime.now)
    source_url: str = ""
    raw_excerpt: str = ""
    parsed_fields: dict[str, Any] = field(default_factory=dict)
    failure_type: FailureType = FailureType.NONE
    error_detail: str = ""
    evidence_quality: EvidenceQuality = EvidenceQuality.FULL
    response_status: int = 0
    response_time_ms: int = 0
    collector_version: str = "1.0"
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            self.snapshot_id = f"EVD-{uuid.uuid4().hex[:12]}"

    @property
    def is_success(self) -> bool:
        return self.failure_type == FailureType.NONE

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "snapshot_id": self.snapshot_id,
            "account_id": self.account_id,
            "platform": self.platform,
            "platform_uid": self.platform_uid,
            "collected_at": self.collected_at.isoformat(),
            "source_url": self.source_url,
            "raw_excerpt": self.raw_excerpt,
            "parsed_fields": self.parsed_fields,
            "failure_type": self.failure_type.value,
            "error_detail": self.error_detail,
            "evidence_quality": self.evidence_quality.value,
            "response_status": self.response_status,
            "response_time_ms": self.response_time_ms,
            "collector_version": self.collector_version,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvidenceSnapshot:
        """Deserialize from dictionary."""
        return cls(
            snapshot_id=d.get("snapshot_id", ""),
            account_id=d.get("account_id", ""),
            platform=d.get("platform", ""),
            platform_uid=d.get("platform_uid", ""),
            collected_at=datetime.fromisoformat(d["collected_at"]) if d.get("collected_at") else datetime.now(),
            source_url=d.get("source_url", ""),
            raw_excerpt=d.get("raw_excerpt", ""),
            parsed_fields=d.get("parsed_fields", {}),
            failure_type=FailureType(d.get("failure_type", "none")),
            error_detail=d.get("error_detail", ""),
            evidence_quality=EvidenceQuality(d.get("evidence_quality", "full")),
            response_status=d.get("response_status", 0),
            response_time_ms=d.get("response_time_ms", 0),
            collector_version=d.get("collector_version", "1.0"),
            notes=d.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# Evidence storage
# ---------------------------------------------------------------------------

_EVIDENCE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS evidence_snapshots (
    snapshot_id       TEXT PRIMARY KEY,
    account_id        TEXT NOT NULL,
    platform          TEXT NOT NULL,
    platform_uid      TEXT NOT NULL,
    collected_at      TEXT NOT NULL,
    source_url        TEXT DEFAULT '',
    raw_excerpt       TEXT DEFAULT '',
    parsed_fields     TEXT DEFAULT '{}',
    failure_type      TEXT DEFAULT 'none',
    error_detail      TEXT DEFAULT '',
    evidence_quality  TEXT DEFAULT 'full',
    response_status   INTEGER DEFAULT 0,
    response_time_ms  INTEGER DEFAULT 0,
    collector_version TEXT DEFAULT '1.0',
    notes             TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_evidence_account
    ON evidence_snapshots(account_id);

CREATE INDEX IF NOT EXISTS idx_evidence_platform
    ON evidence_snapshots(platform);

CREATE INDEX IF NOT EXISTS idx_evidence_time
    ON evidence_snapshots(collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_evidence_failure
    ON evidence_snapshots(failure_type);
"""

# Maximum raw excerpt length stored (chars)
MAX_RAW_EXCERPT_LENGTH = 5000


class EvidenceStore:
    """SQLite-backed storage for evidence snapshots.

    Usage:
        store = EvidenceStore(data_dir="data/tracker")
        store.save_snapshot(snapshot)
        history = store.get_history("darwinex_THA", limit=10)
        failures = store.get_recent_failures(days=7)
    """

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self._db_path = self.data_dir / "evidence.db"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._conn = self._init_db()

    def _init_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level="DEFERRED",
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        conn.executescript(_EVIDENCE_SCHEMA_SQL)
        conn.commit()
        return conn

    def save_snapshot(self, snapshot: EvidenceSnapshot) -> None:
        """Persist an evidence snapshot."""
        # Truncate raw excerpt to prevent DB bloat
        raw = snapshot.raw_excerpt[:MAX_RAW_EXCERPT_LENGTH]
        self._conn.execute(
            """INSERT OR REPLACE INTO evidence_snapshots
               (snapshot_id, account_id, platform, platform_uid,
                collected_at, source_url, raw_excerpt, parsed_fields,
                failure_type, error_detail, evidence_quality,
                response_status, response_time_ms, collector_version, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot.snapshot_id,
                snapshot.account_id,
                snapshot.platform,
                snapshot.platform_uid,
                snapshot.collected_at.isoformat(),
                snapshot.source_url,
                raw,
                json.dumps(snapshot.parsed_fields, ensure_ascii=False),
                snapshot.failure_type.value,
                snapshot.error_detail,
                snapshot.evidence_quality.value,
                snapshot.response_status,
                snapshot.response_time_ms,
                snapshot.collector_version,
                snapshot.notes,
            ),
        )
        self._conn.commit()

    def get_history(
        self, account_id: str, limit: int = 20
    ) -> list[EvidenceSnapshot]:
        """Get evidence history for a specific account, newest first."""
        rows = self._conn.execute(
            """SELECT * FROM evidence_snapshots
               WHERE account_id = ?
               ORDER BY collected_at DESC LIMIT ?""",
            (account_id, limit),
        ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def get_recent_failures(
        self, days: int = 7, platform: str | None = None
    ) -> list[EvidenceSnapshot]:
        """Get recent failed collection attempts."""
        cutoff = datetime.now().isoformat()[:10]  # simple date comparison
        sql = """SELECT * FROM evidence_snapshots
                 WHERE failure_type != 'none'
                   AND collected_at >= ?"""
        params: list[Any] = [cutoff]
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        sql += " ORDER BY collected_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def get_latest_snapshot(self, account_id: str) -> EvidenceSnapshot | None:
        """Get the most recent snapshot for an account."""
        row = self._conn.execute(
            """SELECT * FROM evidence_snapshots
               WHERE account_id = ?
               ORDER BY collected_at DESC LIMIT 1""",
            (account_id,),
        ).fetchone()
        return self._row_to_snapshot(row) if row else None

    def count_by_failure_type(self, days: int = 30) -> dict[str, int]:
        """Count failures by type in the last N days."""
        from datetime import timedelta

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """SELECT failure_type, COUNT(*) as cnt
               FROM evidence_snapshots
               WHERE collected_at >= ?
               GROUP BY failure_type""",
            (cutoff,),
        ).fetchall()
        return {r["failure_type"]: r["cnt"] for r in rows}

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> EvidenceStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            snapshot_id=row["snapshot_id"],
            account_id=row["account_id"],
            platform=row["platform"],
            platform_uid=row["platform_uid"],
            collected_at=datetime.fromisoformat(row["collected_at"]),
            source_url=row["source_url"],
            raw_excerpt=row["raw_excerpt"],
            parsed_fields=json.loads(row["parsed_fields"]) if row["parsed_fields"] else {},
            failure_type=FailureType(row["failure_type"]),
            error_detail=row["error_detail"],
            evidence_quality=EvidenceQuality(row["evidence_quality"]),
            response_status=row["response_status"],
            response_time_ms=row["response_time_ms"],
            collector_version=row["collector_version"],
            notes=row["notes"],
        )
