"""Observed data contracts used by the chat research Skills (no market I/O)."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from engines.aipha_validation_engine import run
from engines.aipha_factor_engine import run as factor_run


def test_quality_gates_require_observed_inputs_and_do_not_invent_a_pass():
    missing = run({"action": "quality_gates", "strategy_id": "example"})["data"]
    assert missing["validated"] is False
    assert set(missing["missing"]) == {"sharpe", "max_drawdown", "total_trades", "n_obs"}
    complete = run({"action": "quality_gates", "sharpe": 1.2, "max_drawdown": 0.1,
                    "total_trades": 35, "n_obs": 300})
    assert complete["ok"] is True
    assert complete["data"]["passed"] is True
    assert complete["data"]["performed"] == ["basic_quality"]
    assert "pbo:" in complete["data"]["not_performed"][-1]
    failed = run({"action": "quality_gates", "sharpe": -0.5, "max_drawdown": 0.5,
                  "total_trades": 5, "n_obs": 30})["data"]
    assert failed["validated"] is True
    assert failed["passed"] is False
    assert all(not gate["passed"] for gate in failed["gates"])


def test_quality_gates_evaluate_real_optional_dsr_and_pbo():
    matrix = np.random.default_rng(4).normal(0.001, 0.01, (300, 3)).tolist()
    result = run({"action": "quality_gates", "sharpe": 1.2, "max_drawdown": 0.1,
                  "total_trades": 35, "n_obs": 300, "n_trials": 10, "returns_matrix": matrix})["data"]
    assert set(result["performed"]) == {"basic_quality", "dsr", "pbo_sampled_cscv"}
    assert len(result["gates"]) == 6


def test_pbo_requires_multiple_real_series_and_returns_an_estimate():
    assert run({"action": "pbo"})["data"]["validated"] is False
    matrix = np.random.default_rng(42).normal(0, 0.01, (80, 3)).tolist()
    result = run({"action": "pbo", "returns_matrix": matrix})["data"]
    assert result["validated"] is True
    assert result["method"] == "sampled_cscv_fixed_seed"
    assert 0 <= result["pbo"] <= 1
    assert result["strategies"] == 3
    assert result["observations"] == 80


@pytest.mark.parametrize("payload", [
    {"action": "pbo", "returns_matrix": [[0.01], [0.02]]},
    {"action": "pbo", "returns_matrix": [[0.01, float("nan")]] * 20},
    {"action": "pbo", "returns_matrix": [[0.01, -2.0]] * 20},
    {"action": "pbo", "returns_matrix": [[0.01, -0.02]] * 20, "n_partitions": 3},
    {"action": "quality_gates", "sharpe": 1.2, "max_drawdown": 15, "total_trades": 30, "n_obs": 300},
])
def test_malformed_or_insufficient_statistics_fail_closed(payload):
    result = run(payload)
    assert result["ok"] is False
    assert result["validated"] is False


def test_walk_forward_distinguishes_split_plan_from_observed_evaluation():
    params = {"action": "walk_forward", "train_size": 20, "test_size": 10, "n_splits": 2, "purge_gap": 5}
    plan = run({**params, "total_size": 45})["data"]
    assert plan["status"] == "split_plan"
    assert plan["validated"] is False
    # Candidate 0 wins in the training period and loses in the first OOS fold;
    # selecting from OOS would incorrectly pick candidate 1 instead.
    rng = np.random.default_rng(7)
    matrix = rng.normal(0, 0.0001, (45, 2))
    matrix[:20, 0] += 0.01
    matrix[25:35, 0] -= 0.01
    matrix[25:35, 1] += 0.01
    result = run({**params, "returns_matrix": matrix.tolist()})["data"]
    assert result["validated"] is True
    assert result["validation_scope"] == "selection_from_fixed_return_matrix"
    assert result["retrained"] is False
    assert result["folds"][0]["selected_strategy_column"] == 0
    assert result["folds"][0]["test_sharpe"] < 0
    assert result["oos_observations"] == 20
    assert result["oos_returns"][:10] == pytest.approx(matrix[25:35, 0])
    for fold in result["folds"]:
        assert fold["test_range"][0] - fold["train_range"][1] == 5
    assert run({**params, "total_size": 40})["ok"] is False


def test_validation_bridge_reads_the_same_environment_contract_as_other_engines():
    env = {**os.environ, "QUANT_CONFIG": json.dumps({"action": "quality_gates"})}
    result = subprocess.run([sys.executable, "-m", "engines.aipha_validation_engine", "--json"],
                            env=env, input="", capture_output=True, text=True, timeout=20, check=True)
    assert json.loads(result.stdout)["data"]["status"] == "insufficient_data"


def test_factor_compute_requires_real_inputs_but_preserves_a_real_zero():
    assert "pe" in factor_run({"action": "list"})["data"]["factors"]
    missing = factor_run({"action": "compute", "factor_name": "pe", "symbols": ["600519"]})
    assert missing["ok"] is False
    assert missing["status"] == "insufficient_data"
    result = factor_run({"action": "compute", "factor_name": "pe", "symbols": ["600519", "000001"],
                         "trade_date": None, "fundamentals": {"600519": {"pe_ttm": 20}, "000001": {"pe_ttm": 0}}, "normalize": "rank"})
    assert result["ok"] is True
    assert [r["value"] for r in result["data"]["results"]] == [20, 0]
    assert result["data"]["results"][1]["rank"] == 0
    short = factor_run({"action": "compute", "factor_name": "momentum", "symbols": ["600519"], "returns": {"600519": [0.01]}})
    assert short["status"] == "insufficient_data"
    complete = factor_run({"action": "compute", "factor_name": "momentum", "symbols": ["600519"], "returns": {"600519": [0.01] * 20}})
    assert complete["data"]["results"][0]["value"] == pytest.approx(1.01 ** 20 - 1)
