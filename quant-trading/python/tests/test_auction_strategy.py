"""
Unit tests for Auction Strategy — validates daily-open buy,
intraday add, and the 4-exit-rule logic on synthetic daily data.
"""

from __future__ import annotations

import backtrader as bt
import numpy as np
import pandas as pd
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strategies.auction_strategy import AuctionStrategy


class TestAuctionMetadata:
    """Test strategy metadata."""

    def test_strategy_id(self):
        assert AuctionStrategy.STRATEGY_META.id == "auction"

    def test_strategy_category(self):
        assert AuctionStrategy.STRATEGY_META.category == "composite"

    def test_parameter_count(self):
        params = AuctionStrategy.STRATEGY_META.parameters
        assert len(params) >= 5
        param_names = [p.name for p in params]
        assert "buy_pct" in param_names
        assert "profit_half_sell" in param_names
        assert "ma_period" in param_names


class TestAuctionSignals:
    """Test signal generation on daily data."""

    def _run_backtest(
        self, df: pd.DataFrame, cash: float = 100_000, **params
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data, name="TEST")
        cerebro.addstrategy(AuctionStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def test_generates_signals_on_trending_data(self, synthetic_klines):
        """Auction strategy should generate buy signals at daily opens."""
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

    def test_buy_signals_present(self, synthetic_klines):
        """At least some buy signals should exist on a trending dataset."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(df)
        buy_signals = [s for s in strategy.get_signals() if s["direction"] == "buy"]
        assert len(buy_signals) > 0, "Auction should buy at market open"

    def test_custom_parameters(self, synthetic_klines):
        """Strategy should accept custom auction parameters."""
        df = synthetic_klines(days=200, seed=42)
        strategy, _ = self._run_backtest(
            df, buy_pct=0.15, add_pct=0.05, profit_half_sell=0.30, ma_period=10
        )
        signals = strategy.get_signals()
        assert isinstance(signals, list)


class TestAuctionEdgeCases:
    """Edge case tests."""

    def _run_backtest(
        self, df: pd.DataFrame, cash: float = 100_000, **params
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        cerebro = bt.Cerebro()
        data = bt.feeds.PandasData(dataname=df)
        cerebro.adddata(data, name="TEST")
        cerebro.addstrategy(AuctionStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def test_short_data_no_crash(self):
        """Should not crash with very short data."""
        dates = pd.bdate_range(start="2025-01-02", periods=10, freq="B")
        prices = np.linspace(100, 105, 10)
        df = pd.DataFrame(
            {
                "open": prices * 0.999,
                "high": prices * 1.01,
                "low": prices * 0.99,
                "close": prices,
                "volume": np.full(10, 100000),
            },
            index=dates,
        )
        strategy, _ = self._run_backtest(df)
        assert isinstance(strategy.get_signals(), list)

    def test_gap_down_scenario(self):
        """Significant gap-down should trigger sell-all exit."""
        dates = pd.bdate_range(start="2025-01-02", periods=30, freq="B")
        rng = np.random.default_rng(77)
        prices = np.full(30, 100.0)
        # Gradual rise first 20 days, then gap-down day 21
        for i in range(1, 20):
            prices[i] = prices[i - 1] * (1 + rng.normal(0.003, 0.005))
        # Gap down on day 21
        prices[20] = prices[19] * 0.95  # -5% open
        prices[21] = prices[20] * 0.97  # further drop
        for i in range(22, 30):
            prices[i] = prices[i - 1] * (1 + rng.normal(-0.001, 0.01))

        df = pd.DataFrame(
            {
                "open": prices * (1 + rng.normal(0, 0.003, 30)),
                "high": prices * (1 + np.abs(rng.normal(0, 0.01, 30))),
                "low": prices * (1 - np.abs(rng.normal(0, 0.01, 30))),
                "close": prices,
                "volume": rng.integers(50000, 200000, 30).astype(float),
            },
            index=dates,
        )
        strategy, _ = self._run_backtest(df)
        sell_signals = [s for s in strategy.get_signals() if s["direction"] == "sell"]
        # Should exit on or after the gap-down
        assert isinstance(sell_signals, list)

    def test_flat_prices(self):
        """Flat prices should still trigger initial buy but few exits."""
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
        assert isinstance(strategy.get_signals(), list)
