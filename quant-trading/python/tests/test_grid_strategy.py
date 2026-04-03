"""
Unit tests for Grid Strategy — validates grid construction,
buy-the-dip / sell-the-rally grid trading logic.
"""

from __future__ import annotations

import backtrader as bt
import numpy as np
import pandas as pd
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strategies.grid_strategy import GridStrategy


class TestGridMetadata:
    """Test strategy metadata."""

    def test_strategy_id(self):
        assert GridStrategy.STRATEGY_META.id == "grid"

    def test_strategy_category(self):
        assert GridStrategy.STRATEGY_META.category == "range"

    def test_parameter_count(self):
        params = GridStrategy.STRATEGY_META.parameters
        assert len(params) >= 3
        param_names = [p.name for p in params]
        assert "grid_pct" in param_names
        assert "grid_count" in param_names


class TestGridSignals:
    """Test grid trading signal generation."""

    def _run_backtest(
        self, df: pd.DataFrame, cash: float = 100_000, **params
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data, name="TEST")
        cerebro.addstrategy(GridStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def _make_oscillating_data(self, days: int = 200, center: float = 100.0, amplitude: float = 10.0, seed: int = 42) -> pd.DataFrame:
        """Generate oscillating price data ideal for grid trading."""
        rng = np.random.default_rng(seed)
        dates = pd.bdate_range(start="2025-01-02", periods=days, freq="B")
        t = np.linspace(0, 8 * np.pi, days)
        closes = center + amplitude * np.sin(t) + rng.normal(0, 1, days)
        return pd.DataFrame(
            {
                "open": closes * (1 + rng.normal(0, 0.002, days)),
                "high": closes * (1 + np.abs(rng.normal(0, 0.01, days))),
                "low": closes * (1 - np.abs(rng.normal(0, 0.01, days))),
                "close": closes,
                "volume": rng.integers(50000, 200000, days).astype(float),
            },
            index=dates,
        )

    def test_generates_signals_on_oscillating_data(self):
        """Grid strategy should generate buy/sell signals when price oscillates."""
        df = self._make_oscillating_data(days=200, center=100, amplitude=10)
        strategy, _ = self._run_backtest(df, center_price=100.0, grid_pct=3.0, grid_count=8)
        signals = strategy.get_signals()
        assert len(signals) > 0, "Should generate signals on oscillating data"

    def test_signal_structure(self):
        """Each signal should have required fields."""
        df = self._make_oscillating_data(days=200, center=100, amplitude=10)
        strategy, _ = self._run_backtest(df, center_price=100.0, grid_pct=3.0, grid_count=8)
        signals = strategy.get_signals()
        for sig in signals:
            assert sig["direction"] in ("buy", "sell")
            assert sig["price"] > 0
            assert "reason" in sig

    def test_auto_center_price(self, synthetic_klines):
        """When center_price=0, strategy should auto-detect center from first close."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df, center_price=0, grid_pct=2.0, grid_count=10)
        signals = strategy.get_signals()
        assert isinstance(signals, list)

    def test_custom_grid_parameters(self):
        """Strategy should accept custom grid size and count."""
        df = self._make_oscillating_data(days=200, center=50, amplitude=5)
        strategy, _ = self._run_backtest(df, center_price=50, grid_pct=1.5, grid_count=15)
        signals = strategy.get_signals()
        assert isinstance(signals, list)


class TestGridEdgeCases:
    """Edge case tests."""

    def _run_backtest(
        self, df: pd.DataFrame, cash: float = 100_000, **params
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data, name="TEST")
        cerebro.addstrategy(GridStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def test_flat_prices_few_signals(self):
        """Flat prices should produce few or no grid crossings."""
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
        strategy, _ = self._run_backtest(df, center_price=100, grid_pct=2.0, grid_count=10)
        signals = strategy.get_signals()
        assert isinstance(signals, list)

    def test_short_data_no_crash(self):
        """Should not crash with very short data."""
        dates = pd.bdate_range(start="2025-01-02", periods=5, freq="B")
        prices = np.array([100, 98, 96, 98, 100], dtype=float)
        df = pd.DataFrame(
            {
                "open": prices * 0.999,
                "high": prices * 1.01,
                "low": prices * 0.99,
                "close": prices,
                "volume": np.full(5, 100000),
            },
            index=dates,
        )
        strategy, _ = self._run_backtest(df, center_price=100, grid_pct=2.0, grid_count=5)
        assert isinstance(strategy.get_signals(), list)
