"""Tests for param_optimizer engine — grid/random search logic (no network/backtrader required)."""

from __future__ import annotations

import json
import sys
import os

# Ensure engines and strategies are importable
_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)


from engines.param_optimizer import (
    _generate_param_values,
    _generate_grid_combinations,
    _generate_random_combinations,
    _get_metric_value,
)


class TestGenerateParamValues:
    """Test parameter value generation from range specs."""

    def test_integer_range(self):
        values = _generate_param_values({"min": 5, "max": 20, "step": 5})
        assert values == [5, 10, 15, 20]

    def test_integer_range_step_1(self):
        values = _generate_param_values({"min": 1, "max": 5, "step": 1})
        assert values == [1, 2, 3, 4, 5]

    def test_float_range(self):
        values = _generate_param_values({"min": 0.0, "max": 1.0, "step": 0.25})
        assert values == [0.0, 0.25, 0.5, 0.75, 1.0]

    def test_explicit_values(self):
        values = _generate_param_values({"values": [3, 7, 14, 21]})
        assert values == [3, 7, 14, 21]

    def test_single_value(self):
        values = _generate_param_values({"min": 10, "max": 10, "step": 1})
        assert values == [10]


class TestGridCombinations:
    """Test grid (Cartesian product) generation."""

    def test_two_params(self):
        ranges = {
            "fast": {"min": 5, "max": 10, "step": 5},
            "slow": {"min": 20, "max": 30, "step": 10},
        }
        combos = _generate_grid_combinations(ranges)
        assert len(combos) == 4  # [5,10] x [20,30]
        assert {"fast": 5, "slow": 20} in combos
        assert {"fast": 10, "slow": 30} in combos

    def test_single_param(self):
        ranges = {"period": {"values": [10, 14, 20]}}
        combos = _generate_grid_combinations(ranges)
        assert len(combos) == 3
        assert all("period" in c for c in combos)

    def test_three_params(self):
        ranges = {
            "a": {"values": [1, 2]},
            "b": {"values": [3, 4]},
            "c": {"values": [5, 6]},
        }
        combos = _generate_grid_combinations(ranges)
        assert len(combos) == 8  # 2 x 2 x 2


class TestRandomCombinations:
    """Test random combination generation."""

    def test_generates_up_to_max_trials(self):
        ranges = {
            "fast": {"min": 5, "max": 50, "step": 1},
            "slow": {"min": 20, "max": 100, "step": 1},
        }
        combos = _generate_random_combinations(ranges, 10)
        assert len(combos) <= 10

    def test_respects_value_ranges(self):
        ranges = {"period": {"min": 10, "max": 20, "step": 5}}
        combos = _generate_random_combinations(ranges, 100)
        valid_values = {10, 15, 20}
        for combo in combos:
            assert combo["period"] in valid_values, f"Got {combo['period']}, expected one of {valid_values}"

    def test_deduplicates(self):
        # With very small range, should deduplicate
        ranges = {"x": {"values": [1, 2]}}
        combos = _generate_random_combinations(ranges, 100)
        assert len(combos) == 2  # Only 2 unique

    def test_explicit_values_random(self):
        ranges = {"mode": {"values": [10, 20, 30]}}
        combos = _generate_random_combinations(ranges, 5)
        for combo in combos:
            assert combo["mode"] in [10, 20, 30]


class TestGetMetricValue:
    """Test metric extraction and comparison logic."""

    def test_sharpe_ratio(self):
        result = {"sharpeRatio": 1.5, "totalReturn": 20}
        assert _get_metric_value(result, "sharpeRatio") == 1.5

    def test_total_return(self):
        result = {"sharpeRatio": 1.5, "totalReturn": 20}
        assert _get_metric_value(result, "totalReturn") == 20

    def test_max_drawdown_inverted(self):
        # maxDrawdown: lower is better → should be negated
        result = {"maxDrawdown": 15}
        assert _get_metric_value(result, "maxDrawdown") == -15

    def test_missing_metric(self):
        result = {}
        assert _get_metric_value(result, "sharpeRatio") == float("-inf")

    def test_win_rate(self):
        result = {"winRate": 65.0}
        assert _get_metric_value(result, "winRate") == 65.0
