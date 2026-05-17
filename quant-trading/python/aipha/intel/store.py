"""Intel Store — SQLite FTS5 新闻存储与全文检索。

持久化存储新闻/研报，提供全文搜索。
遵循 docs/design/20_specs/intel_advisory_qmt_architecture.md §2.5。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from aipha.intel.models import CredibilityTier, IntelType, NewsItem

logger = logging.getLogger(__name__)

_DEFAULT_DB = "data/cache/intel_store.db"


class IntelStore:
    """SQLite FTS5 新闻存储。"""

    def __init__(self, db_path: str = _DEFAULT_DB) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                content TEXT DEFAULT '',
                url TEXT DEFAULT '',
                intel_type TEXT DEFAULT 'news',
                symbols TEXT DEFAULT '',
                published_at TEXT NOT NULL,
                credibility_tier INTEGER DEFAULT 3,
                content_hash TEXT UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # FTS5 虚拟表
        self._conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS news_fts
            USING fts5(title, content, source, content='news',
                       content_rowid='id')
        """)
        self._conn.commit()

    def insert(self, item: NewsItem) -> bool:
        """插入新闻（去重：content_hash）。"""
        if self._conn is None:
            return False
        try:
            cursor = self._conn.execute(
                """INSERT OR IGNORE INTO news
                   (title, source, content, url, intel_type,
                    symbols, published_at, credibility_tier, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.title, item.source, item.content,
                    item.url, item.intel_type.value,
                    ",".join(item.symbols),
                    item.published_at.isoformat(),
                    item.credibility_tier.value,
                    item.content_hash,
                ),
            )
            self._conn.commit()
            return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    def insert_batch(self, items: list[NewsItem]) -> int:
        """批量插入。返回成功数。"""
        count = 0
        for item in items:
            if self.insert(item):
                count += 1
        return count

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """FTS5 全文搜索。"""
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                """SELECT n.id, n.title, n.source, n.content,
                          n.published_at, n.symbols, n.credibility_tier
                   FROM news n
                   JOIN news_fts f ON n.id = f.rowid
                   WHERE news_fts MATCH ?
                   ORDER BY n.published_at DESC
                   LIMIT ?""",
                (query, limit),
            ).fetchall()
            return [
                {
                    "id": r[0], "title": r[1], "source": r[2],
                    "content": r[3], "published_at": r[4],
                    "symbols": r[5], "credibility_tier": r[6],
                }
                for r in rows
            ]
        except Exception:
            logger.debug("FTS search failed for '%s'", query)
            return []

    def get_recent(
        self, limit: int = 50, symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        """获取最新新闻。"""
        if self._conn is None:
            return []
        if symbol:
            rows = self._conn.execute(
                """SELECT id, title, source, content, published_at,
                          symbols, credibility_tier
                   FROM news WHERE symbols LIKE ?
                   ORDER BY published_at DESC LIMIT ?""",
                (f"%{symbol}%", limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT id, title, source, content, published_at,
                          symbols, credibility_tier
                   FROM news ORDER BY published_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0], "title": r[1], "source": r[2],
                "content": r[3], "published_at": r[4],
                "symbols": r[5], "credibility_tier": r[6],
            }
            for r in rows
        ]

    def count(self) -> int:
        if self._conn is None:
            return 0
        return self._conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
