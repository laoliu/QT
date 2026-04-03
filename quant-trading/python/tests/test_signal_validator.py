"""Tests for signal_validator engine — forward return and accuracy calculations (no network required)."""

from __future__ import annotations

import sys
import os

# Ensure engines and strategies are importable
_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)


import pandas as pd
import numpy as np

from engines.signal_validator import (
    _calculate_forward_returns,
    _compute_summary,
)


def _make_price_df(start: str = "2023-01-01", days: int = 30, base: float = 100.0):
    """Create a simple price DataFrame for testing."""
    dates = pd.bdate_range(start, periods=days)
    # Simple upward trend with noise
    np.random.seed(42)
    close = base + np.cumsum(np.random.randn(days) * 0.5) + np.arange(days) * 0.2
    df = pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.random.randint(1000, 10000, days),
        },
        index=dates,
    )
    return df


class TestForwardReturns:
    """Test forward return calculation for signals."""

    def test_buy_signal_returns(self):
        df = _make_price_df(days=20)
        price_data = {"SYM": df}
        signals = [
            {
                "date": str(df.index[5].date()),
                "direction": "buy",
                "price": float(df.iloc[5]["close"]),
                "symbol": "SYM",
            }
        ]

        enriched = _calculate_forward_returns(signals, price_data, [1, 3, 5])
        assert len(enriched) == 1
        sig = enriched[0]
        assert "1d" in sig["returns"]
        assert "3d" in sig["returns"]
        assert "5d" in sig["returns"]
        # correct should be bool for buy signals
        assert isinstance(sig["correct"]["1d"], bool)

    def test_sell_signal_correctness(self):
        # Create a downward-trending df so sell signals should be "correct"
        df = _make_price_df(days=20, base=100)
        # Override: make prices strictly decrease
        for i in range(len(df)):
            df.iloc[i, df.columns.get_loc("close")] = 100.0 - i * 1.0

        price_data = {"SYM": df}
        signals = [
            {
                "date": str(df.index[3].date()),
                "direction": "sell",
                "price": float(df.iloc[3]["close"]),
                "symbol": "SYM",
            }
        ]

        enriched = _calculate_forward_returns(signals, price_data, [1, 5])
        sig = enriched[0]
        # Sell signal on downtrend → correct (price went down)
        assert sig["correct"]["1d"] is True
        assert sig["correct"]["5d"] is True

    def test_missing_symbol(self):
        df = _make_price_df(days=10)
        price_data = {"SYM": df}
        signals = [
            {
                "date": str(df.index[0].date()),
                "direction": "buy",
                "price": 100.0,
                "symbol": "UNKNOWN",
            }
        ]

        # With single symbol, should fall back to the only available key
        enriched = _calculate_forward_returns(signals, price_data, [1])
        # Should still produce results using the single symbol's data
        assert len(enriched) == 1

    def test_horizon_beyond_data(self):
        df = _make_price_df(days=5)
        price_data = {"SYM": df}
        signals = [
            {
                "date": str(df.index[3].date()),
                "direction": "buy",
                "price": float(df.iloc[3]["close"]),
                "symbol": "SYM",
            }
        ]

        enriched = _calculate_forward_returns(signals, price_data, [1, 10])
        sig = enriched[0]
        assert sig["returns"]["1d"] is not None
        # 10d horizon goes beyond data
        assert sig["returns"]["10d"] is None

    def test_multiple_signals(self):
        df = _make_price_df(days=20)
        price_data = {"SYM": df}
        signals = [
            {
                "date": str(df.index[2].date()),
                "direction": "buy",
                "price": float(df.iloc[2]["close"]),
                "symbol": "SYM",
            },
            {
                "date": str(df.index[8].date()),
                "direction": "sell",
                "price": float(df.iloc[8]["close"]),
                "symbol": "SYM",
            },
        ]

        enriched = _calculate_forward_returns(signals, price_data, [1, 3])
        assert len(enriched) == 2
        assert "returns" in enriched[0]
        assert "returns" in enriched[1]


class TestComputeSummary:
    """Test summary statistics computation."""

    def test_basic_accuracy(self):
        enriched = [
            {"direction": "buy", "correct": {"1d": True, "3d": True}, "returns": {"1d": 1.0, "3d": 2.0}},
            {"direction": "buy", "correct": {"1d": False, "3d": True}, "returns": {"1d": -0.5, "3d": 0.5}},
            {"direction": "sell", "correct": {"1d": True, "3d": False}, "returns": {"1d": -1.0, "3d": 1.0}},
        ]

        accuracy, avg_return = _compute_summary(enriched, [1, 3])

        # Buy accuracy 1d: 1/2 = 50%, 3d: 2/2 = 100%
        assert accuracy["1d"]["buy"] == 50.0
        assert accuracy["3d"]["buy"] == 100.0

        # Sell accuracy 1d: 1/1 = 100%, 3d: 0/1 = 0%
        assert accuracy["1d"]["sell"] == 100.0
        assert accuracy["3d"]["sell"] == 0.0

    def test_avg_returns(self):
        enriched = [
            {"direction": "buy", "correct": {"1d": True}, "returns": {"1d": 2.0}},
            {"direction": "buy", "correct": {"1d": True}, "returns": {"1d": 4.0}},
        ]

        _, avg_return = _compute_summary(enriched, [1])
        assert avg_return["1d"]["buy"] == 3.0  # (2 + 4) / 2

    def test_no_signals(self):
        accuracy, avg_return = _compute_summary([], [1, 3])
        assert accuracy["1d"]["buy"] is None
        assert accuracy["1d"]["sell"] is None
        assert avg_return["1d"]["buy"] is None

    def test_only_buy_signals(self):
        enriched = [
            {"direction": "buy", "correct": {"1d": True}, "returns": {"1d": 1.5}},
        ]
        accuracy, avg_return = _compute_summary(enriched, [1])
        assert accuracy["1d"]["buy"] == 100.0
        assert accuracy["1d"]["sell"] is None

    def test_none_values_skipped(self):
        enriched = [
            {"direction": "buy", "correct": {"1d": None}, "returns": {"1d": None}},
            {"direction": "buy", "correct": {"1d": True}, "returns": {"1d": 1.0}},
        ]
        accuracy, avg_return = _compute_summary(enriched, [1])
        # Only 1 non-None buy correct → 100%
        assert accuracy["1d"]["buy"] == 100.0
        assert avg_return["1d"]["buy"] == 1.0
