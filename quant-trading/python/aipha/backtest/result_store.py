"""回测结果索引存储层 (SQLite)

提供结构化索引，替代文件遍历扫描模式。
文件产物（config.yaml / fingerprint.json / HTML / audit）保留在文件系统，
SQLite 仅作索引层提供快速查询。

设计原则:
- 零基础设施：Python 内置 sqlite3，单 .db 文件
- 向后兼容：summary.json 等文件照常写入，DB 为增量索引
- 统一入口：所有回测/WF/Sweep 结果通过同一 API 注册
- 幂等写入：相同 run_id 重复插入时 REPLACE（不报错）

Schema 版本管理:
- SCHEMA_VERSION 常量控制; 升级时追加 migration 函数
- 应用启动自动检查版本并迁移

Usage::

    from aipha.backtest.result_store import ResultStore

    store = ResultStore()                       # 默认 output/backtest_index.db
    store.upsert_backtest_run(run_dict)         # 写入/更新
    runs = store.query_runs(strategy_name="small_size", min_sharpe=1.0)
    store.close()
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# 项目根目录（src/aipha/backtest/ → 上溯 4 级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# AIPHA_DB_PATH 环境变量优先；未设置则使用 output/backtest_index.db
def _resolve_db_path() -> Path:
    env_val = os.environ.get("AIPHA_DB_PATH", "").strip()
    if env_val:
        return Path(env_val)
    return _PROJECT_ROOT / "output" / "backtest_index.db"

DEFAULT_DB_PATH = _resolve_db_path()

SCHEMA_VERSION = 9


# ============================================================================
# 辅助函数
# ============================================================================

def _to_json(obj: Any) -> str | None:
    """将 Python 对象序列化为 JSON 字符串，None 返回 None。"""
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def _from_json(text: str | None) -> Any:
    """JSON 字符串反序列化，None/空返回 None。"""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """将 sqlite3.Row 转为 dict,  JSON 列自动反序列化。"""
    d = dict(row)
    for json_col in ("metrics_json", "cost_json", "config_json",
                      "params_json", "splits_json", "best_params_json",
                      "sweep_config_json"):
        if json_col in d and d[json_col]:
            d[json_col] = _from_json(d[json_col])
    return d


# ============================================================================
# Schema DDL
# ============================================================================

_BACKTEST_RUNS_DDL = """\
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id            TEXT PRIMARY KEY,
    strategy_name     TEXT NOT NULL,
    category          TEXT DEFAULT 'other',
    start_date        TEXT,
    end_date          TEXT,
    initial_capital   REAL DEFAULT 0,
    final_nav         REAL DEFAULT 0,
    -- 核心指标（扁平化便于 WHERE 过滤）
    annualized_return REAL DEFAULT 0,
    max_drawdown      REAL DEFAULT 0,
    sharpe_ratio      REAL DEFAULT 0,
    sortino_ratio     REAL DEFAULT 0,
    calmar_ratio      REAL DEFAULT 0,
    win_rate          REAL DEFAULT 0,
    profit_factor     REAL DEFAULT 0,
    total_trades      INTEGER DEFAULT 0,
    turnover_rate     REAL DEFAULT 0,
    avg_positions     REAL DEFAULT 0,
    max_positions     INTEGER DEFAULT 0,
    -- 成本
    total_fees        REAL DEFAULT 0,
    cost_drag         REAL DEFAULT 0,
    -- 指纹
    config_hash       TEXT,
    git_commit        TEXT,
    seed              INTEGER,
    -- 分类
    run_type          TEXT DEFAULT 'single',
    parent_id         TEXT,
    output_dir        TEXT,
    -- 状态 (promoted / active / retired / invalid)
    status            TEXT DEFAULT 'active',
    -- 时间戳
    created_at        TEXT DEFAULT (datetime('now', 'localtime')),
    -- 可扩展 JSON
    metrics_json      TEXT,
    cost_json         TEXT,
    config_json       TEXT
);
"""

_WF_RESULTS_DDL = """\
CREATE TABLE IF NOT EXISTS wf_results (
    wf_id             TEXT PRIMARY KEY,
    strategy_name     TEXT NOT NULL,
    verdict           TEXT,
    score             REAL DEFAULT 0,
    config_path       TEXT,
    data_range        TEXT,
    train_days        INTEGER,
    test_days         INTEGER,
    n_splits          INTEGER,
    purged            INTEGER DEFAULT 0,
    params_json       TEXT,
    splits_json       TEXT,
    pbo               REAL,
    dsr               REAL,
    hlz_adjusted_t    REAL,
    created_at        TEXT DEFAULT (datetime('now', 'localtime'))
);
"""

_SWEEP_RUNS_DDL = """\
CREATE TABLE IF NOT EXISTS sweep_runs (
    sweep_id          TEXT PRIMARY KEY,
    strategy_name     TEXT NOT NULL,
    total_tasks       INTEGER DEFAULT 0,
    best_params_json  TEXT,
    best_sharpe       REAL DEFAULT 0,
    sweep_config_json TEXT,
    output_dir        TEXT,
    created_at        TEXT DEFAULT (datetime('now', 'localtime'))
);
"""

_SCHEMA_META_DDL = """\
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_STRATEGY_CONFIGS_DDL = """\
CREATE TABLE IF NOT EXISTS strategy_configs (
    config_id     TEXT PRIMARY KEY,
    config_name   TEXT NOT NULL UNIQUE,
    strategy_name TEXT NOT NULL,
    yaml_content  TEXT NOT NULL,
    description   TEXT DEFAULT '',
    status        TEXT DEFAULT 'development',
    parent_config TEXT DEFAULT NULL,
    notes         TEXT DEFAULT '',
    env           TEXT DEFAULT 'research',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    created_by    TEXT DEFAULT '',
    is_active     INTEGER DEFAULT 1
);
"""

_USERS_DDL = """\
CREATE TABLE IF NOT EXISTS users (
    username                TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    password_hash           TEXT NOT NULL,
    role                    TEXT NOT NULL DEFAULT 'viewer',
    email                   TEXT DEFAULT '',
    is_active               INTEGER DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    created_by              TEXT DEFAULT '',
    last_login              TEXT DEFAULT NULL,
    failed_login_attempts   INTEGER DEFAULT 0
);
"""

_PAPER_PORTFOLIOS_DDL = """\
CREATE TABLE IF NOT EXISTS paper_portfolios (
    portfolio_id      TEXT PRIMARY KEY,
    display_name      TEXT NOT NULL,
    config_path       TEXT DEFAULT '',
    paper_mode        TEXT DEFAULT 'multi_strategy',
    paper_status      TEXT DEFAULT 'registered',
    paper_category    TEXT DEFAULT 'research',
    strategies_json   TEXT NOT NULL,
    weights_json      TEXT NOT NULL,
    source_configs_json TEXT DEFAULT '[]',
    backtest_metrics_json TEXT DEFAULT '{}',
    portfolio_expected_json TEXT DEFAULT '{}',
    initial_capital   REAL DEFAULT 1000000.0,
    state_dir         TEXT DEFAULT '',
    notes             TEXT DEFAULT '',
    is_active         INTEGER DEFAULT 1,
    created_at        TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at        TEXT DEFAULT (datetime('now', 'localtime')),
    created_by        TEXT DEFAULT ''
);
"""

_SYSTEM_SETTINGS_DDL = """\
CREATE TABLE IF NOT EXISTS system_settings (
    setting_key       TEXT PRIMARY KEY,
    value_json        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    created_by        TEXT DEFAULT ''
);
"""

_INDEXES_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_br_strategy  ON backtest_runs(strategy_name);",
    "CREATE INDEX IF NOT EXISTS idx_br_category  ON backtest_runs(category);",
    "CREATE INDEX IF NOT EXISTS idx_br_sharpe    ON backtest_runs(sharpe_ratio);",
    "CREATE INDEX IF NOT EXISTS idx_br_run_type  ON backtest_runs(run_type);",
    "CREATE INDEX IF NOT EXISTS idx_br_parent    ON backtest_runs(parent_id);",
    "CREATE INDEX IF NOT EXISTS idx_br_created   ON backtest_runs(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_br_status    ON backtest_runs(status);",
    "CREATE INDEX IF NOT EXISTS idx_wf_strategy  ON wf_results(strategy_name);",
    "CREATE INDEX IF NOT EXISTS idx_sw_strategy  ON sweep_runs(strategy_name);",
    "CREATE INDEX IF NOT EXISTS idx_sc_strategy  ON strategy_configs(strategy_name);",
    "CREATE INDEX IF NOT EXISTS idx_sc_status    ON strategy_configs(status);",
    "CREATE INDEX IF NOT EXISTS idx_sc_active    ON strategy_configs(is_active);",
    "CREATE INDEX IF NOT EXISTS idx_usr_role     ON users(role);",
    "CREATE INDEX IF NOT EXISTS idx_usr_active   ON users(is_active);",
    "CREATE INDEX IF NOT EXISTS idx_pp_status    ON paper_portfolios(paper_status);",
    "CREATE INDEX IF NOT EXISTS idx_pp_active    ON paper_portfolios(is_active);",
]


# ============================================================================
# ResultStore 类
# ============================================================================


class ResultStore:
    """回测结果 SQLite 索引存储。

    Args:
        db_path: 数据库文件路径，默认 ``output/backtest_index.db``
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    # ── 连接管理 ──────────────────────────────────────────────────────

    @property
    def conn(self) -> sqlite3.Connection:
        """获取或创建 SQLite 连接。"""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self._db_path),
                timeout=10,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        return self._conn

    def close(self) -> None:
        """关闭连接。"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __del__(self) -> None:
        """析构时关闭数据库连接防止泄漏。"""
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self) -> "ResultStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        """事务上下文管理器。"""
        cur = self.conn.cursor()
        try:
            yield cur
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ── Schema 初始化 ─────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        """确保表结构存在并按需迁移版本。"""
        with self.transaction() as cur:
            cur.execute(_SCHEMA_META_DDL)
            cur.execute(_BACKTEST_RUNS_DDL)
            cur.execute(_WF_RESULTS_DDL)
            cur.execute(_SWEEP_RUNS_DDL)
            cur.execute(_STRATEGY_CONFIGS_DDL)
            cur.execute(_USERS_DDL)
            cur.execute(_PAPER_PORTFOLIOS_DDL)
            cur.execute(_SYSTEM_SETTINGS_DDL)
            # 版本管理
            cur.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES (?, ?)",
                ("schema_version", "1"),
            )

        # 运行增量迁移（可能会 ALTER TABLE 加列）
        self._run_migrations()

        # 迁移后再创建索引（避免引用尚不存在的列）
        with self.transaction() as cur:
            for idx_sql in _INDEXES_DDL:
                cur.execute(idx_sql)

        logger.debug("ResultStore schema 已就绪: %s", self._db_path)

    def _run_migrations(self) -> None:
        """按版本号顺序执行增量迁移。幂等：已是最新版本则直接返回。"""
        cur = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        )
        row = cur.fetchone()
        current_ver = int(row[0]) if row else 1

        if current_ver < 2:
            # v2: strategy_configs 表已在 DDL 中用 IF NOT EXISTS 创建，无需额外 SQL
            with self.transaction() as c:
                c.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                    ("schema_version", "2"),
                )
            logger.info("ResultStore 迁移到 v2: strategy_configs 表已就绪")
            current_ver = 2

        if current_ver < 3:
            # v3: users 表已在 DDL 中用 IF NOT EXISTS 创建，无需额外 SQL
            with self.transaction() as c:
                c.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                    ("schema_version", "3"),
                )
            logger.info("ResultStore 迁移到 v3: users 表已就绪")
            current_ver = 3

        if current_ver < 4:
            # v4: paper_portfolios 表
            with self.transaction() as c:
                c.execute(_PAPER_PORTFOLIOS_DDL)
                for idx_sql in _INDEXES_DDL:
                    c.execute(idx_sql)
                c.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                    ("schema_version", "4"),
                )
            logger.info("ResultStore 迁移到 v4: paper_portfolios 表已就绪")
            current_ver = 4

        if current_ver < 5:
            # v5: wf_results 表新增 pbo / dsr / hlz_adjusted_t 列
            with self.transaction() as c:
                for col, col_type in [
                    ("pbo", "REAL"),
                    ("dsr", "REAL"),
                    ("hlz_adjusted_t", "REAL"),
                ]:
                    try:
                        c.execute(
                            f"ALTER TABLE wf_results ADD COLUMN {col} {col_type}"
                        )
                    except Exception:
                        pass  # 列已存在，忽略
                c.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                    ("schema_version", "5"),
                )
            logger.info("ResultStore 迁移到 v5: wf_results 新增 pbo/dsr/hlz_adjusted_t")

        if current_ver < 6:
            # v6: system_settings 表
            with self.transaction() as c:
                c.execute(_SYSTEM_SETTINGS_DDL)
                c.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                    ("schema_version", "6"),
                )
            logger.info("ResultStore 迁移到 v6: system_settings 表已就绪")
            current_ver = 6

        if current_ver < 7:
            # v7: paper_portfolios 新增 paper_category 列
            with self.transaction() as c:
                try:
                    c.execute(
                        "ALTER TABLE paper_portfolios ADD COLUMN paper_category TEXT DEFAULT 'research'"
                    )
                except Exception:
                    pass  # 列已存在
                c.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                    ("schema_version", "7"),
                )
            logger.info("ResultStore 迁移到 v7: paper_portfolios 新增 paper_category")

        if current_ver < 8:
            # v8: backtest_runs 新增 status 列 (active/pinned/archived)
            with self.transaction() as c:
                try:
                    c.execute(
                        "ALTER TABLE backtest_runs ADD COLUMN status TEXT DEFAULT 'active'"
                    )
                except Exception:
                    pass  # 列已存在
                c.execute(
                    "CREATE INDEX IF NOT EXISTS idx_br_status ON backtest_runs(status)"
                )
                c.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                    ("schema_version", "8"),
                )
            logger.info("ResultStore 迁移到 v8: backtest_runs 新增 status 列")

        if current_ver < 9:
            # v9: status 重命名 pinned→promoted, archived→invalid
            with self.transaction() as c:
                c.execute(
                    "UPDATE backtest_runs SET status = 'promoted' WHERE status = 'pinned'"
                )
                c.execute(
                    "UPDATE backtest_runs SET status = 'invalid' WHERE status = 'archived'"
                )
                c.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                    ("schema_version", "9"),
                )
            logger.info("ResultStore 迁移到 v9: status pinned→promoted, archived→invalid")

    @property
    def db_path(self) -> Path:
        """返回数据库文件路径。"""
        return self._db_path

    # ── 回测运行写入 ─────────────────────────────────────────────────

    def upsert_backtest_run(self, run: dict[str, Any]) -> None:
        """插入或更新一条回测运行记录。

        幂等：相同 run_id 会被 REPLACE。

        Args:
            run: 回测运行字典，必须包含 ``run_id`` 和 ``strategy_name``。
                 其余字段可选，缺失时使用默认值。

        Raises:
            ValueError: 缺少 run_id 或 strategy_name
        """
        run_id = run.get("run_id")
        strategy_name = run.get("strategy_name")
        if not run_id or not strategy_name:
            raise ValueError("run 必须包含 run_id 和 strategy_name")

        sql = """\
INSERT INTO backtest_runs (
    run_id, strategy_name, category,
    start_date, end_date, initial_capital, final_nav,
    annualized_return, max_drawdown, sharpe_ratio,
    sortino_ratio, calmar_ratio, win_rate, profit_factor,
    total_trades, turnover_rate, avg_positions, max_positions,
    total_fees, cost_drag,
    config_hash, git_commit, seed,
    run_type, parent_id, output_dir,
    created_at,
    metrics_json, cost_json, config_json
) VALUES (
    :run_id, :strategy_name, :category,
    :start_date, :end_date, :initial_capital, :final_nav,
    :annualized_return, :max_drawdown, :sharpe_ratio,
    :sortino_ratio, :calmar_ratio, :win_rate, :profit_factor,
    :total_trades, :turnover_rate, :avg_positions, :max_positions,
    :total_fees, :cost_drag,
    :config_hash, :git_commit, :seed,
    :run_type, :parent_id, :output_dir,
    :created_at,
    :metrics_json, :cost_json, :config_json
) ON CONFLICT(run_id) DO UPDATE SET
    strategy_name = excluded.strategy_name,
    category = excluded.category,
    start_date = excluded.start_date,
    end_date = excluded.end_date,
    initial_capital = excluded.initial_capital,
    final_nav = excluded.final_nav,
    annualized_return = excluded.annualized_return,
    max_drawdown = excluded.max_drawdown,
    sharpe_ratio = excluded.sharpe_ratio,
    sortino_ratio = excluded.sortino_ratio,
    calmar_ratio = excluded.calmar_ratio,
    win_rate = excluded.win_rate,
    profit_factor = excluded.profit_factor,
    total_trades = excluded.total_trades,
    turnover_rate = excluded.turnover_rate,
    avg_positions = excluded.avg_positions,
    max_positions = excluded.max_positions,
    total_fees = excluded.total_fees,
    cost_drag = excluded.cost_drag,
    config_hash = excluded.config_hash,
    git_commit = excluded.git_commit,
    seed = excluded.seed,
    run_type = excluded.run_type,
    parent_id = excluded.parent_id,
    output_dir = excluded.output_dir,
    created_at = excluded.created_at,
    metrics_json = excluded.metrics_json,
    cost_json = excluded.cost_json,
    config_json = excluded.config_json
"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        params = {
            "run_id": run_id,
            "strategy_name": strategy_name,
            "category": run.get("category", "other"),
            "start_date": run.get("start_date"),
            "end_date": run.get("end_date"),
            "initial_capital": run.get("initial_capital", 0),
            "final_nav": run.get("final_nav", 0),
            "annualized_return": run.get("annualized_return", 0),
            "max_drawdown": run.get("max_drawdown", 0),
            "sharpe_ratio": run.get("sharpe_ratio", 0),
            "sortino_ratio": run.get("sortino_ratio", 0),
            "calmar_ratio": run.get("calmar_ratio", 0),
            "win_rate": run.get("win_rate", 0),
            "profit_factor": run.get("profit_factor", 0),
            "total_trades": run.get("total_trades", 0),
            "turnover_rate": run.get("turnover_rate", 0),
            "avg_positions": run.get("avg_positions", 0),
            "max_positions": run.get("max_positions", 0),
            "total_fees": run.get("total_fees", 0),
            "cost_drag": run.get("cost_drag", 0),
            "config_hash": run.get("config_hash"),
            "git_commit": run.get("git_commit"),
            "seed": run.get("seed"),
            "run_type": run.get("run_type", "single"),
            "parent_id": run.get("parent_id"),
            "output_dir": run.get("output_dir"),
            "created_at": run.get("created_at", now),
            "metrics_json": _to_json(run.get("metrics")),
            "cost_json": _to_json(run.get("cost_attribution")),
            "config_json": _to_json(run.get("config")),
        }
        with self.transaction() as cur:
            cur.execute(sql, params)
        logger.debug("upsert backtest_run: %s", run_id)

    def upsert_backtest_runs(self, runs: list[dict[str, Any]]) -> int:
        """批量插入/更新回测运行记录。

        Args:
            runs: 回测运行字典列表

        Returns:
            成功写入的记录数
        """
        count = 0
        for run in runs:
            try:
                self.upsert_backtest_run(run)
                count += 1
            except Exception as e:
                logger.warning("跳过无效记录 %s: %s", run.get("run_id"), e)
        return count

    def delete_run(self, run_id: str) -> bool:
        """删除一条回测运行记录。

        Returns:
            是否删除成功
        """
        with self.transaction() as cur:
            cur.execute("DELETE FROM backtest_runs WHERE run_id = ?", (run_id,))
            return cur.rowcount > 0

    def update_run_status(self, run_ids: list[str], status: str) -> int:
        """批量更新回测运行状态。

        Args:
            run_ids: run_id 列表
            status: 目标状态 (promoted / active / retired / invalid)

        Returns:
            更新的记录数
        """
        valid = ("promoted", "active", "retired", "invalid")
        if not run_ids or status not in valid:
            return 0
        placeholders = ",".join("?" for _ in run_ids)
        with self.transaction() as cur:
            cur.execute(
                f"UPDATE backtest_runs SET status = ? "
                f"WHERE run_id IN ({placeholders})",
                [status, *run_ids],
            )
            return cur.rowcount

    def bulk_set_status_by_strategy(
        self,
        strategy_names: list[str],
        status: str = "invalid",
    ) -> int:
        """按策略名批量设置状态。

        Args:
            strategy_names: 策略名列表
            status: 目标状态 (retired / invalid)

        Returns:
            更新的记录数
        """
        if not strategy_names:
            return 0
        placeholders = ",".join("?" for _ in strategy_names)
        with self.transaction() as cur:
            cur.execute(
                f"UPDATE backtest_runs SET status = ? "
                f"WHERE strategy_name IN ({placeholders}) "
                f"AND COALESCE(status, 'active') NOT IN ('retired', 'invalid')",
                [status, *strategy_names],
            )
            return cur.rowcount

    # ── 回测运行查询 ─────────────────────────────────────────────────

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """按 run_id 查询单条回测运行。"""
        cur = self.conn.execute(
            "SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def query_runs(
        self,
        *,
        strategy_name: str | None = None,
        category: str | None = None,
        run_type: str | None = None,
        min_sharpe: float | None = None,
        max_drawdown: float | None = None,
        parent_id: str | None = None,
        status: str | None = None,
        include_archived: bool = False,
        limit: int = 500,
        order_by: str = "created_at DESC",
    ) -> list[dict[str, Any]]:
        """灵活查询回测运行记录。

        Args:
            strategy_name: 按策略名精确过滤
            category: 按分类过滤 (cb / equity / other)
            run_type: 按运行类型过滤 (single / sweep_task / wf_fold)
            min_sharpe: Sharpe 下限
            max_drawdown: 最大回撤上限（绝对值）
            parent_id: 按父运行过滤（sweep 子任务）
            status: 按状态精确过滤 (promoted / active / retired / invalid)
            include_archived: 为 True 时包含已下线/无效记录；
                              为 False 且 status 未指定时排除 retired + invalid
            limit: 最大返回数
            order_by: 排序字段

        Returns:
            回测运行字典列表
        """
        clauses: list[str] = []
        params: list[Any] = []

        # 默认排除 retired + invalid（除非显式指定 status 或 include_archived）
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        elif not include_archived:
            clauses.append("COALESCE(status, 'active') NOT IN ('retired', 'invalid')")

        if strategy_name is not None:
            clauses.append("strategy_name = ?")
            params.append(strategy_name)
        if category is not None:
            clauses.append("category = ?")
            params.append(category)
        if run_type is not None:
            clauses.append("run_type = ?")
            params.append(run_type)
        if min_sharpe is not None:
            clauses.append("sharpe_ratio >= ?")
            params.append(min_sharpe)
        if max_drawdown is not None:
            clauses.append("ABS(max_drawdown) <= ?")
            params.append(abs(max_drawdown))
        if parent_id is not None:
            clauses.append("parent_id = ?")
            params.append(parent_id)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        # 白名单验证 order_by 防止注入
        allowed_cols = {
            "created_at", "sharpe_ratio", "annualized_return",
            "max_drawdown", "calmar_ratio", "strategy_name", "run_id",
        }
        order_col = order_by.split()[0] if order_by else "created_at"
        order_dir = "DESC" if "DESC" in order_by.upper() else "ASC"
        if order_col not in allowed_cols:
            order_col = "created_at"
            order_dir = "DESC"

        sql = (
            f"SELECT * FROM backtest_runs{where} "
            f"ORDER BY {order_col} {order_dir} LIMIT ?"
        )
        params.append(limit)

        cur = self.conn.execute(sql, params)
        return [_row_to_dict(row) for row in cur.fetchall()]

    def list_strategies(self, include_archived: bool = False) -> list[dict[str, Any]]:
        """列出所有策略的聚合摘要。

        Returns:
            每个策略一条记录: strategy_name, category, run_count,
            best_sharpe, latest_run
        """
        archived_filter = "" if include_archived else " AND COALESCE(status, 'active') NOT IN ('retired', 'invalid')"
        sql = f"""\
SELECT
    strategy_name,
    category,
    COUNT(*) AS run_count,
    MAX(sharpe_ratio) AS best_sharpe,
    MAX(annualized_return) AS best_return,
    MIN(max_drawdown) AS worst_drawdown,
    MAX(created_at) AS latest_run
FROM backtest_runs
WHERE run_type = 'single'{archived_filter}
GROUP BY strategy_name
ORDER BY best_sharpe DESC
"""
        cur = self.conn.execute(sql)
        return [dict(row) for row in cur.fetchall()]

    def count_runs(self, run_type: str | None = None, include_archived: bool = False) -> int:
        """统计记录数。"""
        clauses = []
        params: list[Any] = []
        if run_type:
            clauses.append("run_type = ?")
            params.append(run_type)
        if not include_archived:
            clauses.append("COALESCE(status, 'active') NOT IN ('retired', 'invalid')")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur = self.conn.execute(
            f"SELECT COUNT(*) FROM backtest_runs{where}", params
        )
        return cur.fetchone()[0]

    # ── 策略配置管理 ─────────────────────────────────────────────────

    def upsert_strategy_config(self, cfg: dict[str, Any]) -> None:
        """插入或更新一条策略配置记录。

        幂等：相同 ``config_id`` 触发 REPLACE。
        ``config_name`` 有 UNIQUE 约束，同名配置直接覆盖内容时需保持 config_id 一致。

        Args:
            cfg: 策略配置字典，必须包含 ``config_id``、``config_name``、
                 ``strategy_name``、``yaml_content``。
        """
        required = ("config_id", "config_name", "strategy_name", "yaml_content")
        for field in required:
            if not cfg.get(field):
                raise ValueError(f"strategy_config 缺少必填字段: {field}")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = """\
INSERT OR REPLACE INTO strategy_configs (
    config_id, config_name, strategy_name, yaml_content,
    description, status, parent_config, notes, env,
    created_at, updated_at, created_by, is_active
) VALUES (
    :config_id, :config_name, :strategy_name, :yaml_content,
    :description, :status, :parent_config, :notes, :env,
    :created_at, :updated_at, :created_by, :is_active
)
"""
        params = {
            "config_id":     cfg["config_id"],
            "config_name":   cfg["config_name"],
            "strategy_name": cfg["strategy_name"],
            "yaml_content":  cfg["yaml_content"],
            "description":   cfg.get("description", ""),
            "status":        cfg.get("status", "development"),
            "parent_config": cfg.get("parent_config"),
            "notes":         cfg.get("notes", ""),
            "env":           cfg.get("env", "research"),
            "created_at":    cfg.get("created_at", now),
            "updated_at":    now,
            "created_by":    cfg.get("created_by", ""),
            "is_active":     1 if cfg.get("is_active", True) else 0,
        }
        with self.transaction() as cur:
            cur.execute(sql, params)
        logger.debug("upsert strategy_config: %s", cfg["config_name"])

    def get_strategy_config(self, config_id: str) -> dict[str, Any] | None:
        """按 config_id 查询单条策略配置。"""
        cur = self.conn.execute(
            "SELECT * FROM strategy_configs WHERE config_id = ?", (config_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def get_strategy_config_by_name(self, config_name: str) -> dict[str, Any] | None:
        """按 config_name 查询单条策略配置。"""
        cur = self.conn.execute(
            "SELECT * FROM strategy_configs WHERE config_name = ?", (config_name,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def list_strategy_configs(
        self,
        *,
        strategy_name: str | None = None,
        status: str | None = None,
        env: str | None = None,
        active_only: bool = True,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """查询策略配置列表。

        Args:
            strategy_name: 按策略注册名过滤
            status:        按状态过滤（development/validated/paper_testing/production/archived）
            env:           按环境过滤（research/paper/production）
            active_only:   True 则只返回 is_active=1 的记录
            limit:         最大返回数

        Returns:
            策略配置字典列表，按 updated_at DESC 排序
        """
        clauses: list[str] = []
        params: list[Any] = []

        if active_only:
            clauses.append("is_active = 1")
        if strategy_name:
            clauses.append("strategy_name = ?")
            params.append(strategy_name)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if env:
            clauses.append("env = ?")
            params.append(env)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            f"SELECT * FROM strategy_configs{where} "
            f"ORDER BY updated_at DESC LIMIT ?"
        )
        params.append(limit)
        cur = self.conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def soft_delete_strategy_config(self, config_id: str) -> bool:
        """软删除：将 is_active 设为 0。

        Returns:
            是否找到记录并执行了更新
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.transaction() as cur:
            cur.execute(
                "UPDATE strategy_configs SET is_active=0, updated_at=? WHERE config_id=?",
                (now, config_id),
            )
            return cur.rowcount > 0

    def update_strategy_config_status(
        self, config_id: str, new_status: str, updated_by: str = ""
    ) -> bool:
        """更新策略配置状态（不修改 yaml_content）。

        有效状态: development / validated / paper_testing / production / archived

        Returns:
            是否找到记录并执行了更新
        """
        valid_statuses = {
            "development", "validated", "paper_testing", "production", "archived"
        }
        if new_status not in valid_statuses:
            raise ValueError(
                f"无效状态 '{new_status}'，有效值: {valid_statuses}"
            )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.transaction() as cur:
            cur.execute(
                "UPDATE strategy_configs SET status=?, updated_at=?, created_by=? "
                "WHERE config_id=?",
                (new_status, now, updated_by, config_id),
            )
            return cur.rowcount > 0

    def count_strategy_configs(self, active_only: bool = True) -> int:
        """统计策略配置数量。"""
        sql = (
            "SELECT COUNT(*) FROM strategy_configs WHERE is_active=1"
            if active_only
            else "SELECT COUNT(*) FROM strategy_configs"
        )
        return self.conn.execute(sql).fetchone()[0]

    # ── 用户管理 ─────────────────────────────────────────────────────

    def upsert_user(self, user: dict[str, Any]) -> None:
        """插入或更新一条用户记录。

        幂等：相同 ``username`` 触发 REPLACE。

        Args:
            user: 用户字典，必须包含 ``username``、``name``、``password_hash``、``role``。
        """
        for field in ("username", "name", "password_hash", "role"):
            if not user.get(field):
                raise ValueError(f"user 缺少必填字段: {field}")

        valid_roles = set(("viewer", "researcher", "ops", "risk_approver", "admin"))
        if user["role"] not in valid_roles:
            raise ValueError(f"无效角色 '{user['role']}'，有效值: {valid_roles}")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        existing = self.get_user(user["username"])
        sql = """\
INSERT OR REPLACE INTO users (
    username, name, password_hash, role, email,
    is_active, created_at, updated_at, created_by,
    last_login, failed_login_attempts
) VALUES (
    :username, :name, :password_hash, :role, :email,
    :is_active, :created_at, :updated_at, :created_by,
    :last_login, :failed_login_attempts
)
"""
        params = {
            "username":               user["username"],
            "name":                   user["name"],
            "password_hash":          user["password_hash"],
            "role":                   user["role"],
            "email":                  user.get("email", ""),
            "is_active":              1 if user.get("is_active", True) else 0,
            "created_at":             (existing["created_at"] if existing
                                       else user.get("created_at", now)),
            "updated_at":             now,
            "created_by":             user.get("created_by", ""),
            "last_login":             (existing["last_login"] if existing
                                       else user.get("last_login")),
            "failed_login_attempts":  (existing["failed_login_attempts"] if existing
                                       else user.get("failed_login_attempts", 0)),
        }
        with self.transaction() as cur:
            cur.execute(sql, params)
        logger.debug("upsert user: %s", user["username"])

    def get_user(self, username: str) -> dict[str, Any] | None:
        """按 username 查询单条用户记录。"""
        cur = self.conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def list_users(self, active_only: bool = True) -> list[dict[str, Any]]:
        """查询用户列表，按 username 排序。

        Args:
            active_only: True 则只返回 is_active=1 的用户。
        """
        if active_only:
            cur = self.conn.execute(
                "SELECT * FROM users WHERE is_active=1 ORDER BY username"
            )
        else:
            cur = self.conn.execute("SELECT * FROM users ORDER BY username")
        return [dict(row) for row in cur.fetchall()]

    def update_user_role(self, username: str, new_role: str,
                         updated_by: str = "") -> bool:
        """更新用户角色，不修改密码。

        Returns:
            是否找到并更新了记录。
        """
        valid_roles = {"viewer", "researcher", "ops", "risk_approver", "admin"}
        if new_role not in valid_roles:
            raise ValueError(f"无效角色 '{new_role}'，有效值: {valid_roles}")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.transaction() as cur:
            cur.execute(
                "UPDATE users SET role=?, updated_at=?, created_by=? WHERE username=?",
                (new_role, now, updated_by, username),
            )
            return cur.rowcount > 0

    def update_user_password(self, username: str, new_hash: str,
                              updated_by: str = "") -> bool:
        """更新用户密码哈希。

        Returns:
            是否找到并更新了记录。
        """
        if not new_hash:
            raise ValueError("new_hash 不得为空")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.transaction() as cur:
            cur.execute(
                "UPDATE users SET password_hash=?, updated_at=?, created_by=? "
                "WHERE username=?",
                (new_hash, now, updated_by, username),
            )
            return cur.rowcount > 0

    def record_user_login(self, username: str, success: bool) -> None:
        """记录登录事件：更新 last_login 或累计失败次数。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.transaction() as cur:
            if success:
                cur.execute(
                    "UPDATE users SET last_login=?, failed_login_attempts=0 "
                    "WHERE username=?",
                    (now, username),
                )
            else:
                cur.execute(
                    "UPDATE users SET failed_login_attempts=failed_login_attempts+1 "
                    "WHERE username=?",
                    (username,),
                )

    def soft_delete_user(self, username: str) -> bool:
        """软删除：将 is_active 设为 0，保留记录本身。

        原先这里执行的是 ``DELETE FROM users``（真删），与方法名、与本模块
        其余 ``soft_delete_*`` 的约定（见 :meth:`soft_delete_strategy_config`）
        以及调用方 ``DELETE /system/users``（docstring 写的是 "Soft delete a
        user"）全都相反。真删的两个后果都不能接受：

        1. **审计断链** —— ``last_login`` / ``failed_login_attempts`` 随行消失，
           历史审计记录里引用的这个账号再也查不到主体。
        2. **用户名可被重用** —— 删掉后同名账号可再建，于是历史审计条目
           被静默地重新指向一个新主体。

        同时 ``list_users(active_only=False)`` / ``count_users(active_only=False)``
        这两个分支在真删语义下永远取不到 is_active=0 的行，属于死代码；
        ``auth/router.py`` 里的 ``if not row["is_active"]: 拒绝登录`` 同样永远
        命中不到被删用户。改回软删后这三处才各自成立。

        禁用后该账号无法登录：``build_stauth_credentials()`` 只取
        ``active_only=True``，且登录接口另有 is_active 校验（双层）。
        要恢复账号走 ``PUT /system/users/{username}`` 把 is_active 置回 True。

        不可禁用最后一个活跃 admin，否则系统将无人可管。

        Returns:
            是否命中了记录（用户不存在时 False，调用方据此返回 404）。

        Raises:
            ValueError: 尝试禁用最后一个活跃 admin 时。
        """
        user = self.get_user(username)
        if not user:
            return False
        if user["role"] == "admin":
            admin_count = self.conn.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1"
            ).fetchone()[0]
            if admin_count <= 1:
                raise ValueError("无法删除最后一个活跃管理员账号")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.transaction() as cur:
            cur.execute(
                "UPDATE users SET is_active=0, updated_at=? WHERE username=?",
                (now, username),
            )
            return cur.rowcount > 0

    def count_users(self, active_only: bool = True) -> int:
        """统计用户数量。"""
        sql = (
            "SELECT COUNT(*) FROM users WHERE is_active=1"
            if active_only
            else "SELECT COUNT(*) FROM users"
        )
        return self.conn.execute(sql).fetchone()[0]

    def build_stauth_credentials(self) -> dict[str, Any]:
        """从 users 表构建认证所需的 credentials 字典。

        Returns:
            {"usernames": {username: {"name": ..., "password": ..., "role": ..., "email": ...}}}
        """
        users = self.list_users(active_only=True)
        return {
            "usernames": {
                u["username"]: {
                    "name":     u["name"],
                    "password": u["password_hash"],
                    "role":     u["role"],
                    "email":    u.get("email", ""),
                    "failed_login_attempts": u.get("failed_login_attempts", 0),
                    "logged_in": False,
                }
                for u in users
            }
        }

    # ── 模拟组合管理 ─────────────────────────────────────────────────

    def upsert_paper_portfolio(self, portfolio: dict[str, Any]) -> None:
        """插入或更新一条模拟组合配置。

        幂等：相同 portfolio_id 会被 REPLACE。

        Args:
            portfolio: 组合字典，必须包含 ``portfolio_id``、``display_name``、
                       ``strategies``(list)、``weights``(dict)。
        """
        pid = portfolio.get("portfolio_id")
        if not pid:
            raise ValueError("portfolio 必须包含 portfolio_id")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        strategies = portfolio.get("strategies", [])
        weights = portfolio.get("weights", {})

        sql = """\
INSERT OR REPLACE INTO paper_portfolios (
    portfolio_id, display_name, config_path, paper_mode, paper_status,
    paper_category,
    strategies_json, weights_json, source_configs_json,
    backtest_metrics_json, portfolio_expected_json,
    initial_capital, state_dir, notes, is_active,
    created_at, updated_at, created_by
) VALUES (
    :portfolio_id, :display_name, :config_path, :paper_mode, :paper_status,
    :paper_category,
    :strategies_json, :weights_json, :source_configs_json,
    :backtest_metrics_json, :portfolio_expected_json,
    :initial_capital, :state_dir, :notes, :is_active,
    :created_at, :updated_at, :created_by
)"""
        params = {
            "portfolio_id": pid,
            "display_name": portfolio.get("display_name", pid),
            "config_path": portfolio.get("config_path", ""),
            "paper_mode": portfolio.get("paper_mode", "multi_strategy"),
            "paper_status": portfolio.get("paper_status", "registered"),
            "paper_category": portfolio.get("paper_category", "research"),
            "strategies_json": _to_json(strategies),
            "weights_json": _to_json(weights),
            "source_configs_json": _to_json(portfolio.get("source_configs", [])),
            "backtest_metrics_json": _to_json(portfolio.get("backtest_metrics", {})),
            "portfolio_expected_json": _to_json(portfolio.get("portfolio_expected", {})),
            "initial_capital": portfolio.get("initial_capital", 1_000_000.0),
            "state_dir": portfolio.get("state_dir", f"data/paper_state/{pid}"),
            "notes": portfolio.get("notes", ""),
            "is_active": 1 if portfolio.get("is_active", True) else 0,
            "created_at": portfolio.get("created_at", now),
            "updated_at": now,
            "created_by": portfolio.get("created_by", ""),
        }
        with self.transaction() as cur:
            cur.execute(sql, params)

    def get_paper_portfolio(self, portfolio_id: str) -> dict[str, Any] | None:
        """按 ID 获取模拟组合配置。"""
        row = self.conn.execute(
            "SELECT * FROM paper_portfolios WHERE portfolio_id=?", (portfolio_id,)
        ).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        for col in ("strategies_json", "weights_json", "source_configs_json",
                     "backtest_metrics_json", "portfolio_expected_json"):
            if col in d:
                d[col.replace("_json", "")] = _from_json(d.pop(col))
        return d

    def list_paper_portfolios(
        self, *, active_only: bool = True, status: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出模拟组合。

        Args:
            active_only: 仅返回 is_active=1 的组合
            status: 可选状态过滤 (registered/running/stopped/archived)
        """
        clauses: list[str] = []
        params: list[Any] = []
        if active_only:
            clauses.append("is_active=1")
        if status:
            clauses.append("paper_status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM paper_portfolios{where} ORDER BY created_at", params
        ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            d = _row_to_dict(r)
            for col in ("strategies_json", "weights_json", "source_configs_json",
                         "backtest_metrics_json", "portfolio_expected_json"):
                if col in d:
                    d[col.replace("_json", "")] = _from_json(d.pop(col))
            result.append(d)
        return result

    def update_paper_portfolio_status(
        self, portfolio_id: str, new_status: str,
    ) -> bool:
        """更新组合状态 (registered/running/paused/stopped/archived)。"""
        valid = {"registered", "running", "paused", "stopped", "archived"}
        if new_status not in valid:
            raise ValueError(f"无效状态: {new_status}，有效值: {valid}")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.transaction() as cur:
            # archived status must also deactivate (is_active=0)
            if new_status == "archived":
                cur.execute(
                    "UPDATE paper_portfolios SET paper_status=?, is_active=0, updated_at=? "
                    "WHERE portfolio_id=?",
                    (new_status, now, portfolio_id),
                )
            else:
                cur.execute(
                    "UPDATE paper_portfolios SET paper_status=?, updated_at=? "
                    "WHERE portfolio_id=?",
                    (new_status, now, portfolio_id),
                )
            return cur.rowcount > 0

    def soft_delete_paper_portfolio(self, portfolio_id: str) -> bool:
        """软删除模拟组合（is_active=0）。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.transaction() as cur:
            cur.execute(
                "UPDATE paper_portfolios SET is_active=0, updated_at=? "
                "WHERE portfolio_id=?",
                (now, portfolio_id),
            )
            return cur.rowcount > 0

    def count_paper_portfolios(self, active_only: bool = True) -> int:
        """统计模拟组合数量。"""
        sql = (
            "SELECT COUNT(*) FROM paper_portfolios WHERE is_active=1"
            if active_only
            else "SELECT COUNT(*) FROM paper_portfolios"
        )
        return self.conn.execute(sql).fetchone()[0]

    # ── 系统设置 ─────────────────────────────────────────────────────

    def get_system_setting(self, setting_key: str,
                           default: Any = None) -> Any:
        """按 key 获取系统设置 JSON 值。"""
        row = self.conn.execute(
            "SELECT value_json FROM system_settings WHERE setting_key=?",
            (setting_key,),
        ).fetchone()
        if not row:
            return default
        return _from_json(row[0])

    def set_system_setting(self, setting_key: str, value: Any,
                           updated_by: str = "") -> None:
        """写入系统设置 JSON 值。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.transaction() as cur:
            cur.execute(
                """
INSERT INTO system_settings (setting_key, value_json, updated_at, created_by)
VALUES (?, ?, ?, ?)
ON CONFLICT(setting_key) DO UPDATE SET
    value_json=excluded.value_json,
    updated_at=excluded.updated_at,
    created_by=excluded.created_by
""",
                (setting_key, _to_json(value), now, updated_by),
            )

    # ── Walk-Forward 结果 ────────────────────────────────────────────

    def upsert_wf_result(self, wf: dict[str, Any]) -> None:
        """插入或更新一条 Walk-Forward 验证结果。

        Args:
            wf: WF 结果字典，必须包含 ``wf_id`` 和 ``strategy_name``
        """
        wf_id = wf.get("wf_id")
        strategy_name = wf.get("strategy_name")
        if not wf_id or not strategy_name:
            raise ValueError("wf 必须包含 wf_id 和 strategy_name")

        sql = """\
INSERT OR REPLACE INTO wf_results (
    wf_id, strategy_name, verdict, score,
    config_path, data_range, train_days, test_days,
    n_splits, purged, params_json, splits_json,
    pbo, dsr, hlz_adjusted_t, created_at
) VALUES (
    :wf_id, :strategy_name, :verdict, :score,
    :config_path, :data_range, :train_days, :test_days,
    :n_splits, :purged, :params_json, :splits_json,
    :pbo, :dsr, :hlz_adjusted_t, :created_at
)
"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        params = {
            "wf_id": wf_id,
            "strategy_name": strategy_name,
            "verdict": wf.get("verdict"),
            "score": wf.get("score", 0),
            "config_path": wf.get("config_path"),
            "data_range": wf.get("data_range"),
            "train_days": wf.get("train_days"),
            "test_days": wf.get("test_days"),
            "n_splits": wf.get("n_splits"),
            "purged": 1 if wf.get("purged") else 0,
            "params_json": _to_json(wf.get("params")),
            "splits_json": _to_json(wf.get("splits")),
            "pbo": wf.get("pbo"),
            "dsr": wf.get("dsr"),
            "hlz_adjusted_t": wf.get("hlz_adjusted_t"),
            "created_at": wf.get("created_at", now),
        }
        with self.transaction() as cur:
            cur.execute(sql, params)
        logger.debug("upsert wf_result: %s", wf_id)

    def query_wf_results(
        self, strategy_name: str | None = None
    ) -> list[dict[str, Any]]:
        """查询 Walk-Forward 验证结果。"""
        if strategy_name:
            cur = self.conn.execute(
                "SELECT * FROM wf_results WHERE strategy_name = ? "
                "ORDER BY created_at DESC",
                (strategy_name,),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM wf_results ORDER BY created_at DESC"
            )
        return [_row_to_dict(row) for row in cur.fetchall()]

    # ── Sweep 汇总 ───────────────────────────────────────────────────

    def upsert_sweep_run(self, sweep: dict[str, Any]) -> None:
        """插入或更新一条 Sweep 汇总记录。

        Args:
            sweep: Sweep 结果字典，必须包含 ``sweep_id`` 和 ``strategy_name``
        """
        sweep_id = sweep.get("sweep_id")
        strategy_name = sweep.get("strategy_name")
        if not sweep_id or not strategy_name:
            raise ValueError("sweep 必须包含 sweep_id 和 strategy_name")

        sql = """\
INSERT OR REPLACE INTO sweep_runs (
    sweep_id, strategy_name, total_tasks,
    best_params_json, best_sharpe, sweep_config_json,
    output_dir, created_at
) VALUES (
    :sweep_id, :strategy_name, :total_tasks,
    :best_params_json, :best_sharpe, :sweep_config_json,
    :output_dir, :created_at
)
"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        params = {
            "sweep_id": sweep_id,
            "strategy_name": strategy_name,
            "total_tasks": sweep.get("total_tasks", 0),
            "best_params_json": _to_json(sweep.get("best_params")),
            "best_sharpe": sweep.get("best_sharpe", 0),
            "sweep_config_json": _to_json(sweep.get("sweep_config")),
            "output_dir": sweep.get("output_dir"),
            "created_at": sweep.get("created_at", now),
        }
        with self.transaction() as cur:
            cur.execute(sql, params)
        logger.debug("upsert sweep_run: %s", sweep_id)

    def query_sweep_runs(
        self, strategy_name: str | None = None
    ) -> list[dict[str, Any]]:
        """查询 Sweep 汇总记录。"""
        if strategy_name:
            cur = self.conn.execute(
                "SELECT * FROM sweep_runs WHERE strategy_name = ? "
                "ORDER BY created_at DESC",
                (strategy_name,),
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM sweep_runs ORDER BY created_at DESC"
            )
        return [_row_to_dict(row) for row in cur.fetchall()]


# ============================================================================
# 转换辅助函数：summary.json → DB record
# ============================================================================


def summary_json_to_run_dict(
    summary_path: Path,
    *,
    run_type: str = "single",
    parent_id: str | None = None,
) -> dict[str, Any]:
    """将 output/<name>/summary.json 转换为 upsert_backtest_run 所需的字典。

    Args:
        summary_path: summary.json 文件路径
        run_type: 运行类型
        parent_id: 父运行 ID（sweep 子任务用）

    Returns:
        适配 upsert_backtest_run 的字典
    """
    try:
        text = summary_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = summary_path.read_text(encoding="gbk")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"summary.json 顶层不是 dict 而是 {type(data).__name__}，跳过")
    output_dir = summary_path.parent
    run_id = output_dir.name

    m = data.get("metrics", {})
    c = data.get("cost_attribution", {})

    # ── 兼容旧格式：指标扁平存放在顶层（无 metrics 子字典） ──
    # 当 metrics 子字典为空但顶层存在 annualized_return/sharpe_ratio 时，
    # 从顶层提取指标作为 fallback。
    _flat = not m and any(
        k in data for k in ("annualized_return", "sharpe_ratio", "max_drawdown")
    )
    if _flat:
        m = data  # 整个 data 当作 metrics fallback

    # ── 从 final_nav/initial_capital 估算年化收益（兜底） ──
    _ar_from_nav = 0.0
    init_cap = data.get("initial_capital", 0)
    final_nav = data.get("final_nav", 0)
    _start = data.get("start_date", "")
    _end = data.get("end_date", "")
    if init_cap > 0 and final_nav > 0 and _start and _end:
        try:
            from datetime import date as _date

            _s = _date.fromisoformat(str(_start)[:10])
            _e = _date.fromisoformat(str(_end)[:10])
            _years = (_e - _s).days / 365.25
            if _years > 0:
                _ar_from_nav = (final_nav / init_cap) ** (1.0 / _years) - 1.0
        except (ValueError, TypeError):
            pass

    # 尝试读取 fingerprint
    fp_path = output_dir / "fingerprint.json"
    fp: dict[str, Any] = {}
    if fp_path.is_file():
        try:
            fp = json.loads(fp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # 策略名：优先 strategy_name 字段，其次 index 字段，最后从目录名推断
    strategy_name = data.get("strategy_name") or data.get("index") or run_id
    category = _classify_strategy(strategy_name)

    return {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "category": category,
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "initial_capital": data.get("initial_capital", 0),
        "final_nav": data.get("final_nav", 0),
        "annualized_return": m.get("annualized_return", m.get("annual_return", 0)) or _ar_from_nav,
        "max_drawdown": m.get("max_drawdown_pct", m.get("max_drawdown", 0)),
        "sharpe_ratio": m.get("sharpe_ratio", 0),
        "sortino_ratio": m.get("sortino_ratio", 0),
        "calmar_ratio": m.get("calmar_ratio", 0),
        "win_rate": m.get("win_rate", 0),
        "profit_factor": m.get("profit_factor", 0),
        "total_trades": int(float(m.get("total_trades", data.get("total_trades", 0)))),
        "turnover_rate": m.get("turnover_rate", 0),
        "avg_positions": m.get("avg_positions", 0),
        "max_positions": int(m.get("max_positions", 0)),
        "total_fees": c.get("total_fees", data.get("total_fees", 0)),
        "cost_drag": c.get("cost_drag_annualized", 0),
        "config_hash": fp.get("config_hash"),
        "git_commit": fp.get("git_commit"),
        "seed": fp.get("seed"),
        "run_type": run_type,
        "parent_id": parent_id,
        "output_dir": str(output_dir),
        "metrics": m if not _flat else {},
        "cost_attribution": c,
    }


def wf_json_to_wf_dict(wf_path: Path) -> dict[str, Any]:
    """将 WF JSON 文件转换为 upsert_wf_result 所需的字典。"""
    data = json.loads(wf_path.read_text(encoding="utf-8"))
    wf_id = wf_path.stem  # 文件名（不含扩展名）
    params = data.get("params", {})
    stats = data.get("statistical_validation", {}) or {}
    return {
        "wf_id": wf_id,
        "strategy_name": data.get("strategy_name", "unknown"),
        "verdict": data.get("verdict"),
        "score": data.get("score", 0),
        "config_path": data.get("config_path"),
        "data_range": data.get("data_range"),
        "train_days": params.get("train_days"),
        "test_days": params.get("test_days"),
        "n_splits": params.get("n_splits"),
        "purged": params.get("purged", False),
        "params": params,
        "splits": data.get("splits", data.get("splits_detail")),
        "pbo": stats.get("pbo"),
        "dsr": stats.get("dsr"),
        "hlz_adjusted_t": stats.get("hlz_adjusted_t"),
    }


def _classify_strategy(strategy_name: str) -> str:
    """根据策略名推断分类。"""
    cb_kw = [
        "cb_", "double_low", "small_size", "new_bond", "new_small",
        "low_price", "low_premium", "triple_low", "pseudo_ytm",
        "defensive_value", "vol_breakout", "turnover_surge",
        "negative_premium", "forced_redeem", "ytm_plus", "multi_alpha",
        "multi_factor", "mean_reversion", "conversion_arbitrage",
    ]
    eq_kw = [
        "balanced", "dividend", "pead", "earnings", "multifactor_enhance",
        "smallcap", "sector_momentum", "value_quality", "comprehensive_alpha",
        "ml_predict", "csi500", "csi1000", "csi2000", "a_stock",
        "risk_controlled",
    ]
    for kw in cb_kw:
        if kw in strategy_name:
            return "convertible_bond"
    for kw in eq_kw:
        if kw in strategy_name:
            return "equity"
    if "demo" in strategy_name or "factor_based" in strategy_name:
        return "demo"
    return "other"


# ============================================================================
# 迁移工具
# ============================================================================


def import_existing_results(
    output_dir: str | Path | None = None,
    store: ResultStore | None = None,
) -> dict[str, int]:
    """扫描 output/ 目录，将所有 summary.json 导入 DB。

    支持一级目录和 sweep 子目录。用于一次性迁移历史数据。

    Args:
        output_dir: 输出目录路径，默认 ``output/``
        store: ResultStore 实例，默认创建新实例

    Returns:
        {"imported": N, "skipped": M, "wf_imported": W}
    """
    base = Path(output_dir) if output_dir else _PROJECT_ROOT / "output"
    own_store = store is None
    if own_store:
        store = ResultStore()

    stats = {"imported": 0, "skipped": 0, "wf_imported": 0, "sweep_imported": 0}

    if not base.is_dir():
        return stats

    # 1. 导入一级 summary.json（单回测）
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        summary = entry / "summary.json"
        if not summary.is_file():
            # 检查是否为 sweep 目录（含 sweep_results.json）
            sweep_json = entry / "sweep_results.json"
            if sweep_json.is_file():
                _import_sweep_dir(entry, store, stats)
            else:
                # 递归扫描嵌套子目录（如 _cap_sweep/CB_20w/）
                _import_nested_dir(entry, store, stats)
            continue
        try:
            run_dict = summary_json_to_run_dict(summary)
            store.upsert_backtest_run(run_dict)
            stats["imported"] += 1
        except Exception as e:
            # 聚合分析文件（list型 summary.json）降级为 debug，真正错误仍用 warning
            if "不是 dict" in str(e):
                logger.debug("跳过聚合 %s: %s", entry.name, e)
            else:
                logger.warning("跳过 %s: %s", entry.name, e)
            stats["skipped"] += 1
            # summary.json 不是标准 dict 时，仍扫描子目录中的有效结果
            _import_nested_dir(entry, store, stats)

    # 2. 导入 WF 结果
    for wf_dir_name in ("wf_cb", "wf_equity"):
        wf_dir = base / wf_dir_name
        if not wf_dir.is_dir():
            continue
        for wf_file in wf_dir.glob("*.json"):
            try:
                wf_dict = wf_json_to_wf_dict(wf_file)
                store.upsert_wf_result(wf_dict)
                stats["wf_imported"] += 1
            except Exception as e:
                logger.warning("跳过 WF %s: %s", wf_file.name, e)

    if own_store:
        store.close()

    logger.info(
        "迁移完成: imported=%d, skipped=%d, wf=%d, sweep=%d",
        stats["imported"], stats["skipped"],
        stats["wf_imported"], stats["sweep_imported"],
    )
    return stats


def _import_nested_dir(
    parent_dir: Path, store: ResultStore, stats: dict[str, int]
) -> None:
    """递归扫描嵌套子目录，导入含 summary.json 的子目录。

    处理 ``output/_cap_sweep/CB_20w/summary.json`` 等非标准布局。
    """
    for child in sorted(parent_dir.iterdir()):
        if not child.is_dir():
            continue
        summary = child / "summary.json"
        if summary.is_file():
            try:
                run_dict = summary_json_to_run_dict(summary)
                store.upsert_backtest_run(run_dict)
                stats["imported"] += 1
            except Exception as e:
                logger.warning("跳过嵌套 %s: %s", child.name, e)
                stats["skipped"] += 1


def _import_sweep_dir(
    sweep_dir: Path, store: ResultStore, stats: dict[str, int]
) -> None:
    """导入一个 sweep 目录的汇总和子任务。"""
    sweep_json = sweep_dir / "sweep_results.json"
    sweep_id = sweep_dir.name

    try:
        try:
            text = sweep_json.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = sweep_json.read_text(encoding="gbk")
        data = json.loads(text)
        # 兼容 list 格式：从 [{params, metrics}, ...] 提取 best
        if isinstance(data, list):
            if not data:
                raise ValueError("sweep_results.json 是空 list")
            best_item = max(
                data,
                key=lambda x: (
                    x.get("metrics", {}).get("sharpe_ratio", 0)
                    if isinstance(x, dict) else 0
                ),
            )
            best_m = best_item.get("metrics", {}) if isinstance(best_item, dict) else {}
            store.upsert_sweep_run({
                "sweep_id": sweep_id,
                "strategy_name": best_m.get("strategy_name", sweep_id),
                "total_tasks": len(data),
                "best_params": best_item.get("params"),
                "best_sharpe": best_m.get("sharpe_ratio", 0),
                "sweep_config": None,
                "output_dir": str(sweep_dir),
            })
        else:
            best = data.get("best", {})
            store.upsert_sweep_run({
                "sweep_id": sweep_id,
                "strategy_name": best.get("strategy_name", data.get("strategy", "unknown")),
                "total_tasks": data.get("total_tasks", 0),
                "best_params": best.get("params"),
                "best_sharpe": best.get("sharpe_ratio", best.get("metrics", {}).get("sharpe_ratio", 0)),
                "sweep_config": data.get("config"),
                "output_dir": str(sweep_dir),
            })
        stats["sweep_imported"] += 1
    except Exception as e:
        logger.warning("跳过 sweep %s: %s", sweep_id, e)

    # 导入 sweep 子任务
    for task_dir in sorted(sweep_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        task_summary = task_dir / "summary.json"
        if not task_summary.is_file():
            continue
        try:
            run_dict = summary_json_to_run_dict(
                task_summary, run_type="sweep_task", parent_id=sweep_id
            )
            store.upsert_backtest_run(run_dict)
            stats["imported"] += 1
        except Exception as e:
            logger.warning("跳过 sweep 子任务 %s: %s", task_dir.name, e)
            stats["skipped"] += 1


def import_paper_registry_from_json(
    json_path: str | Path,
    store: ResultStore | None = None,
) -> int:
    """将 paper_registry.json 中的组合导入 DB 的 paper_portfolios 表。

    幂等：相同 portfolio_id 自动覆盖。

    Args:
        json_path: paper_registry.json 的路径
        store: ResultStore 实例（缺省自动创建）

    Returns:
        成功导入的组合数
    """
    json_path = Path(json_path)
    if not json_path.exists():
        logger.warning("paper_registry.json 不存在: %s", json_path)
        return 0

    data = json.loads(json_path.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    if not entries:
        return 0

    if store is None:
        store = ResultStore()

    count = 0
    for entry in entries:
        try:
            pid = entry["entry_id"]
            entry_status = entry.get("paper_status", "registered")
            # Only skip if BOTH registry AND DB say archived.
            # If registry says ready but DB says archived, registry wins
            # (registry is the source of truth for configuration).
            existing = store.get_paper_portfolio(pid)
            if (
                existing
                and existing.get("paper_status") == "archived"
                and entry_status == "archived"
            ):
                logger.debug("跳过已归档组合: %s", pid)
                continue
            portfolio = {
                "portfolio_id": pid,
                "display_name": entry.get("display_name", pid),
                "config_path": entry.get("config_path", ""),
                "paper_mode": entry.get("paper_mode", "multi_strategy"),
                "paper_status": entry.get("paper_status", "registered"),
                "paper_category": entry.get("paper_category", "research"),
                "strategies": entry.get("strategies", []),
                "weights": entry.get("weights", {}),
                "source_configs": entry.get("source_configs", []),
                "backtest_metrics": entry.get("backtest_metrics", {}),
                "portfolio_expected": entry.get("portfolio_expected", {}),
                "initial_capital": entry.get("initial_capital", 1_000_000.0),
                "state_dir": entry.get("state_dir", ""),
                "notes": entry.get("notes", ""),
                "created_at": entry.get("registered_at", ""),
                # archived entries must be deactivated
                "is_active": entry_status != "archived",
            }
            store.upsert_paper_portfolio(portfolio)
            count += 1
        except Exception as e:
            logger.warning("导入组合 %s 失败: %s", entry.get("entry_id"), e)
    logger.info("从 %s 成功导入 %d 条模拟组合", json_path, count)
    return count
