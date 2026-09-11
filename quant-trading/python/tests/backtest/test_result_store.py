"""ResultStore 单元测试 — 基础 CRUD + schema。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from aipha.backtest.result_store import (
    ResultStore,
    _classify_strategy,
    _from_json,
    _to_json,
    summary_json_to_run_dict,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """临时数据库路径。"""
    return tmp_path / "test_index.db"


@pytest.fixture
def store(tmp_db: Path) -> ResultStore:
    """创建临时 ResultStore 实例。"""
    s = ResultStore(db_path=tmp_db)
    yield s
    s.close()


def _make_run(run_id: str = "test_run_1", **overrides) -> dict:
    """构造一条回测运行记录。"""
    base = {
        "run_id": run_id,
        "strategy_name": "small_size",
        "category": "convertible_bond",
        "start_date": "2018-01-02",
        "end_date": "2024-12-31",
        "initial_capital": 1_000_000,
        "final_nav": 2_500_000,
        "annualized_return": 0.14,
        "max_drawdown": -0.12,
        "sharpe_ratio": 1.35,
        "sortino_ratio": 1.8,
        "calmar_ratio": 1.1,
        "win_rate": 0.62,
        "profit_factor": 2.1,
        "total_trades": 500,
        "turnover_rate": 0.15,
        "avg_positions": 8.5,
        "max_positions": 10,
        "total_fees": 25000,
        "cost_drag": 0.003,
        "run_type": "single",
        "output_dir": "/tmp/test_run_1",
    }
    base.update(overrides)
    return base


# ============================================================================
# 辅助函数测试
# ============================================================================


class TestHelperFunctions:
    """辅助函数 _to_json / _from_json / _classify_strategy 测试。"""

    def test_to_json_dict(self):
        assert _to_json({"a": 1}) == '{"a": 1}'

    def test_to_json_none(self):
        assert _to_json(None) is None

    def test_to_json_with_date(self):
        from datetime import date
        result = _to_json({"d": date(2024, 1, 1)})
        assert "2024-01-01" in result

    def test_from_json_valid(self):
        assert _from_json('{"a": 1}') == {"a": 1}

    def test_from_json_none(self):
        assert _from_json(None) is None

    def test_from_json_empty(self):
        assert _from_json("") is None

    def test_from_json_invalid(self):
        assert _from_json("not json") is None

    def test_classify_cb(self):
        assert _classify_strategy("small_size") == "convertible_bond"
        assert _classify_strategy("cb_comprehensive") == "convertible_bond"
        assert _classify_strategy("double_low_plus") == "convertible_bond"

    def test_classify_equity(self):
        assert _classify_strategy("balanced_mf") == "equity"
        assert _classify_strategy("dividend_defensive") == "equity"
        assert _classify_strategy("pead_event") == "equity"

    def test_classify_demo(self):
        assert _classify_strategy("demo_strategy") == "demo"

    def test_classify_other(self):
        assert _classify_strategy("unknown_xyz") == "other"


# ============================================================================
# Schema 初始化测试
# ============================================================================


class TestSchemaInit:
    """数据库 schema 初始化测试。"""

    def test_creates_db_file(self, tmp_db: Path):
        store = ResultStore(db_path=tmp_db)
        assert tmp_db.is_file()
        store.close()

    def test_creates_parent_dirs(self, tmp_path: Path):
        deep_path = tmp_path / "a" / "b" / "c" / "test.db"
        store = ResultStore(db_path=deep_path)
        assert deep_path.is_file()
        store.close()

    def test_schema_version_recorded(self, store: ResultStore):
        """新建库应把当前 SCHEMA_VERSION 写进 schema_meta。

        断言常量而不是字面量：这条测的是「建库时把版本号落盘了」，
        不是「版本号恰好等于 7」。写死字面量的话每次 migration 都得回来
        改测试，而改测试的人无从判断到底该不该改 —— 之前就是这样从 7
        落后到 9 的。
        """
        from aipha.backtest.result_store import SCHEMA_VERSION

        cur = store.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == str(SCHEMA_VERSION)
        # 顺手排除「常量还是初版没人维护」：migration 表应覆盖到当前版本
        assert SCHEMA_VERSION >= 9

    def test_tables_exist(self, store: ResultStore):
        cur = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cur.fetchall()}
        assert "backtest_runs" in tables
        assert "wf_results" in tables
        assert "sweep_runs" in tables
        assert "schema_meta" in tables
        assert "strategy_configs" in tables
        assert "users" in tables

    def test_idempotent_init(self, tmp_db: Path):
        """重复初始化不报错。"""
        s1 = ResultStore(db_path=tmp_db)
        s1.close()
        s2 = ResultStore(db_path=tmp_db)
        s2.close()


# ============================================================================
# 回测运行 CRUD 测试
# ============================================================================


class TestBacktestRunCRUD:
    """回测运行的增删改查测试。"""

    def test_upsert_and_get(self, store: ResultStore):
        run = _make_run()
        store.upsert_backtest_run(run)
        got = store.get_run("test_run_1")
        assert got is not None
        assert got["strategy_name"] == "small_size"
        assert got["sharpe_ratio"] == 1.35

    def test_upsert_replaces_existing(self, store: ResultStore):
        store.upsert_backtest_run(_make_run(sharpe_ratio=1.0))
        store.upsert_backtest_run(_make_run(sharpe_ratio=2.0))
        got = store.get_run("test_run_1")
        assert got["sharpe_ratio"] == 2.0

    def test_get_nonexistent(self, store: ResultStore):
        assert store.get_run("nonexistent") is None

    def test_delete_run(self, store: ResultStore):
        store.upsert_backtest_run(_make_run())
        assert store.delete_run("test_run_1") is True
        assert store.get_run("test_run_1") is None

    def test_delete_nonexistent(self, store: ResultStore):
        assert store.delete_run("nonexistent") is False

    def test_upsert_missing_run_id(self, store: ResultStore):
        with pytest.raises(ValueError, match="run_id"):
            store.upsert_backtest_run({"strategy_name": "x"})

    def test_upsert_missing_strategy_name(self, store: ResultStore):
        with pytest.raises(ValueError, match="strategy_name"):
            store.upsert_backtest_run({"run_id": "x"})

    def test_batch_upsert(self, store: ResultStore):
        runs = [
            _make_run(run_id=f"run_{i}", sharpe_ratio=i * 0.5)
            for i in range(5)
        ]
        count = store.upsert_backtest_runs(runs)
        assert count == 5
        assert store.count_runs() == 5

    def test_batch_upsert_skips_invalid(self, store: ResultStore):
        runs = [
            _make_run(run_id="good"),
            {"bad": "no run_id"},
        ]
        count = store.upsert_backtest_runs(runs)
        assert count == 1

    def test_count_runs_by_type(self, store: ResultStore):
        store.upsert_backtest_run(_make_run(run_id="s1", run_type="single"))
        store.upsert_backtest_run(_make_run(run_id="s2", run_type="sweep_task"))
        assert store.count_runs() == 2
        assert store.count_runs(run_type="single") == 1
        assert store.count_runs(run_type="sweep_task") == 1

    def test_metrics_json_stored(self, store: ResultStore):
        run = _make_run(metrics={"custom_metric": 42})
        store.upsert_backtest_run(run)
        got = store.get_run("test_run_1")
        assert got["metrics_json"] == {"custom_metric": 42}

    def test_config_json_stored(self, store: ResultStore):
        run = _make_run(config={"data": {"source": "tushare"}})
        store.upsert_backtest_run(run)
        got = store.get_run("test_run_1")
        assert got["config_json"]["data"]["source"] == "tushare"


# ── strategy_configs CRUD ──────────────────────────────────────────────────

def _make_cfg(config_id: str = "cfg-001", **overrides) -> dict:
    """构造一条策略配置记录。"""
    base = {
        "config_id":     config_id,
        "config_name":   f"small_size_v{config_id[-1]}",
        "strategy_name": "small_size",
        "yaml_content":  "strategy:\n  name: small_size\n  params:\n    top_k: 10\n",
        "description":   "测试配置",
        "status":        "development",
        "env":           "research",
    }
    base.update(overrides)
    return base


class TestStrategyConfigsCRUD:
    """strategy_configs 表 CRUD 方法测试。"""

    def test_upsert_and_get_by_id(self, store: ResultStore):
        cfg = _make_cfg("cfg-001")
        store.upsert_strategy_config(cfg)
        got = store.get_strategy_config("cfg-001")
        assert got is not None
        assert got["config_name"] == "small_size_v1"
        assert got["strategy_name"] == "small_size"
        assert got["is_active"] == 1

    def test_get_by_name(self, store: ResultStore):
        cfg = _make_cfg("cfg-002", config_name="dl_plus_v1")
        store.upsert_strategy_config(cfg)
        got = store.get_strategy_config_by_name("dl_plus_v1")
        assert got is not None
        assert got["config_id"] == "cfg-002"

    def test_upsert_is_idempotent(self, store: ResultStore):
        cfg = _make_cfg("cfg-003", config_name="same_name")
        store.upsert_strategy_config(cfg)
        # 覆盖同一 config_id
        cfg["yaml_content"] = "strategy:\n  name: small_size\n  params:\n    top_k: 20\n"
        store.upsert_strategy_config(cfg)
        got = store.get_strategy_config("cfg-003")
        assert "top_k: 20" in got["yaml_content"]
        assert store.count_strategy_configs() == 1

    def test_list_strategy_configs(self, store: ResultStore):
        store.upsert_strategy_config(_make_cfg("cfg-a", config_name="small_v1"))
        store.upsert_strategy_config(_make_cfg("cfg-b", config_name="dl_v1",
                                               strategy_name="double_low"))
        configs = store.list_strategy_configs()
        assert len(configs) == 2

    def test_list_filter_by_strategy_name(self, store: ResultStore):
        store.upsert_strategy_config(_make_cfg("cfg-x", config_name="ss_v1"))
        store.upsert_strategy_config(_make_cfg("cfg-y", config_name="dl_v2",
                                               strategy_name="double_low"))
        result = store.list_strategy_configs(strategy_name="small_size")
        assert len(result) == 1
        assert result[0]["config_id"] == "cfg-x"

    def test_list_filter_by_status(self, store: ResultStore):
        store.upsert_strategy_config(_make_cfg("cfg-p1", config_name="s_v1",
                                               status="development"))
        store.upsert_strategy_config(_make_cfg("cfg-p2", config_name="s_v2",
                                               status="validated"))
        validated = store.list_strategy_configs(status="validated")
        assert len(validated) == 1
        assert validated[0]["config_id"] == "cfg-p2"

    def test_soft_delete(self, store: ResultStore):
        store.upsert_strategy_config(_make_cfg("cfg-dd", config_name="to_delete"))
        result = store.soft_delete_strategy_config("cfg-dd")
        assert result is True
        # active_only=True 不返回已删除
        active = store.list_strategy_configs(active_only=True)
        assert len(active) == 0
        # active_only=False 仍能查到
        all_cfgs = store.list_strategy_configs(active_only=False)
        assert len(all_cfgs) == 1
        assert all_cfgs[0]["is_active"] == 0

    def test_soft_delete_nonexistent(self, store: ResultStore):
        result = store.soft_delete_strategy_config("nonexistent-id")
        assert result is False

    def test_update_status(self, store: ResultStore):
        store.upsert_strategy_config(_make_cfg("cfg-st", config_name="status_cfg"))
        ok = store.update_strategy_config_status("cfg-st", "validated", "alice")
        assert ok is True
        got = store.get_strategy_config("cfg-st")
        assert got["status"] == "validated"

    def test_update_status_invalid(self, store: ResultStore):
        store.upsert_strategy_config(_make_cfg("cfg-iv", config_name="inv_cfg"))
        with pytest.raises(ValueError, match="无效状态"):
            store.update_strategy_config_status("cfg-iv", "unknown_status")

    def test_count_strategy_configs(self, store: ResultStore):
        assert store.count_strategy_configs() == 0
        store.upsert_strategy_config(_make_cfg("cfg-c1", config_name="cnt_v1"))
        store.upsert_strategy_config(_make_cfg("cfg-c2", config_name="cnt_v2"))
        assert store.count_strategy_configs() == 2

    def test_missing_required_fields_raises(self, store: ResultStore):
        with pytest.raises(ValueError):
            store.upsert_strategy_config({"config_name": "no_id"})


# ============================================================================
# 用户 CRUD 测试
# ============================================================================


def _make_user(username: str = "testuser", role: str = "researcher") -> dict:
    """构造测试用户数据。"""
    return {
        "username":      username,
        "name":          f"Test {username}",
        "password_hash": "$2b$12$hash_placeholder",
        "role":          role,
        "email":         f"{username}@test.com",
        "created_by":    "test_admin",
    }


class TestUsersCRUD:
    """users 表 CRUD 覆盖。"""

    def test_upsert_and_get(self, store: ResultStore):
        store.upsert_user(_make_user("alice"))
        got = store.get_user("alice")
        assert got is not None
        assert got["name"] == "Test alice"
        assert got["role"] == "researcher"

    def test_upsert_replaces_existing(self, store: ResultStore):
        store.upsert_user(_make_user("bob", role="viewer"))
        store.upsert_user({**_make_user("bob"), "role": "admin"})
        got = store.get_user("bob")
        assert got["role"] == "admin"

    def test_get_nonexistent(self, store: ResultStore):
        assert store.get_user("nobody") is None

    def test_list_users_all(self, store: ResultStore):
        store.upsert_user(_make_user("u1"))
        store.upsert_user(_make_user("u2"))
        users = store.list_users(active_only=False)
        usernames = {u["username"] for u in users}
        assert {"u1", "u2"}.issubset(usernames)

    def test_list_users_active_only(self, store: ResultStore):
        store.upsert_user(_make_user("active_u"))
        store.upsert_user(_make_user("deleted_u"))
        store.soft_delete_user("deleted_u")
        active = store.list_users(active_only=True)
        usernames = {u["username"] for u in active}
        assert "active_u" in usernames
        assert "deleted_u" not in usernames

    def test_update_user_role(self, store: ResultStore):
        store.upsert_user(_make_user("carol", role="viewer"))
        store.update_user_role("carol", "ops")
        got = store.get_user("carol")
        assert got["role"] == "ops"

    def test_update_user_password(self, store: ResultStore):
        store.upsert_user(_make_user("dave"))
        store.update_user_password("dave", "$2b$12$new_hash")
        got = store.get_user("dave")
        assert got["password_hash"] == "$2b$12$new_hash"

    def test_soft_delete(self, store: ResultStore):
        store.upsert_user(_make_user("eve"))
        store.soft_delete_user("eve")
        got = store.get_user("eve")
        assert got is not None
        assert got["is_active"] == 0

    def test_count_users(self, store: ResultStore):
        assert store.count_users() == 0
        store.upsert_user(_make_user("frank"))
        assert store.count_users() == 1
        store.soft_delete_user("frank")
        assert store.count_users(active_only=True) == 0
        assert store.count_users(active_only=False) == 1

    def test_record_user_login(self, store: ResultStore):
        store.upsert_user(_make_user("grace"))
        store.record_user_login("grace", success=True)
        got = store.get_user("grace")
        assert got["last_login"] is not None

    def test_build_stauth_credentials(self, store: ResultStore):
        store.upsert_user(_make_user("heidi", role="admin"))
        creds = store.build_stauth_credentials()
        assert "usernames" in creds
        assert "heidi" in creds["usernames"]
        uc = creds["usernames"]["heidi"]
        assert uc["role"] == "admin"
        assert "password" in uc
        assert "name" in uc

    def test_missing_required_field_raises(self, store: ResultStore):
        with pytest.raises(ValueError):
            store.upsert_user({"name": "no_username"})


# ============================================================================
# WF 结果 PBO/DSR 字段（v5 迁移）
# ============================================================================

def _make_wf(wf_id: str = "wf_test_001", **overrides) -> dict:
    base = {
        "wf_id": wf_id,
        "strategy_name": "ml_csi2000",
        "verdict": "pass",
        "score": 7.5,
        "n_splits": 6,
    }
    base.update(overrides)
    return base


class TestWfResultsPboFields:
    """wf_results 表的 pbo / dsr / hlz_adjusted_t 字段测试。"""

    def test_upsert_wf_with_pbo_dsr(self, store: ResultStore):
        wf = _make_wf(pbo=0.35, dsr=1.23, hlz_adjusted_t=2.5)
        store.upsert_wf_result(wf)
        rows = store.query_wf_results("ml_csi2000")
        assert len(rows) == 1
        row = rows[0]
        assert row["pbo"] == pytest.approx(0.35)
        assert row["dsr"] == pytest.approx(1.23)
        assert row["hlz_adjusted_t"] == pytest.approx(2.5)

    def test_upsert_wf_without_stats_gets_null(self, store: ResultStore):
        wf = _make_wf(wf_id="wf_no_stats")
        store.upsert_wf_result(wf)
        rows = store.query_wf_results("ml_csi2000")
        row = next(r for r in rows if r["wf_id"] == "wf_no_stats")
        assert row["pbo"] is None
        assert row["dsr"] is None
        assert row["hlz_adjusted_t"] is None

    def test_wf_upsert_idempotent_with_stats(self, store: ResultStore):
        wf = _make_wf(pbo=0.40, dsr=0.80, hlz_adjusted_t=1.9)
        store.upsert_wf_result(wf)
        # 更新 pbo 值
        wf["pbo"] = 0.42
        store.upsert_wf_result(wf)
        rows = store.query_wf_results("ml_csi2000")
        assert rows[0]["pbo"] == pytest.approx(0.42)

    def test_wf_missing_required_fields_raises(self, store: ResultStore):
        with pytest.raises(ValueError):
            store.upsert_wf_result({"pbo": 0.3, "dsr": 1.0})

    def test_schema_has_pbo_column(self, store: ResultStore):
        """验证 wf_results 表包含 pbo/dsr/hlz_adjusted_t 列"""
        cur = store.conn.execute("PRAGMA table_info(wf_results)")
        col_names = [row[1] for row in cur.fetchall()]
        assert "pbo" in col_names
        assert "dsr" in col_names
        assert "hlz_adjusted_t" in col_names


