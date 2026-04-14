"""tests/scripts/test_s4_tracking_error.py — S4 跟踪误差检查测试."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ / "scripts") not in sys.path:
    sys.path.insert(0, str(_PROJ / "scripts"))

import s4_tracking_error


class TestComputeTrackingError:
    def test_identical_nav_zero_te(self):
        dates = pd.date_range("2026-01-01", periods=60, freq="B")
        nav = pd.Series([100 * (1 + 0.001) ** i for i in range(60)], index=dates)
        r = s4_tracking_error.compute_tracking_error(nav, nav)
        assert r["te_annual"] < 1e-10
        assert r["daily_deviation_pct"] == 0.0
        assert r["cum_return_deviation"] < 1e-10

    def test_different_nav_positive_te(self):
        import numpy as np
        np.random.seed(42)
        dates = pd.date_range("2026-01-01", periods=100, freq="B")
        nav1 = pd.Series(100 * np.cumprod(1 + np.random.normal(0.001, 0.01, 100)), index=dates)
        nav2 = pd.Series(100 * np.cumprod(1 + np.random.normal(0.001, 0.01, 100)), index=dates)
        r = s4_tracking_error.compute_tracking_error(nav1, nav2)
        assert r["te_annual"] > 0
        assert r["n_days"] > 50

    def test_insufficient_data(self):
        dates = pd.date_range("2026-01-01", periods=3, freq="B")
        nav = pd.Series([100, 101, 102], index=dates)
        r = s4_tracking_error.compute_tracking_error(nav, nav)
        assert r["te_annual"] == float("inf")


class TestCheckS4Gate:
    def test_pass_gate(self):
        metrics = {
            "te_annual": 0.02,
            "daily_deviation_pct": 0.01,
            "cum_return_deviation": 0.03,
            "n_days": 60,
        }
        gate = s4_tracking_error.check_s4_gate(metrics)
        assert gate["gate_pass"] is True
        assert all(c["pass"] for c in gate["checks"])

    def test_fail_gate_high_te(self):
        metrics = {
            "te_annual": 0.10,
            "daily_deviation_pct": 0.01,
            "cum_return_deviation": 0.03,
            "n_days": 60,
        }
        gate = s4_tracking_error.check_s4_gate(metrics)
        assert gate["gate_pass"] is False

    def test_fail_gate_high_deviation(self):
        metrics = {
            "te_annual": 0.02,
            "daily_deviation_pct": 0.20,
            "cum_return_deviation": 0.03,
            "n_days": 60,
        }
        gate = s4_tracking_error.check_s4_gate(metrics)
        assert gate["gate_pass"] is False
