"""
Unit tests for RSI Strategy — validates overbought/oversold signals.
"""

from __future__ import annotations

import backtrader as bt
import numpy as np
import pandas as pd
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strategies.rsi_strategy import RSIStrategy


class TestRSIMetadata:
    """Test strategy metadata."""

    def test_strategy_id(self):
        assert RSIStrategy.STRATEGY_META.id == "rsi"

    def test_strategy_category(self):
        assert RSIStrategy.STRATEGY_META.category == "oscillator"

    def test_parameter_count(self):
        params = RSIStrategy.STRATEGY_META.parameters
        assert len(params) == 3
        param_names = [p.name for p in params]
        assert "period" in param_names
        assert "overbought" in param_names
        assert "oversold" in param_names


class TestRSISignals:
    """Test signal generation."""

    def _run_backtest(
        self, df: pd.DataFrame, cash: float = 100_000, **params
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data, name="TEST")
        cerebro.addstrategy(RSIStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def test_generates_signals_on_trending_data(self, synthetic_klines):
        """RSI should generate buy/sell signals on data with trend changes."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)
        signals = strategy.get_signals()
        assert len(signals) > 0, "Should generate at least one signal"

    def test_signal_structure(self, synthetic_klines):
        """Each signal should have required fields."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)
        signals = strategy.get_signals()
        for sig in signals:
            assert sig["direction"] in ("buy", "sell")
            assert sig["price"] > 0
            assert "reason" in sig
            assert "RSI" in sig["reason"]

    def test_buy_signal_mentions_oversold(self, synthetic_klines):
        """Buy signals should mention oversold threshold."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)
        buy_signals = [s for s in strategy.get_signals() if s["direction"] == "buy"]
        for sig in buy_signals:
            assert "超卖" in sig["reason"]

    def test_custom_parameters(self, synthetic_klines):
        """Strategy should accept custom RSI thresholds."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df, period=10, overbought=80, oversold=20)
        # Should run without error; may or may not produce signals
        signals = strategy.get_signals()
        assert isinstance(signals, list)


class TestRSIEdgeCases:
    """Edge case tests."""

    def _run_backtest(
        self, df: pd.DataFrame, cash: float = 100_000, **params
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data, name="TEST")
        cerebro.addstrategy(RSIStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def test_short_data_no_crash(self):
        """Strategy should not crash with data longer than RSI period."""
        dates = pd.bdate_range(start="2025-01-02", periods=30, freq="B")
        rng = np.random.default_rng(42)
        prices = 100.0 + np.cumsum(rng.normal(0, 0.5, 30))
        df = pd.DataFrame(
            {
                "open": prices * 0.999,
                "high": prices * 1.01,
                "low": prices * 0.99,
                "close": prices,
                "volume": np.full(30, 100000),
            },
            index=dates,
        )
        strategy, _ = self._run_backtest(df)
        assert isinstance(strategy.get_signals(), list)

    def test_flat_prices(self):
        """Strategy should handle flat (constant) prices gracefully."""
        n = 50
        dates = pd.bdate_range(start="2025-01-02", periods=n, freq="B")
        rng = np.random.default_rng(123)
        # Add tiny noise so RSI indicator doesn't get NaN from zero variance
        prices = 50.0 + rng.normal(0, 0.01, n)
        df = pd.DataFrame(
            {
                "open": prices * 0.999,
                "high": prices * 1.001,
                "low": prices * 0.999,
                "close": prices,
                "volume": np.full(n, 100000),
            },
            index=dates,
        )
        strategy, _ = self._run_backtest(df)
        assert isinstance(strategy.get_signals(), list)
