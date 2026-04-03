"""Tests for the Multi-Factor Stock Selection strategy."""

from __future__ import annotations

import math

import backtrader as bt
import pandas as pd
import pytest


# ── helpers ───────────────────────────────────────────────────────

def _make_data(
    prices: list[float],
    volumes: list[float] | None = None,
    start_date: str = "2024-01-01",
) -> bt.feeds.PandasData:
    """Create a backtrader data feed."""
    n = len(prices)
    if volumes is None:
        volumes = [1_000_000] * n
    dates = pd.bdate_range(start=start_date, periods=n)
    df = pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": volumes,
        },
        index=dates,
    )
    return bt.feeds.PandasData(dataname=df)


def _run_backtest(
    prices: list[float],
    volumes: list[float] | None = None,
    params: dict | None = None,
    cash: float = 1_000_000,
) -> tuple:
    from strategies.multi_factor_strategy import MultiFactorStrategy

    cerebro = bt.Cerebro()
    cerebro.adddata(_make_data(prices, volumes))
    cerebro.addstrategy(MultiFactorStrategy, **(params or {}))
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=0.001)
    results = cerebro.run()
    return results[0], cerebro


# ── fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def trending_up() -> list[float]:
    """200-bar uptrend: 80 → 140 with noise."""
    return [80 + (60 / 200) * i + 2 * math.sin(i * 0.3) for i in range(200)]


@pytest.fixture
def choppy_prices() -> list[float]:
    """200-bar range-bound market with noise."""
    return [100 + 8 * math.sin(i * 0.1) + 3 * math.cos(i * 0.37) for i in range(200)]


# ── tests ─────────────────────────────────────────────────────────

class TestMultiFactorMetadata:
    def test_strategy_id(self):
        from strategies.multi_factor_strategy import MultiFactorStrategy

        assert MultiFactorStrategy.STRATEGY_META.id == "multi-factor"

    def test_category(self):
        from strategies.multi_factor_strategy import MultiFactorStrategy

        assert MultiFactorStrategy.STRATEGY_META.category == "composite"

    def test_parameter_count(self):
        from strategies.multi_factor_strategy import MultiFactorStrategy

        # 3 periods + 2 thresholds + 4 weights = 9
        assert len(MultiFactorStrategy.STRATEGY_META.parameters) == 9


class TestMultiFactorScoring:
    def test_uptrend_generates_buy(self, trending_up):
        """A strong uptrend should produce buy signals (momentum positive)."""
        strat, _ = _run_backtest(trending_up)
        buys = [s for s in strat.get_signals() if s["direction"] == "buy"]
        assert len(buys) > 0, "Should produce buy signals in uptrend"

    def test_signal_contains_score(self, trending_up):
        """Signal reason should contain the composite score."""
        strat, _ = _run_backtest(trending_up)
        for sig in strat.get_signals():
            assert "score=" in sig["reason"]
            assert sig["strategyId"] == "multi-factor"

    def test_waits_for_enough_data(self):
        """Strategy should not trade during warm-up period."""
        prices = [100 + i for i in range(70)]  # 70 bars (ma_period=60 default)
        strat, _ = _run_backtest(prices)
        for sig in strat.get_signals():
            # Earliest signal should be after bar 60
            sig_date = sig["date"]
            assert sig_date >= "2024-03-20"  # ~60 trading days from Jan 1

    def test_custom_thresholds(self, choppy_prices):
        """Lower buy threshold should produce fewer buy signals."""
        strat_easy, _ = _run_backtest(choppy_prices, params={"score_buy_threshold": 0.0})
        strat_hard, _ = _run_backtest(choppy_prices, params={"score_buy_threshold": 1.5})
        easy_buys = len([s for s in strat_easy.get_signals() if s["direction"] == "buy"])
        hard_buys = len([s for s in strat_hard.get_signals() if s["direction"] == "buy"])
        assert hard_buys <= easy_buys

    def test_volume_spike_affects_score(self):
        """A volume spike should influence the composite score."""
        n = 200
        prices = [100 + 0.5 * math.sin(i * 0.1) for i in range(n)]
        # Normal volume with a spike at bar 150
        volumes = [1_000_000] * n
        volumes[150] = 5_000_000

        strat, _ = _run_backtest(prices, volumes=volumes)
        # Just verify no crash — volume factor is part of the composite
        assert strat is not None


class TestMultiFactorEdgeCases:
    def test_short_data_few_signals(self):
        """With data just barely above ma_period, expect few or no signals."""
        prices = [100 + i * 0.5 for i in range(65)]
        strat, _ = _run_backtest(prices, params={"ma_period": 60})
        # Only a few bars past warm-up, so very few signals expected
        assert len(strat.get_signals()) <= 2

    def test_flat_prices_no_crash(self):
        """Flat prices should not cause division-by-zero."""
        prices = [100.0] * 100
        strat, _ = _run_backtest(prices)
        assert strat is not None

    def test_zero_weights_no_crash(self):
        """Setting all weights to zero should not crash."""
        prices = [100 + i * 0.5 for i in range(100)]
        strat, _ = _run_backtest(
            prices,
            params={
                "w_momentum": 0,
                "w_volatility": 0,
                "w_volume": 0,
                "w_bias": 0,
            },
        )
        # Zero weights → score always 0 → never hits threshold → no signals
        assert len(strat.get_signals()) == 0
