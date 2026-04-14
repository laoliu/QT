"""ResultStore 单元测试 — 查询、WF/Sweep、迁移工具。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from aipha.backtest.result_store import (
    ResultStore,
    import_existing_results,
    summary_json_to_run_dict,
    wf_json_to_wf_dict,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_index.db"


@pytest.fixture
def store(tmp_db: Path) -> ResultStore:
    s = ResultStore(db_path=tmp_db)
    yield s
    s.close()


def _make_run(run_id: str = "test_run_1", **overrides) -> dict:
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
        "run_type": "single",
        "output_dir": "/tmp/test_run_1",
    }
    base.update(overrides)
    return base


# ============================================================================
# 查询测试
# ============================================================================


class TestQueryRuns:
    """回测运行查询测试。"""

    def test_query_by_strategy(self, store: ResultStore):
        store.upsert_backtest_run(_make_run(run_id="r1", strategy_name="small_size"))
        store.upsert_backtest_run(_make_run(run_id="r2", strategy_name="double_low"))
        results = store.query_runs(strategy_name="small_size")
        assert len(results) == 1
        assert results[0]["run_id"] == "r1"

    def test_query_by_category(self, store: ResultStore):
        store.upsert_backtest_run(_make_run(run_id="r1", category="convertible_bond"))
        store.upsert_backtest_run(_make_run(run_id="r2", category="equity"))
        results = store.query_runs(category="equity")
        assert len(results) == 1
        assert results[0]["run_id"] == "r2"

    def test_query_by_min_sharpe(self, store: ResultStore):
        store.upsert_backtest_run(_make_run(run_id="r1", sharpe_ratio=0.5))
        store.upsert_backtest_run(_make_run(run_id="r2", sharpe_ratio=1.5))
        store.upsert_backtest_run(_make_run(run_id="r3", sharpe_ratio=2.0))
        results = store.query_runs(min_sharpe=1.0)
        assert len(results) == 2
        ids = {r["run_id"] for r in results}
        assert ids == {"r2", "r3"}

    def test_query_by_max_drawdown(self, store: ResultStore):
        store.upsert_backtest_run(_make_run(run_id="r1", max_drawdown=-0.05))
        store.upsert_backtest_run(_make_run(run_id="r2", max_drawdown=-0.30))
        results = store.query_runs(max_drawdown=0.10)
        assert len(results) == 1
        assert results[0]["run_id"] == "r1"

    def test_query_by_run_type(self, store: ResultStore):
        store.upsert_backtest_run(_make_run(run_id="r1", run_type="single"))
        store.upsert_backtest_run(_make_run(run_id="r2", run_type="sweep_task"))
        results = store.query_runs(run_type="sweep_task")
        assert len(results) == 1

    def test_query_by_parent_id(self, store: ResultStore):
        store.upsert_backtest_run(_make_run(run_id="child1", parent_id="sweep_1"))
        store.upsert_backtest_run(_make_run(run_id="child2", parent_id="sweep_1"))
        store.upsert_backtest_run(_make_run(run_id="other"))
        results = store.query_runs(parent_id="sweep_1")
        assert len(results) == 2

    def test_query_order_by_sharpe(self, store: ResultStore):
        store.upsert_backtest_run(_make_run(run_id="r1", sharpe_ratio=0.5))
        store.upsert_backtest_run(_make_run(run_id="r2", sharpe_ratio=2.0))
        results = store.query_runs(order_by="sharpe_ratio DESC")
        assert results[0]["run_id"] == "r2"

    def test_query_limit(self, store: ResultStore):
        for i in range(20):
            store.upsert_backtest_run(_make_run(run_id=f"r{i}"))
        results = store.query_runs(limit=5)
        assert len(results) == 5

    def test_query_order_by_injection_safe(self, store: ResultStore):
        """恶意 order_by 不会导致 SQL 注入。"""
        store.upsert_backtest_run(_make_run())
        results = store.query_runs(order_by="1; DROP TABLE backtest_runs;--")
        assert len(results) >= 0  # 不报错即可

    def test_list_strategies(self, store: ResultStore):
        store.upsert_backtest_run(_make_run(run_id="r1", strategy_name="a", sharpe_ratio=1.0))
        store.upsert_backtest_run(_make_run(run_id="r2", strategy_name="a", sharpe_ratio=2.0))
        store.upsert_backtest_run(_make_run(run_id="r3", strategy_name="b", sharpe_ratio=0.5))
        strategies = store.list_strategies()
        assert len(strategies) == 2
        # 按 best_sharpe DESC 排序
        assert strategies[0]["strategy_name"] == "a"
        assert strategies[0]["best_sharpe"] == 2.0
        assert strategies[0]["run_count"] == 2

    def test_combined_filters(self, store: ResultStore):
        store.upsert_backtest_run(_make_run(
            run_id="r1", strategy_name="small_size",
            category="convertible_bond", sharpe_ratio=1.5,
        ))
        store.upsert_backtest_run(_make_run(
            run_id="r2", strategy_name="small_size",
            category="convertible_bond", sharpe_ratio=0.3,
        ))
        results = store.query_runs(
            strategy_name="small_size", category="convertible_bond",
            min_sharpe=1.0,
        )
        assert len(results) == 1
        assert results[0]["run_id"] == "r1"


# ============================================================================
# WF 结果测试
# ============================================================================


class TestWFResults:
    """Walk-Forward 验证结果测试。"""

    def _make_wf(self, wf_id="wf_1", **overrides):
        base = {
            "wf_id": wf_id,
            "strategy_name": "small_size",
            "verdict": "PASS",
            "score": 7.5,
            "config_path": "configs/test.yml",
            "data_range": "2018-01-02 ~ 2024-12-31",
            "train_days": 504,
            "test_days": 252,
            "n_splits": 5,
            "purged": True,
        }
        base.update(overrides)
        return base

    def test_upsert_and_query(self, store: ResultStore):
        store.upsert_wf_result(self._make_wf())
        results = store.query_wf_results("small_size")
        assert len(results) == 1
        assert results[0]["verdict"] == "PASS"
        assert results[0]["score"] == 7.5

    def test_upsert_replaces(self, store: ResultStore):
        store.upsert_wf_result(self._make_wf(score=5.0))
        store.upsert_wf_result(self._make_wf(score=8.0))
        results = store.query_wf_results("small_size")
        assert len(results) == 1
        assert results[0]["score"] == 8.0

    def test_query_all_wf(self, store: ResultStore):
        store.upsert_wf_result(self._make_wf(wf_id="w1", strategy_name="a"))
        store.upsert_wf_result(self._make_wf(wf_id="w2", strategy_name="b"))
        results = store.query_wf_results()
        assert len(results) == 2

    def test_missing_wf_id(self, store: ResultStore):
        with pytest.raises(ValueError):
            store.upsert_wf_result({"strategy_name": "x"})


# ============================================================================
# Sweep 结果测试
# ============================================================================


class TestSweepResults:
    """Sweep 汇总测试。"""

    def _make_sweep(self, sweep_id="sweep_1", **overrides):
        base = {
            "sweep_id": sweep_id,
            "strategy_name": "small_size",
            "total_tasks": 48,
            "best_params": {"top_k": 5, "rebal": 3},
            "best_sharpe": 1.8,
            "output_dir": "/tmp/sweep_1",
        }
        base.update(overrides)
        return base

    def test_upsert_and_query(self, store: ResultStore):
        store.upsert_sweep_run(self._make_sweep())
        results = store.query_sweep_runs("small_size")
        assert len(results) == 1
        assert results[0]["best_sharpe"] == 1.8
        assert results[0]["total_tasks"] == 48

    def test_query_all_sweeps(self, store: ResultStore):
        store.upsert_sweep_run(self._make_sweep(sweep_id="s1", strategy_name="a"))
        store.upsert_sweep_run(self._make_sweep(sweep_id="s2", strategy_name="b"))
        results = store.query_sweep_runs()
        assert len(results) == 2

    def test_missing_sweep_id(self, store: ResultStore):
        with pytest.raises(ValueError):
            store.upsert_sweep_run({"strategy_name": "x"})


# ============================================================================
# 转换函数测试
# ============================================================================


class TestConversionFunctions:
    """summary_json_to_run_dict / wf_json_to_wf_dict 测试。"""

    def test_summary_json_to_run_dict(self, tmp_path: Path):
        """从 summary.json 转换为 DB 记录。"""
        run_dir = tmp_path / "my_run"
        run_dir.mkdir()
        summary = {
            "strategy_name": "small_size",
            "start_date": "2018-01-02",
            "end_date": "2024-12-31",
            "initial_capital": 1_000_000,
            "final_nav": 2_000_000,
            "total_trades": 100,
            "total_fees": 5000,
            "metrics": {
                "annualized_return": 0.12,
                "max_drawdown_pct": -0.15,
                "sharpe_ratio": 1.2,
                "sortino_ratio": 1.6,
                "calmar_ratio": 0.8,
                "win_rate": 0.6,
                "profit_factor": 1.8,
                "total_trades": 100,
                "avg_positions": 5.0,
                "max_positions": 8,
            },
            "cost_attribution": {
                "total_fees": 5000,
                "cost_drag_annualized": 0.002,
            },
        }
        summary_path = run_dir / "summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        result = summary_json_to_run_dict(summary_path)
        assert result["run_id"] == "my_run"
        assert result["strategy_name"] == "small_size"
        assert result["category"] == "convertible_bond"
        assert result["sharpe_ratio"] == 1.2
        assert result["output_dir"] == str(run_dir)

    def test_summary_with_fingerprint(self, tmp_path: Path):
        """带 fingerprint.json 的转换。"""
        run_dir = tmp_path / "fp_run"
        run_dir.mkdir()
        summary = {
            "strategy_name": "test",
            "start_date": "2020-01-01",
            "end_date": "2024-01-01",
            "initial_capital": 100000,
            "final_nav": 150000,
            "total_trades": 50,
            "total_fees": 1000,
            "metrics": {},
        }
        (run_dir / "summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        fp = {"config_hash": "abc123", "git_commit": "def456", "seed": 42}
        (run_dir / "fingerprint.json").write_text(
            json.dumps(fp), encoding="utf-8"
        )

        result = summary_json_to_run_dict(run_dir / "summary.json")
        assert result["config_hash"] == "abc123"
        assert result["git_commit"] == "def456"
        assert result["seed"] == 42

    def test_wf_json_to_wf_dict(self, tmp_path: Path):
        """WF JSON 文件转换。"""
        wf_data = {
            "strategy_name": "small_size",
            "verdict": "PASS",
            "score": 7.5,
            "config_path": "configs/test.yml",
            "data_range": "2018 ~ 2024",
            "params": {
                "train_days": 504,
                "test_days": 252,
                "n_splits": 5,
                "purged": True,
            },
            "splits": [{"fold": 1, "return": 0.1}],
        }
        wf_path = tmp_path / "small_size_wf.json"
        wf_path.write_text(json.dumps(wf_data), encoding="utf-8")

        result = wf_json_to_wf_dict(wf_path)
        assert result["wf_id"] == "small_size_wf"
        assert result["verdict"] == "PASS"
        assert result["train_days"] == 504
        assert result["purged"] is True


# ============================================================================
# 迁移工具测试
# ============================================================================


class TestImportExistingResults:
    """历史数据迁移测试。"""

    def _create_output_structure(self, base: Path):
        """创建模拟的 output/ 目录结构。"""
        # 普通回测
        for name, sn in [("run_a", "small_size"), ("run_b", "double_low")]:
            d = base / name
            d.mkdir(parents=True)
            summary = {
                "strategy_name": sn,
                "start_date": "2020-01-01",
                "end_date": "2024-01-01",
                "initial_capital": 100000,
                "final_nav": 150000,
                "total_trades": 50,
                "total_fees": 1000,
                "metrics": {"sharpe_ratio": 1.2},
            }
            (d / "summary.json").write_text(
                json.dumps(summary), encoding="utf-8"
            )

        # 孤儿目录（无 summary.json）
        orphan = base / "orphan_run"
        orphan.mkdir()
        (orphan / "backtest_report.html").write_text("<html/>")

        # WF 结果
        wf_dir = base / "wf_cb"
        wf_dir.mkdir()
        wf_data = {
            "strategy_name": "small_size",
            "verdict": "PASS",
            "score": 7.0,
            "config_path": "test.yml",
            "data_range": "2018~2024",
            "params": {"train_days": 252, "test_days": 63, "n_splits": 5, "purged": False},
        }
        (wf_dir / "small_size_wf.json").write_text(
            json.dumps(wf_data), encoding="utf-8"
        )

    def test_import_basic(self, tmp_path: Path):
        base = tmp_path / "output"
        base.mkdir()
        self._create_output_structure(base)

        db_path = base / "backtest_index.db"
        store = ResultStore(db_path=db_path)
        stats = import_existing_results(base, store=store)

        assert stats["imported"] == 2
        assert stats["wf_imported"] == 1
        assert store.count_runs() == 2
        store.close()

    def test_import_skips_orphans(self, tmp_path: Path):
        base = tmp_path / "output"
        base.mkdir()
        self._create_output_structure(base)

        db_path = base / "backtest_index.db"
        store = ResultStore(db_path=db_path)
        stats = import_existing_results(base, store=store)

        # orphan_run 没有 summary.json，不会被导入
        assert store.get_run("orphan_run") is None
        store.close()

    def test_import_idempotent(self, tmp_path: Path):
        """重复导入不会产生重复记录。"""
        base = tmp_path / "output"
        base.mkdir()
        self._create_output_structure(base)

        db_path = base / "backtest_index.db"
        store = ResultStore(db_path=db_path)
        import_existing_results(base, store=store)
        import_existing_results(base, store=store)  # 第二次

        assert store.count_runs() == 2  # 没有翻倍
        store.close()

    def test_import_empty_dir(self, tmp_path: Path):
        base = tmp_path / "empty_output"
        base.mkdir()
        stats = import_existing_results(base)
        assert stats["imported"] == 0

    def test_import_nonexistent_dir(self, tmp_path: Path):
        stats = import_existing_results(tmp_path / "nonexistent")
        assert stats["imported"] == 0
