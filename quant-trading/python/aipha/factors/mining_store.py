"""因子挖掘与评估结果 SQLite 存储层.

表结构:
  - mined_factors: 每个挖掘出的因子 (passed/failed)
  - eval_runs: 评估管道运行记录
  - eval_factors: 评估后的独立因子详情
  - mining_rounds: 挖掘轮次汇总

Usage::
    store = MiningStore()
    store.upsert_mined_factor({...})
    store.upsert_eval_run({...})
    factors = store.query_mined_factors(passed=True)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

_PROJECT_ROOT = PROJECT_ROOT


def _resolve_db_path() -> Path:
    env_val = os.environ.get("AIPHA_DB_PATH", "").strip()
    if env_val:
        return Path(env_val)
    return _PROJECT_ROOT / "output" / "backtest_index.db"


def _to_json(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def _from_json(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


# ── DDL ──

_MINED_FACTORS_DDL = """\
CREATE TABLE IF NOT EXISTS mined_factors (
    factor_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    expression  TEXT NOT NULL,
    category    TEXT,
    method      TEXT,
    ic_mean     REAL DEFAULT 0,
    icir        REAL DEFAULT 0,
    ic_cv       REAL DEFAULT 0,
    post_ic     REAL DEFAULT 0,
    t_stat      REAL DEFAULT 0,
    fitness     REAL DEFAULT 0,
    passed      INTEGER DEFAULT 0,
    failure_reason TEXT,
    round_id    INTEGER DEFAULT 0,
    kb_path     TEXT,
    discovered_at TEXT,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);
"""

_EVAL_RUNS_DDL = """\
CREATE TABLE IF NOT EXISTS eval_runs (
    eval_id       TEXT PRIMARY KEY,
    kb_path       TEXT,
    universe      TEXT,
    start_date    TEXT,
    end_date      TEXT,
    fwd_days      INTEGER DEFAULT 10,
    n_passed      INTEGER DEFAULT 0,
    n_computed    INTEGER DEFAULT 0,
    n_clusters    INTEGER DEFAULT 0,
    n_independent INTEGER DEFAULT 0,
    run_time_s    REAL DEFAULT 0,
    config_json   TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);
"""

_EVAL_FACTORS_DDL = """\
CREATE TABLE IF NOT EXISTS eval_factors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_id       TEXT NOT NULL,
    factor_name   TEXT NOT NULL,
    expression    TEXT,
    kb_icir       REAL DEFAULT 0,
    kb_ic_mean    REAL DEFAULT 0,
    composite_score REAL DEFAULT 0,
    monotonicity  REAL DEFAULT 0,
    avg_turnover  REAL DEFAULT 0,
    wf_oos_icir   REAL DEFAULT 0,
    wf_n_folds    INTEGER DEFAULT 0,
    ic_decay_json TEXT,
    quantile_json TEXT,
    cluster_id    INTEGER DEFAULT 0,
    is_representative INTEGER DEFAULT 1,
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(eval_id, factor_name)
);
"""

_MINING_ROUNDS_DDL = """\
CREATE TABLE IF NOT EXISTS mining_rounds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id    INTEGER NOT NULL,
    kb_path     TEXT,
    generated   INTEGER DEFAULT 0,
    passed      INTEGER DEFAULT 0,
    failed      INTEGER DEFAULT 0,
    dh_score    REAL DEFAULT 0,
    elapsed_s   REAL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_mf_passed ON mined_factors(passed);",
    "CREATE INDEX IF NOT EXISTS idx_mf_icir ON mined_factors(icir);",
    "CREATE INDEX IF NOT EXISTS idx_mf_kb ON mined_factors(kb_path);",
    "CREATE INDEX IF NOT EXISTS idx_ef_eval ON eval_factors(eval_id);",
    "CREATE INDEX IF NOT EXISTS idx_ef_score ON eval_factors(composite_score);",
    "CREATE INDEX IF NOT EXISTS idx_mr_kb ON mining_rounds(kb_path);",
]


class MiningStore:
    """因子挖掘结果 SQLite 存储."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else _resolve_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                _MINED_FACTORS_DDL + _EVAL_RUNS_DDL
                + _EVAL_FACTORS_DDL + _MINING_ROUNDS_DDL
            )
            for idx_sql in _INDEXES:
                conn.execute(idx_sql)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def upsert_mined_factor(self, data: dict) -> None:
        fid = data.get("factor_id") or data.get("name", "")
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mined_factors"
                "(factor_id,name,expression,category,method,"
                "ic_mean,icir,ic_cv,post_ic,t_stat,fitness,"
                "passed,failure_reason,round_id,kb_path,"
                "discovered_at,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                "datetime('now','localtime'))",
                (fid, data.get("name", ""),
                 data.get("expression", ""),
                 data.get("category"),
                 data.get("method"),
                 data.get("ic_mean", 0),
                 data.get("icir", 0),
                 data.get("ic_cv", 0),
                 data.get("post_ic", 0),
                 data.get("t_stat", 0),
                 data.get("fitness", 0),
                 1 if data.get("passed") else 0,
                 data.get("failure_reason"),
                 data.get("round_id", 0),
                 data.get("kb_path"),
                 data.get("discovered_at")),
            )

    def upsert_mined_factors_batch(self, items: list[dict]) -> None:
        for item in items:
            self.upsert_mined_factor(item)

    def upsert_eval_run(self, data: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO eval_runs"
                "(eval_id,kb_path,universe,start_date,end_date,"
                "fwd_days,n_passed,n_computed,n_clusters,"
                "n_independent,run_time_s,config_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,"
                "datetime('now','localtime'))",
                (data.get("eval_id", ""),
                 data.get("kb_path"),
                 data.get("universe"),
                 data.get("start_date") or data.get("start"),
                 data.get("end_date") or data.get("end"),
                 data.get("fwd_days", 10),
                 data.get("n_passed", 0),
                 data.get("n_computed", 0),
                 data.get("n_clusters", 0),
                 data.get("n_independent", 0),
                 data.get("run_time_s", 0),
                 _to_json(data.get("config_json") or data.get("config"))),
            )

    def upsert_eval_factor(self, data: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO eval_factors"
                "(eval_id,factor_name,expression,kb_icir,"
                "kb_ic_mean,composite_score,monotonicity,"
                "avg_turnover,wf_oos_icir,wf_n_folds,"
                "ic_decay_json,quantile_json,cluster_id,"
                "is_representative,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                "datetime('now','localtime'))",
                (data.get("eval_id", ""),
                 data.get("name") or data.get("factor_name", ""),
                 data.get("expression", ""),
                 data.get("kb_icir", 0),
                 data.get("kb_ic_mean", 0),
                 data.get("composite_score", 0),
                 data.get("monotonicity", 0),
                 data.get("avg_turnover", 0),
                 data.get("wf_oos_icir", 0),
                 data.get("wf_n_folds", 0),
                 _to_json(data.get("ic_decay_json") or data.get("ic_decay")),
                 _to_json(data.get("quantile_json") or data.get("quantile")),
                 data.get("cluster_id", 0),
                 1 if data.get("is_representative", True) else 0),
            )

    def upsert_mining_round(self, data: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO mining_rounds"
                "(round_id,kb_path,generated,passed,failed,"
                "dh_score,elapsed_s,created_at) "
                "VALUES(?,?,?,?,?,?,?,datetime('now','localtime'))",
                (data.get("round_id", 0),
                 data.get("kb_path"),
                 data.get("generated", 0),
                 data.get("passed", 0),
                 data.get("failed", 0),
                 data.get("dh_score", 0),
                 data.get("elapsed_s", 0)),
            )

    def query_mined_factors(
        self, *, passed: bool | None = None,
        min_icir: float = 0, limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        clauses = ["abs(icir) >= ?"]
        params: list[Any] = [min_icir]
        if passed is not None:
            clauses.append("passed = ?")
            params.append(1 if passed else 0)
        where = " AND ".join(clauses)
        params.extend([limit, offset])
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM mined_factors WHERE {where} "
                "ORDER BY abs(icir) DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def count_mined_factors(
        self, *, passed: bool | None = None,
        min_icir: float = 0,
    ) -> int:
        clauses = ["abs(icir) >= ?"]
        params: list[Any] = [min_icir]
        if passed is not None:
            clauses.append("passed = ?")
            params.append(1 if passed else 0)
        where = " AND ".join(clauses)
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT count(*) FROM mined_factors WHERE {where}",
                params,
            ).fetchone()
        return row[0] if row else 0

    def query_eval_runs(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM eval_runs "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["config_json"] = _from_json(d.get("config_json"))
            result.append(d)
        return result

    def query_eval_factors(
        self, eval_id: str | None = None,
        min_score: float = 0, limit: int = 100,
    ) -> list[dict]:
        clauses = ["composite_score >= ?"]
        params: list[Any] = [min_score]
        if eval_id:
            clauses.append("eval_id = ?")
            params.append(eval_id)
        where = " AND ".join(clauses)
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM eval_factors WHERE {where} "
                "ORDER BY composite_score DESC LIMIT ?",
                params,
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["ic_decay_json"] = _from_json(d.get("ic_decay_json"))
            d["quantile_json"] = _from_json(d.get("quantile_json"))
            result.append(d)
        return result

    def query_mining_rounds(
        self, kb_path: str | None = None, limit: int = 100,
    ) -> list[dict]:
        if kb_path:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM mining_rounds "
                    "WHERE kb_path = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (kb_path, limit),
                ).fetchall()
        else:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM mining_rounds "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_dashboard_stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute(
                "SELECT count(*) FROM mined_factors"
            ).fetchone()[0]
            passed = conn.execute(
                "SELECT count(*) FROM mined_factors WHERE passed=1"
            ).fetchone()[0]
            evals = conn.execute(
                "SELECT count(*) FROM eval_runs"
            ).fetchone()[0]
            indep = conn.execute(
                "SELECT count(*) FROM eval_factors "
                "WHERE is_representative=1"
            ).fetchone()[0]
            rounds = conn.execute(
                "SELECT count(*) FROM mining_rounds"
            ).fetchone()[0]
            top_row = conn.execute(
                "SELECT max(abs(icir)) FROM mined_factors "
                "WHERE passed=1"
            ).fetchone()
            top_icir = top_row[0] if top_row and top_row[0] else 0
            avg_score_row = conn.execute(
                "SELECT avg(composite_score) FROM eval_factors "
                "WHERE is_representative=1"
            ).fetchone()
            avg_score = round(avg_score_row[0], 3) if (
                avg_score_row and avg_score_row[0]) else 0
        return {
            "total_mined": total,
            "total_passed": passed,
            "pass_rate": round(passed / max(total, 1) * 100, 1),
            "total_eval_runs": evals,
            "total_independent": indep,
            "total_rounds": rounds,
            "top_icir": round(top_icir, 3),
            "avg_composite_score": avg_score,
        }

    def close(self) -> None:
        """No-op for connection-per-call model."""
        pass
