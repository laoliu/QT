"""
Unit tests for KDJ Strategy — validates golden/death cross signals
in oversold/overbought zones using the custom KDJ indicator.
"""

from __future__ import annotations

import backtrader as bt
import numpy as np
import pandas as pd
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strategies.kdj_strategy import KDJStrategy


class TestKDJMetadata:
    """Test strategy metadata."""

    def test_strategy_id(self):
        assert KDJStrategy.STRATEGY_META.id == "kdj"

    def test_strategy_category(self):
        assert KDJStrategy.STRATEGY_META.category == "oscillator"

    def test_parameter_count(self):
        params = KDJStrategy.STRATEGY_META.parameters
        assert len(params) == 5
        param_names = [p.name for p in params]
        assert "k_period" in param_names
        assert "d_period" in param_names
        assert "overbought" in param_names
        assert "oversold" in param_names


class TestKDJSignals:
    """Test signal generation with KDJ indicator."""

    def _run_backtest(
        self, df: pd.DataFrame, cash: float = 100_000, **params
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data, name="TEST")
        cerebro.addstrategy(KDJStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def test_generates_signals_on_trending_data(self, synthetic_klines):
        """KDJ should generate signals when market oscillates."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)
        signals = strategy.get_signals()
        assert len(signals) > 0, "Should generate at least one signal"

    def test_signal_structure(self, synthetic_klines):
        """Each signal should have correct structure."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)
        signals = strategy.get_signals()
        for sig in signals:
            assert sig["direction"] in ("buy", "sell")
            assert sig["price"] > 0
            assert "reason" in sig

    def test_buy_signals_mention_kdj(self, synthetic_klines):
        """Buy signals should mention KDJ indicator values."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)
        buy_signals = [s for s in strategy.get_signals() if s["direction"] == "buy"]
        for sig in buy_signals:
            assert "K=" in sig["reason"] or "J=" in sig["reason"]

    def test_custom_parameters(self, synthetic_klines):
        """Strategy should work with custom KDJ parameters."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df, k_period=14, d_period=5, overbought=90, oversold=10)
        signals = strategy.get_signals()
        assert isinstance(signals, list)


class TestKDJEdgeCases:
    """Edge case tests."""

    def _run_backtest(
        self, df: pd.DataFrame, cash: float = 100_000, **params
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data, name="TEST")
        cerebro.addstrategy(KDJStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def test_short_data_no_crash(self):
        """Should not crash with very short data."""
        dates = pd.bdate_range(start="2025-01-02", periods=15, freq="B")
        prices = np.linspace(100, 102, 15)
        df = pd.DataFrame(
            {
                "open": prices * 0.999,
                "high": prices * 1.01,
                "low": prices * 0.99,
                "close": prices,
                "volume": np.full(15, 100000),
            },
            index=dates,
        )
        strategy, _ = self._run_backtest(df)
        assert isinstance(strategy.get_signals(), list)

    def test_flat_prices(self):
        """Should handle flat prices (K,D around 50, J around 50)."""
        dates = pd.bdate_range(start="2025-01-02", periods=50, freq="B")
        price = 50.0
        df = pd.DataFrame(
            {
                "open": np.full(50, price),
                "high": np.full(50, price * 1.001),
                "low": np.full(50, price * 0.999),
                "close": np.full(50, price),
                "volume": np.full(50, 100000),
            },
            index=dates,
        )
        strategy, _ = self._run_backtest(df)
        signals = strategy.get_signals()
        # Flat prices → KDJ hovers around 50 → no buy/sell triggers
        assert isinstance(signals, list)
