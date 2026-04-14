"""Tests for parallel_runner module.

Tests cover:
- Parameter grid generation (generate_param_grid)
- Task creation (create_sweep_tasks)
- Worker function (_worker_run_single)
- Parallel orchestration (run_parallel_backtests)
- Sweep summary statistics
- Error handling in workers
- Convenience entry point (sweep_strategy_params)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from aipha.backtest.parallel_runner import (
    ParallelResult,
    ParallelTask,
    SweepSummary,
    _make_param_label,
    _worker_run_single,
    create_sweep_tasks,
    generate_param_grid,
    run_parallel_backtests,
    sweep_strategy_params,
)


# ============================================================================
# 基础配置字典（synthetic 数据源 + buy_and_hold 策略）
# ============================================================================

_BASE_CONFIG = {
    "name": "test_sweep",
    "data": {
        "source": "synthetic",
        "start_date": "2024-01-01",
        "end_date": "2024-06-30",
        "symbols": ["600000.SH", "000001.SZ"],
    },
    "strategy": {
        "name": "demo_dual_ma",
        "params": {"short_window": 5, "long_window": 20},
    },
    "cost": {
        "commission_rate": 0.0001,
        "stamp_tax_rate": 0.0,
        "slippage_bps": 0.0,
    },
    "risk": {
        "enabled": False,
    },
    "initial_capital": 100_000,
    "random_seed": 42,
    "output_dir": "output/test_sweep",
}


# ============================================================================
# generate_param_grid tests
# ============================================================================


class TestGenerateParamGrid:
    """Tests for generate_param_grid function."""

    def test_empty_ranges_returns_single_empty(self):
        """Empty param_ranges → single empty dict."""
        result = generate_param_grid({})
        assert result == [{}]

    def test_single_param_single_value(self):
        """Single param with single value → one combo."""
        result = generate_param_grid({"short_window": [5]})
        assert result == [{"short_window": 5}]

    def test_single_param_multiple_values(self):
        """Single param with multiple values → N combos."""
        result = generate_param_grid({"short_window": [3, 5, 8]})
        assert len(result) == 3
        assert result[0] == {"short_window": 3}
        assert result[1] == {"short_window": 5}
        assert result[2] == {"short_window": 8}

    def test_two_params_cartesian_product(self):
        """Two params → full Cartesian product."""
        result = generate_param_grid({
            "short_window": [3, 5],
            "long_window": [10, 20],
        })
        assert len(result) == 4
        # Check all 4 combinations exist
        expected = [
            {"short_window": 3, "long_window": 10},
            {"short_window": 3, "long_window": 20},
            {"short_window": 5, "long_window": 10},
            {"short_window": 5, "long_window": 20},
        ]
        assert result == expected

    def test_three_params_cartesian_product(self):
        """Three params → 2×2×2 = 8 combos."""
        result = generate_param_grid({
            "a": [1, 2],
            "b": [10, 20],
            "c": ["x", "y"],
        })
        assert len(result) == 8

    def test_preserves_value_types(self):
        """Values retain their original types."""
        result = generate_param_grid({
            "int_param": [5],
            "float_param": [0.5],
            "str_param": ["high"],
            "bool_param": [True],
        })
        assert len(result) == 1
        combo = result[0]
        assert isinstance(combo["int_param"], int)
        assert isinstance(combo["float_param"], float)
        assert isinstance(combo["str_param"], str)
        assert isinstance(combo["bool_param"], bool)

    def test_large_grid(self):
        """Large grid calculation is correct."""
        result = generate_param_grid({
            "a": list(range(5)),
            "b": list(range(4)),
            "c": list(range(3)),
        })
        assert len(result) == 5 * 4 * 3


# ============================================================================
# _make_param_label tests
# ============================================================================


class TestMakeParamLabel:
    """Tests for _make_param_label helper."""

    def test_no_changes_returns_baseline(self):
        """No differences → 'baseline'."""
        assert _make_param_label({"a": 1}, {"a": 1}) == "baseline"

    def test_changed_values(self):
        """Changed values appear in label."""
        label = _make_param_label(
            {"short_window": 8, "long_window": 20},
            {"short_window": 5, "long_window": 20},
        )
        assert "short_window=8" in label

    def test_new_keys(self):
        """New keys not in base appear in label."""
        label = _make_param_label(
            {"short_window": 5, "extra": 10},
            {"short_window": 5},
        )
        assert "extra=10" in label

    def test_float_formatting(self):
        """Float values are formatted concisely."""
        label = _make_param_label(
            {"rate": 0.001234},
            {},
        )
        assert "rate=0.001234" in label


# ============================================================================
# create_sweep_tasks tests
# ============================================================================


class TestCreateSweepTasks:
    """Tests for create_sweep_tasks function."""

    def test_creates_correct_number_of_tasks(self):
        """Number of tasks = size of param grid."""
        tasks = create_sweep_tasks(
            _BASE_CONFIG,
            {"short_window": [3, 5, 8]},
        )
        assert len(tasks) == 3

    def test_task_ids_are_sequential(self):
        """Task IDs are 0, 1, 2, ..."""
        tasks = create_sweep_tasks(
            _BASE_CONFIG,
            {"short_window": [3, 5, 8]},
        )
        assert [t.task_id for t in tasks] == [0, 1, 2]

    def test_task_configs_have_overridden_params(self):
        """Each task's config has the overridden strategy params."""
        tasks = create_sweep_tasks(
            _BASE_CONFIG,
            {"short_window": [3, 5]},
        )
        assert tasks[0].config_dict["strategy"]["params"]["short_window"] == 3
        assert tasks[1].config_dict["strategy"]["params"]["short_window"] == 5

    def test_base_params_preserved(self):
        """Non-overridden params remain from base config."""
        tasks = create_sweep_tasks(
            _BASE_CONFIG,
            {"short_window": [3]},
        )
        assert tasks[0].config_dict["strategy"]["params"]["long_window"] == 20

    def test_unique_output_dirs(self):
        """Each task has a unique output directory."""
        tasks = create_sweep_tasks(
            _BASE_CONFIG,
            {"short_window": [3, 5, 8]},
        )
        dirs = {t.output_dir for t in tasks}
        assert len(dirs) == 3

    def test_does_not_mutate_base_config(self):
        """Base config dict is not modified."""
        import copy
        original = copy.deepcopy(_BASE_CONFIG)
        create_sweep_tasks(_BASE_CONFIG, {"short_window": [3, 5]})
        assert _BASE_CONFIG == original

    def test_custom_output_base_dir(self):
        """Custom output base directory is used."""
        tasks = create_sweep_tasks(
            _BASE_CONFIG,
            {"short_window": [3]},
            output_base_dir="output/custom",
        )
        # Use Path for cross-platform comparison
        assert Path("output/custom") in Path(tasks[0].output_dir).parents or \
            "custom" in tasks[0].output_dir

    def test_param_labels_are_set(self):
        """Tasks have meaningful param labels."""
        tasks = create_sweep_tasks(
            _BASE_CONFIG,
            {"short_window": [3, 5]},
        )
        assert tasks[0].param_label  # non-empty
        assert "short_window=3" in tasks[0].param_label

    def test_two_param_sweep(self):
        """Two-param sweep creates correct number and overrides."""
        tasks = create_sweep_tasks(
            _BASE_CONFIG,
            {"short_window": [3, 5], "long_window": [10, 30]},
        )
        assert len(tasks) == 4
        # First task: short_window=3, long_window=10
        p = tasks[0].config_dict["strategy"]["params"]
        assert p["short_window"] == 3
        assert p["long_window"] == 10

    def test_empty_ranges_creates_single_baseline_task(self):
        """Empty param_ranges → single task with original params."""
        tasks = create_sweep_tasks(_BASE_CONFIG, {})
        assert len(tasks) == 1
        assert tasks[0].param_label == "baseline"


# ============================================================================
# _worker_run_single tests (with synthetic data)
# ============================================================================


class TestWorkerRunSingle:
    """Tests for _worker_run_single function.

    Uses synthetic data + buy_and_hold strategy for fast execution.
    """

    def test_successful_run(self):
        """Worker returns success with valid metrics."""
        import copy
        config = copy.deepcopy(_BASE_CONFIG)
        task = ParallelTask(
            task_id=0,
            config_dict=config,
            param_label="test",
        )
        result = _worker_run_single(task)

        assert result.success
        assert result.task_id == 0
        assert result.param_label == "test"
        assert result.elapsed_seconds >= 0  # 极快回测可能为 0.0
        assert "annualized_return" in result.metrics
        assert "max_drawdown_pct" in result.metrics
        assert "sharpe_ratio" in result.metrics

    def test_metrics_are_numeric(self):
        """All returned metrics are numeric."""
        import copy
        config = copy.deepcopy(_BASE_CONFIG)
        task = ParallelTask(task_id=1, config_dict=config)
        result = _worker_run_single(task)

        assert result.success
        for k, v in result.metrics.items():
            assert isinstance(v, (int, float)), f"Metric {k}={v} not numeric"

    def test_invalid_strategy_returns_error(self):
        """Invalid strategy name → result.success=False with error message."""
        import copy
        config = copy.deepcopy(_BASE_CONFIG)
        config["strategy"]["name"] = "nonexistent_strategy_xyz"
        task = ParallelTask(task_id=2, config_dict=config)
        result = _worker_run_single(task)

        assert not result.success
        assert result.error  # non-empty error message
        assert result.elapsed_seconds >= 0

    def test_params_recorded_in_result(self):
        """Strategy params are recorded in result."""
        import copy
        config = copy.deepcopy(_BASE_CONFIG)
        config["strategy"]["params"]["short_window"] = 8
        task = ParallelTask(task_id=3, config_dict=config)
        result = _worker_run_single(task)

        assert result.params.get("short_window") == 8


# ============================================================================
# run_parallel_backtests tests
# ============================================================================


class TestRunParallelBacktests:
    """Tests for run_parallel_backtests orchestration."""

    def test_empty_tasks_returns_empty_summary(self):
        """Empty task list → empty SweepSummary."""
        summary = run_parallel_backtests([])
        assert summary.total_tasks == 0
        assert summary.success_count == 0
        assert summary.fail_count == 0
        assert summary.results == []
        assert summary.best_result is None

    def test_single_task_serial(self):
        """Single task runs successfully (max_workers=1)."""
        import copy
        config = copy.deepcopy(_BASE_CONFIG)
        tasks = [ParallelTask(task_id=0, config_dict=config, param_label="single")]

        summary = run_parallel_backtests(tasks, max_workers=1)

        assert summary.total_tasks == 1
        assert summary.success_count == 1
        assert summary.fail_count == 0
        assert summary.best_result is not None
        assert summary.best_result.success

    def test_multiple_tasks_parallel(self):
        """Multiple tasks run in parallel with correct summary."""
        tasks = create_sweep_tasks(
            _BASE_CONFIG,
            {"short_window": [3, 5]},
        )

        summary = run_parallel_backtests(tasks, max_workers=2)

        assert summary.total_tasks == 2
        assert summary.success_count == 2
        assert summary.fail_count == 0
        assert len(summary.results) == 2

    def test_results_sorted_by_annualized_return(self):
        """Successful results are sorted by annualized_return descending."""
        tasks = create_sweep_tasks(
            _BASE_CONFIG,
            {"short_window": [3, 5, 8]},
        )

        summary = run_parallel_backtests(tasks, max_workers=2)

        success_results = [r for r in summary.results if r.success]
        returns = [r.metrics.get("annualized_return", float("-inf")) for r in success_results]
        assert returns == sorted(returns, reverse=True)

    def test_best_result_has_highest_return(self):
        """best_result has the highest annualized_return."""
        tasks = create_sweep_tasks(
            _BASE_CONFIG,
            {"short_window": [3, 5]},
        )

        summary = run_parallel_backtests(tasks, max_workers=2)

        if summary.success_count > 0:
            best_ar = summary.best_result.metrics.get("annualized_return", float("-inf"))
            for r in summary.results:
                if r.success:
                    assert r.metrics.get("annualized_return", float("-inf")) <= best_ar

    def test_mixed_success_and_failure(self):
        """Mix of valid and invalid tasks → correct counts."""
        import copy
        good_config = copy.deepcopy(_BASE_CONFIG)
        bad_config = copy.deepcopy(_BASE_CONFIG)
        bad_config["strategy"]["name"] = "nonexistent_strategy_xyz"

        tasks = [
            ParallelTask(task_id=0, config_dict=good_config, param_label="good"),
            ParallelTask(task_id=1, config_dict=bad_config, param_label="bad"),
        ]

        summary = run_parallel_backtests(tasks, max_workers=2)

        assert summary.total_tasks == 2
        assert summary.success_count == 1
        assert summary.fail_count == 1
        assert summary.best_result is not None

    def test_total_elapsed_is_positive(self):
        """Total elapsed time is positive."""
        import copy
        config = copy.deepcopy(_BASE_CONFIG)
        tasks = [ParallelTask(task_id=0, config_dict=config)]

        summary = run_parallel_backtests(tasks, max_workers=1)
        assert summary.total_elapsed > 0


# ============================================================================
# sweep_strategy_params tests
# ============================================================================


class TestSweepStrategyParams:
    """Tests for the convenience sweep_strategy_params entry point."""

    def test_from_yaml_file(self, tmp_path):
        """Can sweep from a YAML config file."""
        import yaml

        config_path = tmp_path / "test_config.yml"
        config_path.write_text(yaml.dump(_BASE_CONFIG), encoding="utf-8")

        summary = sweep_strategy_params(
            config_path=str(config_path),
            param_ranges={"short_window": [3, 5]},
            output_base_dir=str(tmp_path / "sweep_output"),
            max_workers=1,
        )

        assert summary.total_tasks == 2
        assert summary.success_count == 2


# ============================================================================
# SweepSummary tests
# ============================================================================


class TestSweepSummary:
    """Tests for SweepSummary dataclass."""

    def test_dataclass_creation(self):
        """SweepSummary can be created with required fields."""
        summary = SweepSummary(
            total_tasks=5,
            success_count=4,
            fail_count=1,
            total_elapsed=10.5,
            results=[],
        )
        assert summary.total_tasks == 5
        assert summary.best_result is None

    def test_with_best_result(self):
        """SweepSummary can have a best_result."""
        best = ParallelResult(
            task_id=0,
            metrics={"annualized_return": 0.30},
        )
        summary = SweepSummary(
            total_tasks=1,
            success_count=1,
            fail_count=0,
            total_elapsed=5.0,
            results=[best],
            best_result=best,
        )
        assert summary.best_result.metrics["annualized_return"] == 0.30


# ============================================================================
# ParallelTask / ParallelResult tests
# ============================================================================


class TestParallelDataClasses:
    """Tests for ParallelTask and ParallelResult dataclasses."""

    def test_parallel_task_defaults(self):
        """ParallelTask has sensible defaults."""
        task = ParallelTask(task_id=0, config_dict={})
        assert task.param_label == ""
        assert task.output_dir == ""

    def test_parallel_result_defaults(self):
        """ParallelResult has sensible defaults."""
        result = ParallelResult(task_id=0)
        assert result.success is True
        assert result.error == ""
        assert result.metrics == {}
        assert result.params == {}
        assert result.elapsed_seconds == 0.0

    def test_parallel_result_failure(self):
        """ParallelResult can represent failure."""
        result = ParallelResult(
            task_id=1,
            success=False,
            error="ValueError: bad config",
        )
        assert not result.success
        assert "ValueError" in result.error
