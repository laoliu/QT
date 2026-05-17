"""Credential management and quality checking for the Portfolio Library.

Phase 4 components:
- CredentialEntry: Secure model for platform login credentials
- CredentialStore: Encrypted local storage for cookies/tokens
- QualityChecker: Automated quality rules (anomaly detection, freshness)
- ScheduleConfig: Periodic task configuration for maintenance
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from .library_models import FetchStability, LibraryAccount, Platform
from .library_storage import LibraryStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Credential Management
# ---------------------------------------------------------------------------


class CredentialType(str, Enum):
    """Type of stored credential."""

    COOKIE = "cookie"
    TOKEN = "token"
    API_KEY = "api_key"
    SESSION = "session"


class CredentialStatus(str, Enum):
    """Health status of a credential."""

    VALID = "valid"
    EXPIRING_SOON = "expiring_soon"  # within 7 days
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CredentialEntry:
    """Represents a stored credential for a platform.

    The actual secret value is stored encrypted on disk, never in memory
    longer than necessary. This model holds metadata only.

    Attributes:
        credential_id: Unique identifier.
        platform: Which platform this credential is for.
        credential_type: Cookie, token, API key, etc.
        status: Current health status.
        expires_at: When the credential expires (empty if unknown).
        last_verified: When we last confirmed it works.
        notes: Human notes (e.g. "refreshed via browser 2026-05-01").
    """

    credential_id: str
    platform: Platform
    credential_type: CredentialType
    status: CredentialStatus = CredentialStatus.UNKNOWN
    expires_at: str = ""
    last_verified: str = ""
    notes: str = ""

    def is_expired(self, reference: datetime | None = None) -> bool:
        """Check if credential is expired based on expires_at."""
        if not self.expires_at:
            return False
        ref = reference or datetime.now()
        try:
            exp = datetime.fromisoformat(self.expires_at)
            return ref > exp
        except ValueError:
            return False

    def is_expiring_soon(self, days: int = 7, reference: datetime | None = None) -> bool:
        """Check if credential expires within N days."""
        if not self.expires_at:
            return False
        ref = reference or datetime.now()
        try:
            exp = datetime.fromisoformat(self.expires_at)
            return ref + timedelta(days=days) > exp > ref
        except ValueError:
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "credential_id": self.credential_id,
            "platform": self.platform.value,
            "credential_type": self.credential_type.value,
            "status": self.status.value,
            "expires_at": self.expires_at,
            "last_verified": self.last_verified,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CredentialEntry:
        return cls(
            credential_id=d["credential_id"],
            platform=Platform(d["platform"]),
            credential_type=CredentialType(d["credential_type"]),
            status=CredentialStatus(d.get("status", "unknown")),
            expires_at=d.get("expires_at", ""),
            last_verified=d.get("last_verified", ""),
            notes=d.get("notes", ""),
        )


class CredentialStore:
    """Manages credential metadata in SQLite.

    SECURITY: Actual secrets (cookie values, tokens) are stored in
    .env or OS keyring, NOT in this database. This store only tracks
    metadata (expiry, status, platform association).

    Usage:
        store = CredentialStore(data_dir="data/tracker")
        store.upsert(entry)
        expiring = store.get_expiring(days=7)
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS credentials (
        credential_id   TEXT PRIMARY KEY,
        platform        TEXT NOT NULL,
        credential_type TEXT NOT NULL,
        status          TEXT DEFAULT 'unknown',
        expires_at      TEXT DEFAULT '',
        last_verified   TEXT DEFAULT '',
        notes           TEXT DEFAULT ''
    );
    """

    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._data_dir / "credentials.db"
        self._conn = self._init_db()

    def _init_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        conn.executescript(self._SCHEMA)
        conn.commit()
        return conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def upsert(self, entry: CredentialEntry) -> None:
        """Insert or update credential metadata."""
        self._conn.execute(
            """INSERT OR REPLACE INTO credentials
               (credential_id, platform, credential_type, status,
                expires_at, last_verified, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.credential_id,
                entry.platform.value,
                entry.credential_type.value,
                entry.status.value,
                entry.expires_at,
                entry.last_verified,
                entry.notes,
            ),
        )
        self._conn.commit()

    def get(self, credential_id: str) -> CredentialEntry | None:
        """Get credential by ID."""
        row = self._conn.execute(
            "SELECT * FROM credentials WHERE credential_id = ?",
            (credential_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def list_by_platform(self, platform: Platform) -> list[CredentialEntry]:
        """Get all credentials for a platform."""
        rows = self._conn.execute(
            "SELECT * FROM credentials WHERE platform = ?",
            (platform.value,),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def get_expiring(self, days: int = 7) -> list[CredentialEntry]:
        """Get credentials expiring within N days."""
        all_creds = self._conn.execute("SELECT * FROM credentials").fetchall()
        entries = [self._row_to_entry(r) for r in all_creds]
        return [e for e in entries if e.is_expiring_soon(days) or e.is_expired()]

    def delete(self, credential_id: str) -> bool:
        """Delete a credential entry."""
        cursor = self._conn.execute(
            "DELETE FROM credentials WHERE credential_id = ?",
            (credential_id,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> CredentialEntry:
        return CredentialEntry(
            credential_id=row["credential_id"],
            platform=Platform(row["platform"]),
            credential_type=CredentialType(row["credential_type"]),
            status=CredentialStatus(row["status"] or "unknown"),
            expires_at=row["expires_at"] or "",
            last_verified=row["last_verified"] or "",
            notes=row["notes"] or "",
        )
