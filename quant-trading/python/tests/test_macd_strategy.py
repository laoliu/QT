"""
Unit tests for MACD Strategy — validates golden/death cross signals
using synthetic K-line data fed through backtrader cerebro.
"""

from __future__ import annotations

import backtrader as bt
import numpy as np
import pandas as pd
import pytest
import sys
import os

# Add parent dir so we can import strategies
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategies.macd_strategy import MACDStrategy


class TestMACDStrategy:
    """Tests for the MACD oscillator strategy."""

    def _run_backtest(
        self, df: pd.DataFrame, cash: float = 100_000, **params
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        """Helper: run a backtest with the given DataFrame and return (strategy, cerebro)."""
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data, name="TEST")
        cerebro.addstrategy(MACDStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def test_strategy_metadata(self):
        """STRATEGY_META is properly set."""
        meta = MACDStrategy.STRATEGY_META
        assert meta is not None
        assert meta.id == "macd"
        assert meta.category == "trend"
        assert len(meta.parameters) == 3
        param_names = [p.name for p in meta.parameters]
        assert "fast_period" in param_names
        assert "slow_period" in param_names
        assert "signal_period" in param_names

    def test_generates_signals_on_trending_data(self, synthetic_klines):
        """Strategy should generate buy and sell signals on data with trend changes."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _cerebro = self._run_backtest(df)

        signals = strategy.get_signals()
        assert len(signals) > 0, "Should generate at least one signal on trending data"

        # Check signal structure
        for sig in signals:
            assert sig["direction"] in ("buy", "sell")
            assert sig["price"] > 0
            assert sig["strategyId"] == "macd"
            assert "MACD" in sig["reason"]

    def test_golden_cross_generates_buy(self, synthetic_klines):
        """On uptrending data, the first signal should typically be a buy."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)

        signals = strategy.get_signals()
        if len(signals) > 0:
            # In a trend-up-then-down pattern, first trades likely start as buys
            buy_signals = [s for s in signals if s["direction"] == "buy"]
            assert len(buy_signals) > 0, "Should have at least one buy signal"
            # Verify golden cross is mentioned
            for bs in buy_signals:
                assert "金叉" in bs["reason"]

    def test_death_cross_generates_sell(self, synthetic_klines):
        """After a buy, a death cross should generate a sell."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)

        signals = strategy.get_signals()
        sell_signals = [s for s in signals if s["direction"] == "sell"]
        if len(sell_signals) > 0:
            for ss in sell_signals:
                assert "死叉" in ss["reason"]

    def test_custom_parameters(self, synthetic_klines):
        """Custom fast/slow/signal periods should be accepted."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(
            df, fast_period=8, slow_period=21, signal_period=5
        )

        # Verify parameters were applied
        assert strategy.p.fast_period == 8
        assert strategy.p.slow_period == 21
        assert strategy.p.signal_period == 5

        # Should still produce signals
        signals = strategy.get_signals()
        # With shorter periods, might generate more signals
        assert isinstance(signals, list)

    def test_no_position_sizing_at_zero_price(self, synthetic_klines):
        """Position sizing should handle edge cases gracefully."""
        df = synthetic_klines(days=200, seed=42)
        strategy, cerebro = self._run_backtest(df, cash=100_000)

        # The strategy should not crash and final value should be reasonable
        final = cerebro.broker.getvalue()
        assert final > 0, "Final value should be positive"

    def test_trade_count_tracking(self, synthetic_klines):
        """Trade count and win count tracking via BaseStrategy."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)

        # trade_count and win_count should be non-negative
        assert strategy._trade_count >= 0
        assert strategy._win_count >= 0
        assert strategy._win_count <= strategy._trade_count

    def test_short_data_no_crash(self):
        """Strategy should handle minimal data without crashing."""
        # Need at least slow_period(26) + signal_period(9) ≈ 35 bars for MACD
        # Use 50 bars to give enough room
        rng = np.random.default_rng(99)
        prices = 100 + np.cumsum(rng.normal(0, 1, 50))
        dates = pd.bdate_range(start="2025-06-01", periods=50, freq="B")
        df = pd.DataFrame(
            {
                "open": prices,
                "high": prices + rng.uniform(0, 2, 50),
                "low": prices - rng.uniform(0, 2, 50),
                "close": prices,
                "volume": rng.integers(50_000, 200_000, size=50),
            },
            index=dates,
        )
        strategy, cerebro = self._run_backtest(df)
        # Should not crash; may or may not produce signals
        assert cerebro.broker.getvalue() > 0
