"""
Unit tests for Pair Trading Strategy — validates cointegration-based
spread z-score entry/exit with dual data feeds.
"""

from __future__ import annotations

import backtrader as bt
import numpy as np
import pandas as pd
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strategies.pair_trading_strategy import PairTradingStrategy


def _make_cointegrated_pair(
    days: int = 200, seed: int = 42
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate a pair of cointegrated price series.

    Series B tracks series A with a fixed ratio + mean-reverting noise.
    """
    rng = np.random.default_rng(seed)
    # Random walk for asset A
    returns_a = rng.normal(0.0005, 0.015, days)
    prices_a = 100.0 * np.exp(np.cumsum(returns_a))

    # Asset B = 1.5 * A + mean-reverting spread
    spread = np.zeros(days)
    for i in range(1, days):
        spread[i] = 0.8 * spread[i - 1] + rng.normal(0, 0.5)
    prices_b = 1.5 * prices_a + spread + 50

    dates = pd.bdate_range(start="2025-01-02", periods=days, freq="B")

    def _to_df(prices, dates):
        closes = np.array(prices)
        return pd.DataFrame(
            {
                "open": closes * 0.999,
                "high": closes * 1.005,
                "low": closes * 0.995,
                "close": closes,
                "volume": np.full(len(closes), 100000.0),
            },
            index=dates,
        )

    return _to_df(prices_a, dates), _to_df(prices_b, dates)


class TestPairTradingMetadata:
    """Test strategy metadata."""

    def test_strategy_id(self):
        assert PairTradingStrategy.STRATEGY_META.id == "pair-trading"

    def test_strategy_category(self):
        assert PairTradingStrategy.STRATEGY_META.category == "arbitrage"

    def test_parameter_count(self):
        params = PairTradingStrategy.STRATEGY_META.parameters
        assert len(params) >= 3
        param_names = [p.name for p in params]
        assert "lookback" in param_names
        assert "z_entry" in param_names
        assert "z_exit" in param_names


class TestPairTradingSignals:
    """Test signal generation with cointegrated pair."""

    def _run_backtest(
        self,
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        cash: float = 200_000,
        **params,
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        cerebro = bt.Cerebro()
        data_a = bt.feeds.PandasData(dataname=df_a)
        data_b = bt.feeds.PandasData(dataname=df_b)
        cerebro.adddata(data_a, name="A")
        cerebro.adddata(data_b, name="B")
        cerebro.addstrategy(PairTradingStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def test_generates_signals_on_cointegrated_pair(self):
        """Should generate spread entry/exit signals."""
        pytest.importorskip("statsmodels")
        df_a, df_b = _make_cointegrated_pair(days=200, seed=42)
        strategy, _ = self._run_backtest(df_a, df_b)
        signals = strategy.get_signals()
        assert len(signals) > 0, "Should generate at least one signal on cointegrated pair"

    def test_signal_structure(self):
        """Signals should have required fields."""
        pytest.importorskip("statsmodels")
        df_a, df_b = _make_cointegrated_pair(days=200, seed=42)
        strategy, _ = self._run_backtest(df_a, df_b)
        signals = strategy.get_signals()
        for sig in signals:
            assert sig["direction"] in ("buy", "sell")
            assert sig["price"] > 0
            assert "reason" in sig

    def test_custom_parameters(self):
        """Strategy should accept custom z-score thresholds."""
        pytest.importorskip("statsmodels")
        df_a, df_b = _make_cointegrated_pair(days=200, seed=42)
        strategy, _ = self._run_backtest(
            df_a, df_b, lookback=30, z_entry=1.5, z_exit=0.3
        )
        signals = strategy.get_signals()
        assert isinstance(signals, list)


class TestPairTradingEdgeCases:
    """Edge case tests."""

    def _run_backtest(
        self,
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        cash: float = 200_000,
        **params,
    ) -> tuple[bt.Strategy, bt.Cerebro]:
        cerebro = bt.Cerebro()
        data_a = bt.feeds.PandasData(dataname=df_a)
        data_b = bt.feeds.PandasData(dataname=df_b)
        cerebro.adddata(data_a, name="A")
        cerebro.adddata(data_b, name="B")
        cerebro.addstrategy(PairTradingStrategy, **params)
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)
        results = cerebro.run()
        return results[0], cerebro

    def test_independent_series_no_crash(self):
        """Two independent random walks should not crash (no cointegration)."""
        rng = np.random.default_rng(99)
        days = 100
        dates = pd.bdate_range(start="2025-01-02", periods=days, freq="B")

        def _random_df():
            returns = rng.normal(0, 0.02, days)
            closes = 100.0 * np.exp(np.cumsum(returns))
            return pd.DataFrame(
                {
                    "open": closes * 0.999,
                    "high": closes * 1.005,
                    "low": closes * 0.995,
                    "close": closes,
                    "volume": np.full(days, 100000.0),
                },
                index=dates,
            )

        strategy, _ = self._run_backtest(_random_df(), _random_df())
        assert isinstance(strategy.get_signals(), list)

    def test_short_data(self):
        """Short data (< lookback) should not crash."""
        df_a, df_b = _make_cointegrated_pair(days=30, seed=42)
        strategy, _ = self._run_backtest(df_a, df_b, lookback=60)
        assert isinstance(strategy.get_signals(), list)

    def test_missing_cointegration_test_fails_closed(self, monkeypatch):
        """A missing/failed test must never be interpreted as cointegration."""
        stattools = pytest.importorskip("statsmodels.tsa.stattools")

        def unavailable(*args, **kwargs):
            raise RuntimeError("cointegration test unavailable")

        monkeypatch.setattr(stattools, "coint", unavailable)
        df_a, df_b = _make_cointegrated_pair(days=200, seed=42)
        strategy, _ = self._run_backtest(df_a, df_b)
        assert strategy.get_signals() == []
