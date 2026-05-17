"""财务数据 Provider。

提供 F-Score / ROIC / EVtoEBIT 等衍生指标，
基于 TuShare 财务报表数据 + SQLite 缓存。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = Path("data/cache/financials")


class FinancialProvider:
    """财务数据 Provider — 衍生基本面指标。"""

    def __init__(
        self,
        cache_dir: str | Path | None = None,
    ) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._cache_dir / "financials.db"
        self._conn: sqlite3.Connection | None = None

    # ---------- lifecycle ----------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_tables()
        return self._conn

    def _init_tables(self) -> None:
        self._get_conn().executescript("""
            CREATE TABLE IF NOT EXISTS income (
                ts_code TEXT, end_date TEXT,
                revenue REAL, net_profit REAL, gross_profit REAL,
                operate_profit REAL, ebit REAL,
                PRIMARY KEY (ts_code, end_date)
            );
            CREATE TABLE IF NOT EXISTS balance (
                ts_code TEXT, end_date TEXT,
                total_assets REAL, total_liab REAL,
                total_equity REAL, current_assets REAL,
                current_liab REAL, fix_assets REAL,
                invested_capital REAL,
                PRIMARY KEY (ts_code, end_date)
            );
            CREATE TABLE IF NOT EXISTS cashflow (
                ts_code TEXT, end_date TEXT,
                operate_cf REAL, invest_cf REAL, finance_cf REAL,
                PRIMARY KEY (ts_code, end_date)
            );
        """)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ---------- derived metrics ----------

    def get_fscore_inputs(
        self, ts_code: str, end_date: str,
    ) -> dict[str, Any]:
        """获取 Piotroski F-Score 所需的 9 项输入。"""
        cur = self._get_current(ts_code, end_date)
        prev = self._get_previous(ts_code, end_date)
        if not cur:
            return {}
        ta = cur.get("total_assets", 1)
        roa = cur.get("net_profit", 0) / ta if ta else 0
        roa_prev = 0
        if prev:
            ta_prev = prev.get("total_assets", 1)
            roa_prev = prev.get("net_profit", 0) / ta_prev if ta_prev else 0
        return {
            "roa": roa,
            "roa_prev": roa_prev,
            "operating_cf": cur.get("operate_cf", 0),
            "net_profit": cur.get("net_profit", 0),
            "debt_ratio": self._debt_ratio(cur),
            "debt_ratio_prev": self._debt_ratio(prev) if prev else 0.5,
            "current_ratio": self._current_ratio(cur),
            "current_ratio_prev": (
                self._current_ratio(prev) if prev else 1.0
            ),
            "gross_margin": self._gross_margin(cur),
            "gross_margin_prev": (
                self._gross_margin(prev) if prev else 0
            ),
            "asset_turnover": self._asset_turnover(cur),
            "asset_turnover_prev": (
                self._asset_turnover(prev) if prev else 0
            ),
        }

    def get_roic(
        self, ts_code: str, end_date: str,
    ) -> float | None:
        """Return on Invested Capital = EBIT×(1-tax) / IC."""
        cur = self._get_current(ts_code, end_date)
        if not cur:
            return None
        ebit = cur.get("ebit", 0)
        ic = cur.get("invested_capital", 0)
        if not ic:
            return None
        nopat = ebit * (1 - 0.25)  # 25% corporate tax
        return nopat / ic

    def get_ev_to_ebit(
        self, ts_code: str, end_date: str,
        market_cap: float | None = None,
    ) -> float | None:
        """Enterprise Value / EBIT."""
        cur = self._get_current(ts_code, end_date)
        if not cur or not market_cap:
            return None
        ebit = cur.get("ebit", 0)
        if not ebit:
            return None
        total_liab = cur.get("total_liab", 0)
        ev = market_cap + total_liab
        return ev / ebit

    # ---------- internal ----------

    def _get_current(
        self, ts_code: str, end_date: str,
    ) -> dict[str, Any] | None:
        """获取指定报告期或最近报告期数据。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM income WHERE ts_code=? AND end_date<=? "
            "ORDER BY end_date DESC LIMIT 1",
            (ts_code, end_date),
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in conn.execute("SELECT * FROM income LIMIT 0").description]
        inc = dict(zip(cols, row))
        # merge balance
        brow = conn.execute(
            "SELECT * FROM balance WHERE ts_code=? AND end_date<=? "
            "ORDER BY end_date DESC LIMIT 1",
            (ts_code, end_date),
        ).fetchone()
        if brow:
            bcols = [d[0] for d in conn.execute("SELECT * FROM balance LIMIT 0").description]
            inc.update(dict(zip(bcols, brow)))
        # merge cashflow
        crow = conn.execute(
            "SELECT * FROM cashflow WHERE ts_code=? AND end_date<=? "
            "ORDER BY end_date DESC LIMIT 1",
            (ts_code, end_date),
        ).fetchone()
        if crow:
            ccols = [d[0] for d in conn.execute("SELECT * FROM cashflow LIMIT 0").description]
            inc.update(dict(zip(ccols, crow)))
        return inc

    def _get_previous(
        self, ts_code: str, end_date: str,
    ) -> dict[str, Any] | None:
        """获取上一报告期数据（skip 1）。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT end_date FROM income WHERE ts_code=? AND end_date<=? "
            "ORDER BY end_date DESC LIMIT 2",
            (ts_code, end_date),
        ).fetchall()
        if len(rows) < 2:
            return None
        prev_date = rows[1][0]
        return self._get_current(ts_code, prev_date)

    @staticmethod
    def _debt_ratio(data: dict[str, Any]) -> float:
        ta = data.get("total_assets", 0)
        tl = data.get("total_liab", 0)
        return tl / ta if ta else 0.5

    @staticmethod
    def _current_ratio(data: dict[str, Any]) -> float:
        ca = data.get("current_assets", 0)
        cl = data.get("current_liab", 0)
        return ca / cl if cl else 1.0

    @staticmethod
    def _gross_margin(data: dict[str, Any]) -> float:
        rev = data.get("revenue", 0)
        gp = data.get("gross_profit", 0)
        return gp / rev if rev else 0.0

    @staticmethod
    def _asset_turnover(data: dict[str, Any]) -> float:
        rev = data.get("revenue", 0)
        ta = data.get("total_assets", 0)
        return rev / ta if ta else 0.0
