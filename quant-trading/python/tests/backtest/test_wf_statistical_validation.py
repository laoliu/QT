"""WF 统计诊断测试。"""
from __future__ import annotations

from aipha.backtest.wf_statistical_validation import (
    summarize_wf_statistical_validation,
)


def _make_fold_returns() -> list[list[float]]:
    return [
        [0.010, 0.003, -0.002, 0.004, 0.006, -0.001, 0.002, 0.005, 0.001, 0.004],
        [0.006, 0.002, -0.001, 0.003, 0.005, 0.000, 0.001, 0.004, 0.002, 0.003],
        [0.008, 0.001, -0.003, 0.005, 0.004, -0.001, 0.003, 0.004, 0.002, 0.006],
        [0.007, 0.002, -0.002, 0.004, 0.003, 0.001, 0.002, 0.005, 0.001, 0.004],
    ]


class TestSummarizeWfStatisticalValidation:
    def test_empty_input(self):
        result = summarize_wf_statistical_validation([], n_trials=3)
        assert result["n_obs"] == 0
        assert result["pbo"] is None
        assert result["passes_statistical_gate"] is False
        assert result["reason"] == "no_valid_fold_returns"

    def test_short_input_returns_reason(self):
        result = summarize_wf_statistical_validation([[0.01, -0.01]], n_trials=2)
        assert result["n_obs"] == 2
        assert result["dsr"] is None
        assert result["reason"] == "insufficient_oos_observations"

    def test_populates_statistical_fields(self):
        result = summarize_wf_statistical_validation(_make_fold_returns(), n_trials=6)
        assert result["n_obs"] == 40
        assert result["n_folds"] == 4
        assert result["n_trials"] == 6
        assert result["observed_sharpe"] is not None
        assert result["dsr"] is not None
        assert result["hlz_adjusted_t"] is not None
        assert result["pbo"] is not None
        assert result["pbo_method"] in {"fold_matrix", "single_series"}
        assert result["negative_fold_ratio"] is not None
