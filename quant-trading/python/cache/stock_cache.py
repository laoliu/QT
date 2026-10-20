"""
SQLite-based stock data cache for quant-trading extension.

Provides transparent caching for all data fetched from remote APIs:
  - Stock list (Sina batch)
  - Fundamentals (Xueqiu per-stock)
  - K-line history
  - Code-name mapping

Cache is stored at ~/data/cache/stock_data.db

Usage:
    from cache.stock_cache import StockCache
    cache = StockCache()                   # auto-creates DB
    cache.get_stock_list("2026-02-25")     # returns cached or None
    cache.save_stock_list(data, "2026-02-25")
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


@lru_cache(maxsize=1)
def _cn_calendar():
    from aipha.data.calendar import CNStockCalendar
    return CNStockCalendar()


# ── Cache location ──────────────────────────────────────────────

def _default_cache_dir() -> Path:
    """Return default cache directory: ~/data/cache/"""
    home = Path.home()
    return home / ".quant-trading" / "extensions" / "quant-trading" / "cache"


# ── Trading hours detection (China A-share) ─────────────────────

def _now_utc_naive() -> datetime:
    """Return UTC without tzinfo to preserve the SQLite timestamp format."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now_cst() -> datetime:
    """Return current time in CST (UTC+8), without tzinfo."""
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5  # Mon=0 .. Fri=4


def _is_trading_hours(dt: Optional[datetime] = None) -> bool:
    """Check if current time is within A-share trading hours (9:15-15:05 CST, weekdays)."""
    dt = dt or _now_cst()
    if not _is_weekday(dt):
        return False
    t = dt.hour * 100 + dt.minute
    # 9:15 - 15:05 (a few minutes buffer after close)
    return 915 <= t <= 1505


def _today_date_str(dt: Optional[datetime] = None) -> str:
    """Return today's date as YYYY-MM-DD in CST."""
    dt = dt or _now_cst()
    return dt.strftime("%Y-%m-%d")


def _is_stale(updated_at_str: str, ttl_seconds: int) -> bool:
    """Check if a cached entry has exceeded its TTL."""
    try:
        updated_at = datetime.fromisoformat(updated_at_str)
        if updated_at.tzinfo is None:
            # Legacy SQLite timestamps represent UTC despite lacking an offset.
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - updated_at).total_seconds()
        return age > ttl_seconds
    except (ValueError, TypeError):
        return True


# ── TTL constants ───────────────────────────────────────────────

# During trading hours
TTL_STOCK_LIST_TRADING = 5 * 60          # 5 minutes
TTL_FUNDAMENTALS_TRADING = 30 * 60       # 30 minutes
TTL_KLINE_TODAY_TRADING = 5 * 60         # 5 minutes for today's bar

# After trading hours — data valid until next trading day
TTL_STOCK_LIST_CLOSED = 18 * 3600        # 18 hours (conservative)
TTL_FUNDAMENTALS_CLOSED = 18 * 3600      # 18 hours
TTL_KLINE_HISTORICAL = 365 * 24 * 3600   # ~1 year (effectively permanent)

# Code-name mapping (rarely changes)
TTL_CODE_NAME = 7 * 24 * 3600            # 7 days


# ── SQL Schema ──────────────────────────────────────────────────

_SCHEMA_SQL = """
-- Stock list: batch data from Sina (one row per stock per day)
CREATE TABLE IF NOT EXISTS stock_list (
    code TEXT NOT NULL,
    fetch_date TEXT NOT NULL,
    name TEXT,
    exchange TEXT,
    price REAL,
    change_amt REAL,
    change_pct REAL,
    open_price REAL,
    high REAL,
    low REAL,
    prev_close REAL,
    volume REAL,
    turnover REAL,
    data_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (code, fetch_date)
);

-- Fundamentals: per-stock data from Xueqiu
CREATE TABLE IF NOT EXISTS stock_fundamentals (
    code TEXT NOT NULL,
    fetch_date TEXT NOT NULL,
    pe REAL,
    pe_ttm REAL,
    pe_static REAL,
    pb REAL,
    eps REAL,
    bvps REAL,
    dividend_yield REAL,
    total_market_val REAL,
    total_market_val_yi REAL,
    circulating_market_val REAL,
    circulating_market_val_yi REAL,
    turnover_rate REAL,
    industry TEXT,
    data_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (code, fetch_date)
);

-- K-line history data
CREATE TABLE IF NOT EXISTS kline_data (
    symbol TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'cn_stock',
    period TEXT NOT NULL DEFAULT 'daily',
    trade_date TEXT NOT NULL,
    open_price REAL,
    high REAL,
    low REAL,
    close_price REAL,
    volume INTEGER,
    amount REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, market, period, trade_date)
);

-- Code-name mapping
CREATE TABLE IF NOT EXISTS stock_code_name (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Cache metadata (for tracking batch freshness)
CREATE TABLE IF NOT EXISTS cache_meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_stock_list_date
    ON stock_list(fetch_date);
CREATE INDEX IF NOT EXISTS idx_fundamentals_date
    ON stock_fundamentals(fetch_date);
CREATE INDEX IF NOT EXISTS idx_kline_symbol_date
    ON kline_data(symbol, trade_date);
"""


class StockCache:
    """SQLite-based stock data cache.

    Thread-safe via WAL mode. Designed for transparent read-through caching.
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self._cache_dir = cache_dir or _default_cache_dir()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._cache_dir / "stock_data.db"
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Initialize database and create schema."""
        self._conn = sqlite3.connect(str(self._db_path), timeout=10, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA cache_size=-8000")  # 8MB cache
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        self._quarantine_ambiguous_klines()

    def _quarantine_ambiguous_klines(self):
        """Preserve old rows that may have been fetched from the wrong namespace."""
        from data_providers.symbols import INDEX_CODES

        conn = self._conn
        marker = "stock_index_namespace_v1"
        placeholders = ",".join("?" for _ in INDEX_CODES)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM cache_meta WHERE key = ?", (marker,)).fetchone():
                return
            conn.execute("""CREATE TABLE IF NOT EXISTS kline_data_quarantine AS
                SELECT *, '' AS reason, '' AS quarantined_at FROM kline_data WHERE 0""")
            now = _now_utc_naive().isoformat()
            args = ("ambiguous_stock_index_namespace", now, *sorted(INDEX_CODES))
            cursor = conn.execute(f"""INSERT INTO kline_data_quarantine
                SELECT *, ?, ? FROM kline_data
                WHERE market = 'cn_stock' AND symbol IN ({placeholders})""", args)
            conn.execute(f"""DELETE FROM kline_data
                WHERE market = 'cn_stock' AND symbol IN ({placeholders})""", sorted(INDEX_CODES))
            conn.execute("INSERT INTO cache_meta (key, value, updated_at) VALUES (?, ?, ?)",
                         (marker, str(cursor.rowcount), now))
        if cursor.rowcount:
            print(f"[cache] Quarantined {cursor.rowcount} ambiguous stock/index bars", file=sys.stderr)

    def _ensure_conn(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._init_db()
            assert self._conn is not None
            return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Stock List (Sina batch) ──────────────────────────────────

    def get_stock_list(self, fetch_date: Optional[str] = None) -> Optional[dict[str, dict[str, Any]]]:
        """Get cached stock list for a given date.

        Returns dict of {code: {stock_data}} or None if cache miss/stale.
        """
        conn = self._ensure_conn()
        fetch_date = fetch_date or _today_date_str()

        # Check freshness
        meta_key = f"stock_list:{fetch_date}"
        row = conn.execute(
            "SELECT updated_at FROM cache_meta WHERE key = ?",
            (meta_key,)
        ).fetchone()

        if not row:
            return None

        ttl = TTL_STOCK_LIST_TRADING if _is_trading_hours() else TTL_STOCK_LIST_CLOSED
        if _is_stale(row[0], ttl):
            return None

        # Fetch all stock records for this date
        rows = conn.execute(
            "SELECT code, data_json FROM stock_list WHERE fetch_date = ?",
            (fetch_date,)
        ).fetchall()

        if not rows:
            return None

        result: dict[str, dict[str, Any]] = {}
        for code, data_json in rows:
            try:
                result[code] = json.loads(data_json)
            except (json.JSONDecodeError, TypeError):
                pass

        if not result:
            return None

        return result

    def save_stock_list(self, stock_data: dict[str, dict[str, Any]], fetch_date: Optional[str] = None):
        """Save stock list to cache."""
        conn = self._ensure_conn()
        fetch_date = fetch_date or _today_date_str()
        now = _now_utc_naive().isoformat()

        # Batch insert with upsert
        records = []
        for code, data in stock_data.items():
            records.append((
                code,
                fetch_date,
                data.get("name"),
                data.get("exchange"),
                data.get("price"),
                data.get("change_amt"),
                data.get("change_pct"),
                data.get("open"),
                data.get("high"),
                data.get("low"),
                data.get("prev_close"),
                data.get("volume"),
                data.get("turnover"),
                json.dumps(data, ensure_ascii=False),
                now,
            ))

        conn.executemany("""
            INSERT OR REPLACE INTO stock_list
                (code, fetch_date, name, exchange, price, change_amt, change_pct,
                 open_price, high, low, prev_close, volume, turnover, data_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, records)

        # Update metadata
        conn.execute(
            "INSERT OR REPLACE INTO cache_meta (key, value, updated_at) VALUES (?, ?, ?)",
            (f"stock_list:{fetch_date}", str(len(records)), now)
        )
        conn.commit()
        print(f"[缓存] 已缓存 {len(records)} 只股票列表数据 ({fetch_date})", file=sys.stderr)

    # ── Fundamentals (SQLite + local TuShare read-through) ───────

    def get_fundamentals(
        self,
        codes: list[str],
        fetch_date: Optional[str] = None,
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """Get cached fundamentals for given stock codes.

        Returns (cached_data, missing_codes).
        cached_data: {code: {pe, pb, eps, ...}}
        missing_codes: codes not in cache or stale.
        """
        conn = self._ensure_conn()
        fetch_date = fetch_date or _today_date_str()
        ttl = TTL_FUNDAMENTALS_TRADING if _is_trading_hours() else TTL_FUNDAMENTALS_CLOSED

        cached: dict[str, dict[str, Any]] = {}
        missing: list[str] = []

        # Query in batches to avoid SQLite variable limit
        batch_size = 500
        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"""SELECT code, data_json, updated_at
                    FROM stock_fundamentals
                    WHERE code IN ({placeholders}) AND fetch_date = ?""",
                (*batch, fetch_date)
            ).fetchall()

            found_codes = set()
            for code, data_json, updated_at in rows:
                if not _is_stale(updated_at, ttl):
                    try:
                        cached[code] = json.loads(data_json)
                        found_codes.add(code)
                    except (json.JSONDecodeError, TypeError):
                        pass

            missing.extend(c for c in batch if c not in found_codes)

        # Current dashboard snapshots may reuse local data. Historical research
        # must not receive today's mutable financial cache as PIT evidence.
        if missing and fetch_date == _today_date_str():
            try:
                from cache.local_fundamentals import load_current
                local = load_current(missing, fetch_date)
                if local:
                    self.save_fundamentals(local, fetch_date)
                    cached.update(local)
                    missing = [code for code in missing if code not in local]
            except Exception as exc:
                print(f"[cache] Local fundamental lookup failed: {exc}", file=sys.stderr)
        return cached, missing

    def save_fundamentals(
        self,
        data: dict[str, dict[str, Any]],
        fetch_date: Optional[str] = None,
    ):
        """Save fundamentals data to cache.

        data: {code: {pe, pb, eps, bvps, total_market_val, ...}}
        """
        conn = self._ensure_conn()
        fetch_date = fetch_date or _today_date_str()
        now = _now_utc_naive().isoformat()

        records = []
        for code, d in data.items():
            records.append((
                code,
                fetch_date,
                d.get("pe"),
                d.get("pe_ttm"),
                d.get("pe_static"),
                d.get("pb"),
                d.get("eps"),
                d.get("bvps"),
                d.get("dividend_yield"),
                d.get("total_market_val"),
                d.get("total_market_val_yi"),
                d.get("circulating_market_val"),
                d.get("circulating_market_val_yi"),
                d.get("turnover_rate"),
                d.get("industry"),
                json.dumps(d, ensure_ascii=False),
                now,
            ))

        conn.executemany("""
            INSERT OR REPLACE INTO stock_fundamentals
                (code, fetch_date, pe, pe_ttm, pe_static, pb, eps, bvps,
                 dividend_yield, total_market_val, total_market_val_yi,
                 circulating_market_val, circulating_market_val_yi,
                 turnover_rate, industry, data_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, records)
        conn.commit()
        if records:
            print(f"[缓存] 已缓存 {len(records)} 只股票基本面数据 ({fetch_date})", file=sys.stderr)

    # ── K-line History ───────────────────────────────────────────

    def get_klines(
        self,
        symbol: str,
        market: str = "cn_stock",
        period: str = "daily",
        start_date: str = "",
        end_date: str = "",
    ) -> tuple[list[dict[str, Any]], str, str]:
        """Get cached K-line data for a symbol.

        Returns (bars, cached_start, cached_end).
        bars: list of OHLCV dicts.
        cached_start/cached_end: the date range actually available in cache.
        Returns ([], "", "") if no cached data.
        """
        conn = self._ensure_conn()

        query = """
            SELECT trade_date, open_price, high, low, close_price, volume, amount
            FROM kline_data
            WHERE symbol = ? AND market = ? AND period = ?
        """
        params: list[Any] = [symbol, market, period]

        if start_date:
            query += " AND trade_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND trade_date <= ?"
            params.append(end_date)

        query += " ORDER BY trade_date ASC"
        rows = conn.execute(query, params).fetchall()

        if not rows:
            return [], "", ""

        bars = []
        for trade_date, open_p, high, low, close_p, volume, amount in rows:
            bars.append({
                "timestamp": int(datetime.strptime(trade_date, "%Y-%m-%d").timestamp() * 1000),
                "open": open_p or 0,
                "high": high or 0,
                "low": low or 0,
                "close": close_p or 0,
                "volume": volume or 0,
                "amount": amount or 0,
            })

        cached_start = rows[0][0]
        cached_end = rows[-1][0]
        return bars, cached_start, cached_end

    def save_klines(
        self,
        symbol: str,
        bars: list[dict[str, Any]],
        market: str = "cn_stock",
        period: str = "daily",
    ):
        """Save K-line bars to cache."""
        conn = self._ensure_conn()
        now = _now_utc_naive().isoformat()

        records = []
        for bar in bars:
            ts = bar.get("timestamp", 0)
            if isinstance(ts, (int, float)) and ts > 0:
                trade_date = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            else:
                continue

            records.append((
                symbol, market, period, trade_date,
                bar.get("open", 0),
                bar.get("high", 0),
                bar.get("low", 0),
                bar.get("close", 0),
                int(bar.get("volume", 0)),
                bar.get("amount", 0),
                now,
            ))

        if records:
            with self._lock:
                conn.executemany("""
                    INSERT OR REPLACE INTO kline_data
                        (symbol, market, period, trade_date,
                         open_price, high, low, close_price,
                         volume, amount, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, records)
                conn.commit()
            print(f"[缓存] 已缓存 {symbol} 的 {len(records)} 条K线数据", file=sys.stderr)

    def get_klines_missing_range(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        market: str = "cn_stock",
        period: str = "daily",
        available_dates: Optional[set[str]] = None,
    ) -> list[tuple[str, str]]:
        """Find date ranges not covered in cache (including middle gaps).

        Detects gaps by checking actual cached trade_dates against expected
        trading days. Returns a list of (gap_start, gap_end) date strings
        that need fetching. If cache fully covers the range, returns [].
        """
        conn = self._ensure_conn()

        # Query cached dates within the requested range
        rows = conn.execute(
            """SELECT trade_date FROM kline_data
               WHERE symbol = ? AND market = ? AND period = ?
                 AND trade_date >= ? AND trade_date <= ?
               ORDER BY trade_date ASC""",
            (symbol, market, period, start_date, end_date),
        ).fetchall()

        cached_dates = {r[0] for r in rows}
        cached_dates.update(available_dates or ())
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = min(datetime.strptime(end_date, "%Y-%m-%d").date(), _now_cst().date())
        if start_dt > end_dt:
            return []

        if period == "daily" and market in ("cn_stock", "index", "fund", "convertible_bond"):
            calendar = _cn_calendar()
            gaps = []
            gap_start = gap_end = None
            current = start_dt
            while current <= end_dt:
                day = current.isoformat()
                if calendar.is_trading_day(current):
                    if day not in cached_dates:
                        gap_start = gap_start or day
                        gap_end = day
                    elif gap_start:
                        gaps.append((gap_start, gap_end))
                        gap_start = gap_end = None
                current += timedelta(days=1)
            if gap_start:
                gaps.append((gap_start, gap_end))
            return gaps

        if not cached_dates:
            return [(start_date, end_dt.isoformat())]
        cached_start = min(cached_dates)
        cached_end = max(cached_dates)

        gaps: list[tuple[str, str]] = []

        # Gap before cached data
        if start_date < cached_start:
            gaps.append((start_date, (datetime.fromisoformat(cached_start) - timedelta(days=1)).date().isoformat()))

        # Detect interior gaps: consecutive cached dates >5 calendar days apart
        # (normal weekends = 2-3 days gap; holidays up to ~5 days; anything
        #  larger likely means missing data that should be re-fetched)
        sorted_dates = sorted(cached_dates) if period == "daily" else []
        for i in range(1, len(sorted_dates)):
            prev_dt = datetime.strptime(sorted_dates[i - 1], "%Y-%m-%d")
            curr_dt = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
            gap_days = (curr_dt - prev_dt).days
            if gap_days > 5:
                # There's a suspicious gap — re-fetch this range
                gap_s = (prev_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                gap_e = (curr_dt - timedelta(days=1)).strftime("%Y-%m-%d")
                if gap_s <= gap_e:
                    gaps.append((gap_s, gap_e))

        # Gap after cached data
        if end_dt.isoformat() > cached_end:
            gaps.append(((datetime.fromisoformat(cached_end) + timedelta(days=1)).date().isoformat(), end_dt.isoformat()))

        # Merge overlapping/adjacent gaps
        if len(gaps) > 1:
            gaps.sort()
            merged = [gaps[0]]
            for gs, ge in gaps[1:]:
                if gs <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], ge))
                else:
                    merged.append((gs, ge))
            gaps = merged

        return gaps

    # ── Code-Name Mapping ────────────────────────────────────────

    def get_code_name_map(self) -> Optional[dict[str, str]]:
        """Get cached code→name mapping. Returns None if stale."""
        conn = self._ensure_conn()

        row = conn.execute(
            "SELECT updated_at FROM cache_meta WHERE key = 'code_name_map'"
        ).fetchone()

        if not row or _is_stale(row[0], TTL_CODE_NAME):
            return None

        rows = conn.execute("SELECT code, name FROM stock_code_name").fetchall()
        if not rows:
            return None

        return {code: name for code, name in rows}

    def save_code_name_map(self, mapping: dict[str, str]):
        """Save code→name mapping to cache."""
        conn = self._ensure_conn()
        now = _now_utc_naive().isoformat()

        records = [(code, name, now) for code, name in mapping.items()]
        conn.executemany(
            "INSERT OR REPLACE INTO stock_code_name (code, name, updated_at) VALUES (?, ?, ?)",
            records
        )
        conn.execute(
            "INSERT OR REPLACE INTO cache_meta (key, value, updated_at) VALUES (?, ?, ?)",
            ("code_name_map", str(len(records)), now)
        )
        conn.commit()
        print(f"[缓存] 已缓存 {len(records)} 个股票代码-名称映射", file=sys.stderr)

    def get_stock_name(self, code: str) -> Optional[str]:
        """Get a single stock's name from cache."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT name FROM stock_code_name WHERE code = ?", (code,)
        ).fetchone()
        return row[0] if row else None

    # ── Cache Statistics & Management ────────────────────────────

    def get_meta(self, key: str) -> Optional[str]:
        """Get a value from the cache_meta table."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT value FROM cache_meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Set a value in the cache_meta table."""
        conn = self._ensure_conn()
        now = _now_utc_naive().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO cache_meta (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
        conn.commit()

    def auto_populate(self, allow_remote: bool = False) -> dict[str, int]:
        """Auto-populate stock_list, code_name_map, and fundamentals from existing kline data if empty."""
        conn = self._ensure_conn()
        populated: dict[str, int] = {}

        # Populate stock_list from kline symbols if empty
        sl_count = conn.execute("SELECT COUNT(*) FROM stock_list").fetchone()[0]
        if sl_count == 0:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM kline_data WHERE market = 'cn_stock'"
            ).fetchall()
            if rows:
                stock_data = {}
                for (sym,) in rows:
                    ex = "SH" if sym.startswith("6") else "SZ" if sym.startswith(("0", "3")) else "BJ"
                    stock_data[sym] = {"stock_id": sym, "name": "", "exchange": ex}
                self.save_stock_list(stock_data)
                populated["stock_list"] = len(stock_data)
                print(f"[缓存] 从K线数据自动填充股票列表: {len(stock_data)} ֻ", file=sys.stderr)

        # Populate code_name_map via AKShare if empty
        cn_count = conn.execute("SELECT COUNT(*) FROM stock_code_name").fetchone()[0]
        if cn_count == 0:
            try:
                from cache.local_fundamentals import cache_dir, _read
                path = cache_dir() / "stock_basic.parquet"
                df = _read(path) if path.exists() else None
                mapping = ({str(r["ts_code"])[:6]: str(r["name"]) for _, r in df.iterrows()
                            if isinstance(r.get("name"), str) and r["name"]}
                           if df is not None and {"ts_code", "name"}.issubset(df.columns) else {})
                if not mapping and allow_remote:
                    import akshare as ak
                    df = ak.stock_info_a_code_name()
                    mapping = {str(r["code"]).zfill(6): str(r["name"]) for _, r in df.iterrows()}
                if mapping:
                    self.save_code_name_map(mapping)
                    populated["code_name_map"] = len(mapping)
                    print(f"[缓存] 从AKShare自动填充代码名称映射: {len(mapping)} 条", file=sys.stderr)

                    # Backfill stock_list names from code_name_map
                    sl_rows = conn.execute("SELECT code FROM stock_list WHERE name = '' OR name IS NULL").fetchall()
                    if sl_rows:
                        updates = [(mapping.get(code, ""), code) for (code,) in sl_rows if code in mapping]
                        if updates:
                            conn.executemany(
                                "UPDATE stock_list SET name = ? WHERE code = ?", updates
                            )
                            conn.commit()
                            print(f"[缓存] 回填股票名称: {len(updates)} 条", file=sys.stderr)
            except Exception as e:
                print(f"[缓存] AKShare代码名称映射获取失败: {e}", file=sys.stderr)

        # Populate current snapshots from disk, even when yesterday's rows exist.
        # Reading statistics must not trigger hundreds of network requests.
        try:
            codes = [r[0] for r in conn.execute("""SELECT DISTINCT code FROM stock_list
                UNION SELECT DISTINCT symbol FROM kline_data WHERE market = 'cn_stock'""")]
            loaded, missing = self.get_fundamentals(codes)
            if loaded:
                populated["fundamentals"] = len(loaded)
            if missing and allow_remote:
                from cache.prefetch import prefetch_fundamentals
                prefetch_fundamentals({code: {} for code in missing}, top_n=len(missing))
        except Exception as e:
            print(f"[缓存] 基本面数据获取失败: {e}", file=sys.stderr)

        return populated

    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        conn = self._ensure_conn()

        stock_list_count = conn.execute("SELECT COUNT(DISTINCT code) FROM stock_list").fetchone()[0]
        stock_list_dates = conn.execute("SELECT COUNT(DISTINCT fetch_date) FROM stock_list").fetchone()[0]
        fundamentals_count = conn.execute("SELECT COUNT(DISTINCT code) FROM stock_fundamentals").fetchone()[0]
        kline_records = conn.execute("SELECT COUNT(*) FROM kline_data").fetchone()[0]
        kline_symbols = conn.execute("SELECT COUNT(DISTINCT symbol) FROM kline_data").fetchone()[0]
        code_name_count = conn.execute("SELECT COUNT(*) FROM stock_code_name").fetchone()[0]
        from cache.local_fundamentals import coverage
        local_fundamentals = coverage()
        snapshot_dates = conn.execute("SELECT MAX(fetch_date) FROM stock_fundamentals").fetchone()[0]
        valuation_dates = []
        for (raw,) in conn.execute("SELECT data_json FROM stock_fundamentals WHERE fetch_date = ?", (snapshot_dates,)):
            try:
                date = json.loads(raw).get("valuation_date")
                if date:
                    valuation_dates.append(date)
            except (ValueError, TypeError, AttributeError):
                pass

        # DB file size
        db_size_bytes = os.path.getsize(self._db_path) if os.path.exists(self._db_path) else 0
        db_size_mb = round(db_size_bytes / (1024 * 1024), 2)

        # Latest update times
        meta_rows = conn.execute(
            "SELECT key, value, updated_at FROM cache_meta ORDER BY updated_at DESC"
        ).fetchall()

        return {
            "db_path": str(self._db_path),
            "db_size_mb": db_size_mb,
            "stock_list": {
                "unique_stocks": stock_list_count,
                "cached_dates": stock_list_dates,
            },
            "fundamentals": {
                "unique_stocks": fundamentals_count,
                "snapshot_date": snapshot_dates,
                "oldest_valuation_date": min(valuation_dates, default=None),
                "latest_valuation_date": max(valuation_dates, default=None),
            },
            "local_fundamentals": local_fundamentals,
            "klines": {
                "total_bars": kline_records,
                "unique_symbols": kline_symbols,
            },
            "code_name_map": {
                "entries": code_name_count,
            },
            "cache_meta": [
                {"key": k, "value": v, "updated_at": u} for k, v, u in meta_rows
            ],
        }

    def symbols_detail(self) -> list[dict[str, Any]]:
        """Get per-symbol kline cache detail (symbol, period, count, date range)."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT symbol, period, COUNT(*) as bars, MIN(trade_date) as min_date, MAX(trade_date) as max_date "
            "FROM kline_data GROUP BY symbol, period ORDER BY symbol, period"
        ).fetchall()
        return [
            {"symbol": r[0], "interval": r[1], "bars": r[2], "dateRange": {"min": r[3], "max": r[4]}}
            for r in rows
        ]

    def stale_kline_symbols(self, stale_days: int = 2, market: str = "cn_stock",
                            period: str = "daily") -> list[str]:
        """Return symbols whose latest cached bar is older than stale_days."""
        conn = self._ensure_conn()
        cutoff = (_now_cst() - timedelta(days=stale_days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            """SELECT symbol, MAX(trade_date) as latest
               FROM kline_data
               WHERE market = ? AND period = ?
               GROUP BY symbol
               HAVING latest < ?
               ORDER BY latest ASC""",
            (market, period, cutoff),
        ).fetchall()
        return [r[0] for r in rows]

    def prewarm_klines(
        self,
        symbols: list[str] | None = None,
        days: int = 120,
        market: str = "cn_stock",
        max_workers: int = 8,
    ) -> dict[str, int]:
        """Pre-warm K-line cache for given symbols (or all stale ones).

        Fetches only missing/stale data using incremental gap detection.
        Returns {"fetched": N, "cached_hit": M, "failed": F}.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if symbols is None:
            # Default: refresh all stale symbols in cache
            symbols = self.stale_kline_symbols(stale_days=2, market=market)
            if not symbols:
                print("[预热] 所有缓存K线数据均为最新", file=sys.stderr)
                return {"fetched": 0, "cached_hit": 0, "failed": 0}

        end_date = _now_cst().strftime("%Y-%m-%d")
        start_dt = _now_cst() - timedelta(days=int(days * 1.5))
        start_date = start_dt.strftime("%Y-%m-%d")

        # Lazy import to avoid circular deps
        try:
            import importlib
            akp = importlib.import_module("data_providers.akshare_provider")
            _get_klines = akp.get_klines
        except ImportError:
            from data_providers.akshare_provider import get_klines as _get_klines

        stats = {"fetched": 0, "cached_hit": 0, "failed": 0}
        total = len(symbols)
        print(f"[预热] 开始预热 {total} 只股票K线 (并发×{max_workers}) ...", file=sys.stderr)

        def _warm_one(sym: str):
            try:
                bars = _get_klines(sym, "1d", start_date, end_date, market)
                return sym, len(bars) if bars else 0, None
            except Exception as e:
                return sym, 0, str(e)

        done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_warm_one, s): s for s in symbols}
            for fut in as_completed(futures):
                sym, count, err = fut.result()
                done += 1
                if err:
                    stats["failed"] += 1
                elif count > 0:
                    stats["fetched"] += 1
                else:
                    stats["cached_hit"] += 1
                if done % 50 == 0 or done == total:
                    print(f"[预热] 进度 {done}/{total}  成功:{stats['fetched']}  "
                          f"缓存命中:{stats['cached_hit']}  失败:{stats['failed']}", file=sys.stderr)

        print(f"[预热] 完成: {stats}", file=sys.stderr)
        return stats

    def clear(self, table: Optional[str] = None):
        """Clear cache data.

        If table is specified, only clear that table.
        Otherwise, clear all tables.
        """
        conn = self._ensure_conn()
        valid_tables = ["stock_list", "stock_fundamentals", "kline_data", "stock_code_name", "cache_meta"]

        if table:
            if table not in valid_tables:
                raise ValueError(f"Invalid table: {table}. Valid: {valid_tables}")
            conn.execute(f"DELETE FROM {table}")
            if table != "cache_meta":
                # Also clean related meta entries
                conn.execute("DELETE FROM cache_meta WHERE key LIKE ?", (f"{table}%",))
        else:
            for t in valid_tables:
                conn.execute(f"DELETE FROM {t}")

        conn.commit()
        conn.execute("VACUUM")
        print(f"[缓存] 已清除缓存 {'(' + table + ')' if table else '(全部)'}", file=sys.stderr)

    def cleanup_old(self, days: int = 30):
        """Remove cache entries older than N days."""
        conn = self._ensure_conn()
        cutoff = (_now_utc_naive() - timedelta(days=days)).isoformat()

        for table in ["stock_list", "stock_fundamentals"]:
            conn.execute(f"DELETE FROM {table} WHERE updated_at < ?", (cutoff,))

        conn.execute("DELETE FROM cache_meta WHERE updated_at < ?", (cutoff,))
        conn.commit()
        conn.execute("VACUUM")
        print(f"[缓存] 已清除 {days} 天前的旧缓存", file=sys.stderr)


# ── Singleton pattern ───────────────────────────────────────────

_cache_instance: Optional[StockCache] = None


def get_cache() -> StockCache:
    """Get or create the global StockCache instance."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = StockCache()
    return _cache_instance


# ── CLI entry for cache management ──────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    config = json.loads(sys.stdin.read())
    action = config.get("action", "stats")
    cache = get_cache()

    if action == "stats":
        result = cache.stats()
    elif action == "refresh":
        populated = cache.auto_populate()
        stats = cache.stats()
        stats["populated"] = populated
        result = stats
    elif action == "symbols_detail":
        result = cache.symbols_detail()
    elif action == "query_klines":
        symbol = config.get("symbol", "")
        market = config.get("market", "cn_stock")
        period = config.get("period", "daily")
        start_date = config.get("start_date", "")
        end_date = config.get("end_date", "")
        bars, cached_start, cached_end = cache.get_klines(
            symbol, market, period, start_date, end_date
        )
        result = {
            "bars": bars,
            "count": len(bars),
            "cached_start": cached_start,
            "cached_end": cached_end,
        }
    elif action == "clear":
        table = config.get("table")
        cache.clear(table)
        result = {"status": "ok", "cleared": table or "all"}
    elif action == "cleanup":
        days = config.get("days", 30)
        cache.cleanup_old(days)
        result = {"status": "ok", "cleaned_before_days": days}
    elif action == "prewarm":
        symbols = config.get("symbols")  # None = all stale
        days = config.get("days", 120)
        market = config.get("market", "cn_stock")
        max_workers = config.get("max_workers", 8)
        result = cache.prewarm_klines(symbols, days, market, max_workers)
    else:
        result = {"error": f"Unknown action: {action}"}

    print(json.dumps(result, ensure_ascii=False))
