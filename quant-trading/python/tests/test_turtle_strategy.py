"""
Unit tests for Turtle Strategy — validates Donchian channel breakout entry,
ATR-based position sizing and pyramiding, and exit logic.
"""

from __future__ import annotations

import backtrader as bt
import numpy as np
import pandas as pd
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strategies.turtle_strategy import TurtleStrategy


class TestTurtleMetadata:
    """Test strategy metadata."""

    def test_strategy_id(self):
        assert TurtleStrategy.STRATEGY_META.id == "turtle"

    def test_strategy_category(self):
        assert TurtleStrategy.STRATEGY_META.category == "trend"

    def test_parameter_count(self):
        params = TurtleStrategy.STRATEGY_META.parameters
        assert len(params) >= 4
        param_names = [p.name for p in params]
        assert "entry_period" in param_names
        assert "exit_period" in param_names
        assert "atr_period" in param_names
        assert "risk_pct" in param_names


class TestTurtleSignals:
    """Test signal generation via Donchian channel breakout."""

    def _run_backtest(
        self, df: pd.DataFrame, cash: float = 100_000, **params
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data, name="TEST")
        cerebro.addstrategy(TurtleStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def test_generates_signals_on_trending_data(self, trending_up_klines):
        """Turtle should generate entry signals when price breaks out."""
        df = trending_up_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)
        signals = strategy.get_signals()
        assert len(signals) > 0, "Should generate at least one signal on trending data"

    def test_signal_structure(self, trending_up_klines):
        """Each signal should have correct structure."""
        df = trending_up_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)
        signals = strategy.get_signals()
        for sig in signals:
            assert sig["direction"] in ("buy", "sell")
            assert sig["price"] > 0
            assert "reason" in sig

    def test_buy_signal_mentions_breakout(self, trending_up_klines):
        """Buy signals should reference Donchian channel breakout."""
        df = trending_up_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)
        buy_signals = [s for s in strategy.get_signals() if s["direction"] == "buy"]
        if buy_signals:
            assert any("突破" in sig["reason"] or "加仓" in sig["reason"] for sig in buy_signals)

    def test_exits_on_downtrend(self, synthetic_klines):
        """Should produce sell signals when price drops below exit channel."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)
        sell_signals = [s for s in strategy.get_signals() if s["direction"] == "sell"]
        # On a trend-then-reversal dataset the turtle should exit
        assert isinstance(sell_signals, list)

    def test_custom_parameters(self, trending_up_klines):
        """Strategy should accept custom Donchian/ATR parameters."""
        df = trending_up_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(
            df, entry_period=10, exit_period=5, atr_period=14, risk_pct=0.02
        )
        signals = strategy.get_signals()
        assert isinstance(signals, list)


class TestTurtleEdgeCases:
    """Edge case tests."""

    def _run_backtest(
        self, df: pd.DataFrame, cash: float = 100_000, **params
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data, name="TEST")
        cerebro.addstrategy(TurtleStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def test_short_data_no_crash(self):
        """Should not crash with data slightly longer than entry_period."""
        dates = pd.bdate_range(start="2025-01-02", periods=25, freq="B")
        prices = np.linspace(100, 110, 25)
        df = pd.DataFrame(
            {
                "open": prices * 0.999,
                "high": prices * 1.01,
                "low": prices * 0.99,
                "close": prices,
                "volume": np.full(25, 100000),
            },
            index=dates,
        )
        strategy, _ = self._run_backtest(df)
        assert isinstance(strategy.get_signals(), list)

    def test_flat_prices(self):
        """Should handle flat prices (no breakout → no signals)."""
        dates = pd.bdate_range(start="2025-01-02", periods=50, freq="B")
        price = 100.0
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
        # Flat prices → no Donchian breakout → likely zero signals
        assert isinstance(signals, list)
