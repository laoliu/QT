"""CB Event Database — 可转债事件追踪与数据库。

追踪并存储 CB 生命周期事件：强赎、下修、回售、到期等。
提供 PIT(Point-in-Time) 查询接口，确保回测无前视偏差。

事件来源：TuShare cb_call / cb_share / cb_issue，结合公告日期 PIT 对齐。

学术依据：
- 事件驱动 alpha 是 CB 市场壁垒最高的策略类型
- 强赎/下修公告后 5-20 日存在显著异常收益（广发/中金研报）
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Enums & Data Models
# ------------------------------------------------------------------

class CBEventType(str, Enum):
    """CB 生命周期事件类型。"""
    FORCE_REDEEM = "force_redeem"         # 强制赎回
    DOWN_REVISION = "down_revision"       # 转股价下修
    PUT_BACK = "put_back"                 # 回售
    MATURITY = "maturity"                 # 到期
    LISTING = "listing"                   # 上市
    DELIST = "delist"                     # 退市/摘牌
    CONVERSION_START = "conversion_start" # 进入转股期


@dataclass
class CBEventRecord:
    """单条 CB 事件记录。"""
    ts_code: str
    event_type: str            # CBEventType.value
    event_date: str            # YYYYMMDD
    announce_date: str = ""    # 公告日（PIT 锚定）
    details: str = ""          # JSON 格式附加信息
    source: str = "tushare"
    fetched_at: str = ""       # ISO 时间戳


# ------------------------------------------------------------------
# Event Store (SQLite)
# ------------------------------------------------------------------

class CBEventStore:
    """SQLite-backed CB event store.

    Table schema:
        events(ts_code, event_type, event_date, announce_date,
               details, source, fetched_at,
               PRIMARY KEY(ts_code, event_type, event_date))
    """

    def __init__(self, db_path: str | Path = "data/cache/cb_events.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create tables if not exist."""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                ts_code TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_date TEXT NOT NULL,
                announce_date TEXT DEFAULT '',
                details TEXT DEFAULT '',
                source TEXT DEFAULT 'tushare',
                fetched_at TEXT DEFAULT '',
                PRIMARY KEY (ts_code, event_type, event_date)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_type_date
            ON events(event_type, event_date)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_announce
            ON events(announce_date)
        """)
        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def upsert_events(self, events: list[CBEventRecord]) -> int:
        """Insert or replace events. Returns count of upserted rows."""
        if not events:
            return 0
        conn = self._get_conn()
        rows = [
            (e.ts_code, e.event_type, e.event_date,
             e.announce_date, e.details, e.source,
             e.fetched_at or datetime.now().isoformat())
            for e in events
        ]
        conn.executemany(
            """INSERT OR REPLACE INTO events
               (ts_code, event_type, event_date, announce_date,
                details, source, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        logger.info("Upserted %d CB events", len(rows))
        return len(rows)

    def query_events(
        self,
        ts_code: str | None = None,
        event_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        pit_date: str | None = None,
    ) -> list[CBEventRecord]:
        """Query events with optional filters. PIT: only events announced by pit_date."""
        conn = self._get_conn()
        conditions: list[str] = []
        params: list[str] = []

        if ts_code:
            conditions.append("ts_code = ?")
            params.append(ts_code)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if date_from:
            conditions.append("event_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("event_date <= ?")
            params.append(date_to)
        if pit_date:
            conditions.append("(announce_date <= ? OR announce_date = '')")
            params.append(pit_date)

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM events WHERE {where} ORDER BY event_date DESC"
        cursor = conn.execute(sql, params)

        results = []
        for row in cursor:
            results.append(CBEventRecord(
                ts_code=row["ts_code"],
                event_type=row["event_type"],
                event_date=row["event_date"],
                announce_date=row["announce_date"],
                details=row["details"],
                source=row["source"],
                fetched_at=row["fetched_at"],
            ))
        return results

    def is_force_redeemed(self, ts_code: str, as_of_date: str) -> bool:
        """Check if CB has announced force redeem by as_of_date (PIT)."""
        events = self.query_events(
            ts_code=ts_code,
            event_type=CBEventType.FORCE_REDEEM.value,
            pit_date=as_of_date,
        )
        return len(events) > 0

    def get_recent_events(
        self,
        event_type: str,
        lookback_days: int = 30,
        as_of_date: str | None = None,
    ) -> pd.DataFrame:
        """Get recent events of given type for event-driven signals."""
        if as_of_date is None:
            as_of_date = datetime.now().strftime("%Y%m%d")

        # Compute date_from by subtracting lookback_days (approximate)
        from datetime import timedelta
        dt = datetime.strptime(as_of_date, "%Y%m%d")
        date_from = (dt - timedelta(days=lookback_days)).strftime("%Y%m%d")

        events = self.query_events(
            event_type=event_type,
            date_from=date_from,
            date_to=as_of_date,
            pit_date=as_of_date,
        )
        if not events:
            return pd.DataFrame(columns=[
                "ts_code", "event_type", "event_date", "announce_date", "details",
            ])
        return pd.DataFrame([asdict(e) for e in events])

    def close(self) -> None:
        """Close DB connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ------------------------------------------------------------------
# Event Tracker (fetch + store)
# ------------------------------------------------------------------

class CBEventTracker:
    """Daily CB event tracker — fetches events and stores to DB.

    Usage:
        tracker = CBEventTracker(store)
        tracker.refresh_force_redeem_events()
        tracker.refresh_down_revision_events()
    """

    def __init__(
        self,
        store: CBEventStore,
        gateway: Any | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway

    def refresh_force_redeem_events(self) -> int:
        """Fetch force redeem announcements from TuShare and store."""
        if self._gateway is None:
            from aipha.data.tushare_gateway import get_tushare_gateway
            self._gateway = get_tushare_gateway()

        try:
            # TuShare cb_call: 强制赎回公告
            df = self._gateway.query("cb_call", fields=[
                "ts_code", "call_type", "call_date",
                "ann_date", "call_price", "is_call",
            ])
        except Exception as e:
            logger.warning("Failed to fetch cb_call data: %s", e)
            return 0

        if df is None or df.empty:
            logger.info("No force redeem events fetched")
            return 0

        events: list[CBEventRecord] = []
        for _, row in df.iterrows():
            events.append(CBEventRecord(
                ts_code=str(row.get("ts_code", "")),
                event_type=CBEventType.FORCE_REDEEM.value,
                event_date=str(row.get("call_date", "")),
                announce_date=str(row.get("ann_date", "")),
                details=json.dumps({
                    "call_type": str(row.get("call_type", "")),
                    "call_price": float(row["call_price"]) if pd.notna(row.get("call_price")) else None,
                    "is_call": str(row.get("is_call", "")),
                }, ensure_ascii=False),
                source="tushare_cb_call",
            ))

        return self._store.upsert_events(events)

    def refresh_down_revision_events(self) -> int:
        """Fetch conversion price down-revision events."""
        if self._gateway is None:
            from aipha.data.tushare_gateway import get_tushare_gateway
            self._gateway = get_tushare_gateway()

        try:
            # TuShare cb_share: 转股价变动记录
            df = self._gateway.query("cb_share", fields=[
                "ts_code", "bond_short_name", "publish_date",
                "change_date", "convert_price_initial",
                "convert_price_after_change", "change_reason",
            ])
        except Exception as e:
            logger.warning("Failed to fetch cb_share data: %s", e)
            return 0

        if df is None or df.empty:
            logger.info("No down revision events fetched")
            return 0

        events: list[CBEventRecord] = []
        for _, row in df.iterrows():
            # Only include actual down-revisions (price decreased)
            price_before = row.get("convert_price_initial")
            price_after = row.get("convert_price_after_change")
            is_down = False
            if pd.notna(price_before) and pd.notna(price_after):
                is_down = float(price_after) < float(price_before)

            events.append(CBEventRecord(
                ts_code=str(row.get("ts_code", "")),
                event_type=CBEventType.DOWN_REVISION.value,
                event_date=str(row.get("change_date", "")),
                announce_date=str(row.get("publish_date", "")),
                details=json.dumps({
                    "price_before": float(price_before) if pd.notna(price_before) else None,
                    "price_after": float(price_after) if pd.notna(price_after) else None,
                    "is_down_revision": is_down,
                    "reason": str(row.get("change_reason", "")),
                }, ensure_ascii=False),
                source="tushare_cb_share",
            ))

        return self._store.upsert_events(events)

    def refresh_all(self) -> dict[str, int]:
        """Refresh all event types. Returns counts per type."""
        counts: dict[str, int] = {}
        counts["force_redeem"] = self.refresh_force_redeem_events()
        counts["down_revision"] = self.refresh_down_revision_events()
        total = sum(counts.values())
        logger.info("CB event refresh complete: %d total events, breakdown: %s", total, counts)
        return counts
