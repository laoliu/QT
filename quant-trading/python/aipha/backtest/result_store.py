"""回测结果 SQLite 持久化存储"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any


class ResultStore:
    """回测结果 SQLite 存储。

    用法::

        store = ResultStore("results.db")
        run_id = store.save_result(backtest_result_dict)
        results = store.list_results(limit=10)
        detail = store.get_result(run_id)
    """

    def __init__(self, db_path: str = "backtest_results.db") -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT NOT NULL,
                    start_date TEXT,
                    end_date TEXT,
                    initial_capital REAL,
                    final_value REAL,
                    total_return REAL,
                    sharpe_ratio REAL,
                    max_drawdown REAL,
                    total_trades INTEGER,
                    total_fees REAL,
                    config_json TEXT,
                    result_json TEXT,
                    created_at REAL DEFAULT (strftime('%s','now')),
                    tags TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_strategy_id ON backtest_runs(strategy_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at ON backtest_runs(created_at)
            """)

    def save_result(self, result: dict[str, Any], config: dict[str, Any] | None = None, tags: str = "") -> int:
        """保存回测结果，返回 run_id。"""
        data = result.get("data", result)
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO backtest_runs
                (strategy_id, start_date, end_date, initial_capital, final_value,
                 total_return, sharpe_ratio, max_drawdown, total_trades, total_fees,
                 config_json, result_json, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get("strategyId", ""),
                data.get("startDate", ""),
                data.get("endDate", ""),
                data.get("initialCapital", 0),
                data.get("finalValue", 0),
                data.get("totalReturn", 0),
                data.get("sharpeRatio", 0),
                data.get("maxDrawdown", 0),
                data.get("totalTrades", 0),
                data.get("totalFees", 0),
                json.dumps(config or {}, ensure_ascii=False, default=str),
                json.dumps(data, ensure_ascii=False, default=str),
                tags,
            ))
            return cursor.lastrowid

    def get_result(self, run_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM backtest_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            return dict(row)

    def list_results(
        self, strategy_id: str | None = None, limit: int = 50, offset: int = 0,
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            if strategy_id:
                rows = conn.execute(
                    "SELECT id, strategy_id, start_date, end_date, total_return, sharpe_ratio, "
                    "max_drawdown, total_trades, created_at, tags "
                    "FROM backtest_runs WHERE strategy_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (strategy_id, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, strategy_id, start_date, end_date, total_return, sharpe_ratio, "
                    "max_drawdown, total_trades, created_at, tags "
                    "FROM backtest_runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            return [dict(r) for r in rows]

    def delete_result(self, run_id: int) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute("DELETE FROM backtest_runs WHERE id = ?", (run_id,))
            return cursor.rowcount > 0

    def count(self) -> int:
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
