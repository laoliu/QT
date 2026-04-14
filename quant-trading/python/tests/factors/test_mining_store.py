"""MiningStore 单元测试."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from aipha.factors.mining_store import MiningStore


@pytest.fixture()
def store(tmp_path: Path) -> MiningStore:
    db_path = tmp_path / "test_mining.db"
    return MiningStore(db_path=db_path)


class TestMiningStoreInit:
    def test_creates_db_file(self, store: MiningStore) -> None:
        assert store.db_path.exists()

    def test_tables_exist(self, store: MiningStore) -> None:
        import sqlite3
        with sqlite3.connect(str(store.db_path)) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table'"
            ).fetchall()}
        assert "mined_factors" in tables
        assert "eval_runs" in tables
        assert "eval_factors" in tables
        assert "mining_rounds" in tables


class TestUpsertMinedFactor:
    def test_upsert_single(self, store: MiningStore) -> None:
        store.upsert_mined_factor({
            "factor_id": "R1_test_factor",
            "name": "test_factor",
            "expression": "ts_mean($close, 5)",
            "category": "mutation",
            "method": "window_swap",
            "ic_mean": 0.035,
            "icir": 0.72,
            "post_ic": 0.025,
            "fitness": 0.6,
            "passed": True,
            "failure_reason": "",
            "round_id": 1,
            "kb_path": "/tmp/kb.json",
            "discovered_at": "2026-03-15T10:00:00",
        })
        rows = store.query_mined_factors(passed=True)
        assert len(rows) == 1
        assert rows[0]["name"] == "test_factor"

    def test_upsert_batch(self, store: MiningStore) -> None:
        batch = [
            {"factor_id": f"R1_f{i}", "name": f"f{i}",
             "expression": f"ts_mean($close, {i})",
             "category": "mutation", "method": "op",
             "ic_mean": 0.03, "icir": 0.5 + i * 0.1,
             "post_ic": 0.02, "fitness": 0.5,
             "passed": i % 2 == 0, "failure_reason": "",
             "round_id": 1, "kb_path": "/tmp/kb.json",
             "discovered_at": "2026-03-15T10:00:00"}
            for i in range(6)
        ]
        store.upsert_mined_factors_batch(batch)
        all_rows = store.query_mined_factors()
        assert len(all_rows) == 6
        passed = store.query_mined_factors(passed=True)
        assert len(passed) == 3

    def test_count(self, store: MiningStore) -> None:
        store.upsert_mined_factor({
            "factor_id": "R1_c", "name": "c",
            "expression": "e", "category": "m", "method": "m",
            "ic_mean": 0.04, "icir": 0.8, "post_ic": 0.03,
            "fitness": 0.7, "passed": True, "failure_reason": "",
            "round_id": 1, "kb_path": "", "discovered_at": "",
        })
        assert store.count_mined_factors() == 1
        assert store.count_mined_factors(passed=True) == 1
        assert store.count_mined_factors(passed=False) == 0


class TestEvalRunAndFactors:
    def test_upsert_eval_run(self, store: MiningStore) -> None:
        store.upsert_eval_run({
            "eval_id": "eval_001",
            "kb_path": "/tmp/kb.json",
            "universe": "csi2000",
            "start_date": "2015-01-05",
            "end_date": "2026-01-01",
            "fwd_days": 10,
            "n_passed": 30, "n_computed": 28,
            "n_clusters": 8, "n_independent": 18,
            "run_time_s": 45.2,
            "config_json": {"key": "val"},
        })
        runs = store.query_eval_runs()
        assert len(runs) == 1
        assert runs[0]["eval_id"] == "eval_001"
        assert runs[0]["config_json"] == {"key": "val"}

    def test_upsert_eval_factor(self, store: MiningStore) -> None:
        store.upsert_eval_run({
            "eval_id": "eval_002", "kb_path": "", "universe": "",
            "start_date": "", "end_date": "", "fwd_days": 10,
            "n_passed": 0, "n_computed": 0, "n_clusters": 0,
            "n_independent": 0, "run_time_s": 0, "config_json": None,
        })
        store.upsert_eval_factor({
            "eval_id": "eval_002",
            "factor_name": "alpha1",
            "expression": "ts_rank($close, 10)",
            "kb_icir": 1.2, "kb_ic_mean": 0.04,
            "composite_score": 0.85,
            "monotonicity": 0.9, "avg_turnover": 0.3,
            "wf_oos_icir": 0.8, "wf_n_folds": 5,
            "ic_decay_json": {1: 0.05, 5: 0.04, 10: 0.03},
            "quantile_json": {"monotonicity": 0.9},
            "cluster_id": 1, "is_representative": True,
        })
        factors = store.query_eval_factors(eval_id="eval_002")
        assert len(factors) == 1
        assert factors[0]["composite_score"] == 0.85
        assert factors[0]["ic_decay_json"] is not None


class TestMiningRounds:
    def test_upsert_and_query(self, store: MiningStore) -> None:
        for i in range(5):
            store.upsert_mining_round({
                "round_id": i + 1, "kb_path": "/tmp/kb.json",
                "generated": 40, "passed": i,
                "failed": 40 - i, "dh_score": 0.5,
                "elapsed_s": 10.0,
            })
        rounds = store.query_mining_rounds()
        assert len(rounds) == 5
        # most recent first
        assert rounds[0]["round_id"] == 5


class TestDashboardStats:
    def test_empty_db(self, store: MiningStore) -> None:
        stats = store.get_dashboard_stats()
        assert stats["total_mined"] == 0
        assert stats["pass_rate"] == 0

    def test_with_data(self, store: MiningStore) -> None:
        for i in range(10):
            store.upsert_mined_factor({
                "factor_id": f"R1_f{i}", "name": f"f{i}",
                "expression": f"e{i}", "category": "m",
                "method": "m", "ic_mean": 0.03,
                "icir": 0.5 + i * 0.1, "post_ic": 0.02,
                "fitness": 0.5, "passed": i < 4,
                "failure_reason": "", "round_id": 1,
                "kb_path": "", "discovered_at": "",
            })
        stats = store.get_dashboard_stats()
        assert stats["total_mined"] == 10
        assert stats["total_passed"] == 4
        assert stats["pass_rate"] == 40.0
