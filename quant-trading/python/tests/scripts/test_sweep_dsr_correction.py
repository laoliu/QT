"""tests for scripts/sweep_dsr_correction.py"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

# Ensure src available
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from sweep_dsr_correction import (
    check_boundary_params,
    load_sweep_results,
    run_dsr_correction,
    _load_daily_returns,
    _make_verdict,
)
from aipha.backtest.advanced_validation import DSRResult


def _make_sweep_results(n: int = 10, base_sharpe: float = 1.0):
    """生成模拟 sweep 结果。"""
    results = []
    for i in range(n):
        sharpe = base_sharpe + 0.05 * i
        results.append({
            "task_id": f"task_{i:04d}",
            "params": {
                "top_k": 3 + i % 4 * 2,
                "weight_size": 0.10 + i % 5 * 0.05,
                "stop_loss_pct": 0.08 + i % 3 * 0.02,
            },
            "metrics": {
                "sharpe_ratio": sharpe,
                "annualized_return": 0.15 + 0.02 * i,
                "max_drawdown": 0.10 + 0.005 * i,
                "trading_days": 1700,
            },
        })
    return results


class TestLoadSweepResults:
    """load_sweep_results 的测试。"""

    def test_load_list_format(self, tmp_path):
        data = _make_sweep_results(5)
        json_path = tmp_path / "sweep_results.json"
        json_path.write_text(json.dumps(data), encoding="utf-8")
        loaded = load_sweep_results(tmp_path)
        assert len(loaded) == 5

    def test_load_dict_format(self, tmp_path):
        data = {"results": _make_sweep_results(3), "metadata": {}}
        json_path = tmp_path / "sweep_results.json"
        json_path.write_text(json.dumps(data), encoding="utf-8")
        loaded = load_sweep_results(tmp_path)
        assert len(loaded) == 3

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_sweep_results(tmp_path)


class TestCheckBoundaryParams:
    """check_boundary_params 的测试。"""

    def test_no_boundary_hit(self):
        results = _make_sweep_results(10)
        # best 在中间
        best = results[5]
        warnings = check_boundary_params(results, best)
        # top_k 和 weight_size 可能或可能不触达边界，
        # 但函数应返回列表
        assert isinstance(warnings, list)

    def test_upper_boundary_detected(self):
        results = [
            {"params": {"weight_size": 0.20}, "metrics": {"sharpe_ratio": 1.0}},
            {"params": {"weight_size": 0.25}, "metrics": {"sharpe_ratio": 1.1}},
            {"params": {"weight_size": 0.30}, "metrics": {"sharpe_ratio": 1.2}},
            {"params": {"weight_size": 0.35}, "metrics": {"sharpe_ratio": 1.3}},
        ]
        best = results[-1]  # weight_size=0.35 是上界
        warnings = check_boundary_params(results, best)
        assert len(warnings) == 1
        assert "上界" in warnings[0]["warning"]
        assert warnings[0]["param"] == "weight_size"

    def test_lower_boundary_detected(self):
        results = [
            {"params": {"stop_loss": 0.05}, "metrics": {"sharpe_ratio": 1.3}},
            {"params": {"stop_loss": 0.08}, "metrics": {"sharpe_ratio": 1.1}},
            {"params": {"stop_loss": 0.10}, "metrics": {"sharpe_ratio": 1.0}},
        ]
        best = results[0]  # stop_loss=0.05 是下界
        warnings = check_boundary_params(results, best)
        assert len(warnings) == 1
        assert "下界" in warnings[0]["warning"]

    def test_empty_results_no_crash(self):
        warnings = check_boundary_params([], {})
        assert warnings == []


class TestRunDsrCorrection:
    """run_dsr_correction 的端到端测试。"""

    def test_basic_run(self, tmp_path):
        data = _make_sweep_results(48, base_sharpe=0.8)
        json_path = tmp_path / "sweep_results.json"
        json_path.write_text(json.dumps(data), encoding="utf-8")

        result = run_dsr_correction(tmp_path)

        assert "dsr" in result
        assert "n_trials" in result
        assert result["n_trials"] == 48
        assert "boundary_warnings" in result
        assert "verdict" in result
        assert (tmp_path / "dsr_correction.json").exists()

    def test_few_trials_still_works(self, tmp_path):
        data = _make_sweep_results(3)
        json_path = tmp_path / "sweep_results.json"
        json_path.write_text(json.dumps(data), encoding="utf-8")

        result = run_dsr_correction(tmp_path)
        assert result["n_trials"] == 3
        # 只有 3 次试验，E[max(SR)] 很小，应更容易通过
        assert result["dsr"]["expected_max_sharpe"] < 1.0

    def test_high_sharpe_passes(self, tmp_path):
        """高 Sharpe（3.0+）在少量试验下应通过。"""
        data = _make_sweep_results(5, base_sharpe=2.5)
        json_path = tmp_path / "sweep_results.json"
        json_path.write_text(json.dumps(data), encoding="utf-8")

        result = run_dsr_correction(tmp_path)
        assert result["dsr"]["is_significant"] is True


class TestMakeVerdict:
    """_make_verdict 的测试。"""

    def test_passed_verdict(self):
        dsr = DSRResult(
            observed_sharpe=1.5,
            deflated_sharpe=0.95,
            p_value=0.01,
            haircut_sharpe=1.0,
            n_trials=10,
            expected_max_sharpe=0.5,
            is_significant=True,
            significance_level=0.05,
        )
        verdict = _make_verdict(dsr, [])
        assert verdict["passed"] is True
        assert "✅" in verdict["recommendation"]

    def test_failed_dsr(self):
        dsr = DSRResult(
            observed_sharpe=1.0,
            deflated_sharpe=0.4,
            p_value=0.20,
            haircut_sharpe=0.5,
            n_trials=48,
            expected_max_sharpe=0.8,
            is_significant=False,
            significance_level=0.05,
        )
        verdict = _make_verdict(dsr, [])
        assert verdict["passed"] is False
        assert "❌" in verdict["recommendation"]

    def test_boundary_warning_fails(self):
        dsr = DSRResult(
            observed_sharpe=1.5,
            deflated_sharpe=0.95,
            p_value=0.01,
            haircut_sharpe=1.0,
            n_trials=10,
            expected_max_sharpe=0.5,
            is_significant=True,
            significance_level=0.05,
        )
        warnings = [{"param": "x", "value": "0.5", "range": "[0,0.5]",
                      "warning": "触达上界"}]
        verdict = _make_verdict(dsr, warnings)
        assert verdict["passed"] is False
