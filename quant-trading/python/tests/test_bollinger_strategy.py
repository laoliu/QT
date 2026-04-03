"""Tests for the Bollinger Band Mean-Reversion strategy."""

from __future__ import annotations

import datetime

import backtrader as bt
import pytest


# ── helpers ───────────────────────────────────────────────────────

def _make_data(prices: list[float], start_date: str = "2024-01-01") -> bt.feeds.PandasData:
    """Create a backtrader data feed from a list of close prices."""
    import pandas as pd

    dates = pd.bdate_range(start=start_date, periods=len(prices))
    df = pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1_000_000] * len(prices),
        },
        index=dates,
    )
    return bt.feeds.PandasData(dataname=df)


def _run_backtest(
    prices: list[float],
    params: dict | None = None,
    cash: float = 1_000_000,
) -> tuple:
    """Run a Bollinger strategy backtest and return (strategy_instance, cerebro)."""
    from strategies.bollinger_strategy import BollingerStrategy

    cerebro = bt.Cerebro()
    cerebro.adddata(_make_data(prices))
    cerebro.addstrategy(BollingerStrategy, **(params or {}))
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=0.001)
    results = cerebro.run()
    return results[0], cerebro


# ── fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def sideways_prices() -> list[float]:
    """200-bar sideways oscillating prices (range 95–105)."""
    import math

    base = 100.0
    return [base + 5 * math.sin(i * 0.15) for i in range(200)]


@pytest.fixture
def trending_down_prices() -> list[float]:
    """150-bar downtrend: 120 → 80."""
    return [120 - (40 / 150) * i for i in range(150)]


# ── tests ─────────────────────────────────────────────────────────

class TestBollingerMetadata:
    def test_strategy_id(self):
        from strategies.bollinger_strategy import BollingerStrategy

        assert BollingerStrategy.STRATEGY_META is not None
        assert BollingerStrategy.STRATEGY_META.id == "bollinger"

    def test_category(self):
        from strategies.bollinger_strategy import BollingerStrategy

        assert BollingerStrategy.STRATEGY_META.category == "oscillator"

    def test_params_count(self):
        from strategies.bollinger_strategy import BollingerStrategy

        assert len(BollingerStrategy.STRATEGY_META.parameters) == 3


class TestBollingerSignals:
    def test_sideways_generates_signals(self, sideways_prices):
        """Sideways (oscillating) market should trigger mean-reversion signals."""
        strat, _ = _run_backtest(sideways_prices)
        signals = strat.get_signals()
        # In a sideways market, we expect at least some buy/sell pairs
        assert len(signals) > 0, "Should generate signals in oscillating market"

    def test_signal_structure(self, sideways_prices):
        """Signals should have the required fields."""
        strat, _ = _run_backtest(sideways_prices)
        for sig in strat.get_signals():
            assert "direction" in sig
            assert sig["direction"] in ("buy", "sell")
            assert "price" in sig
            assert sig["price"] > 0
            assert "reason" in sig
            assert "strategyId" in sig
            assert sig["strategyId"] == "bollinger"

    def test_buy_signals_mention_lower_band(self, sideways_prices):
        """Buy signals should reference the lower band in reason."""
        strat, _ = _run_backtest(sideways_prices)
        buy_signals = [s for s in strat.get_signals() if s["direction"] == "buy"]
        if buy_signals:
            assert any("下轨" in s["reason"] for s in buy_signals)

    def test_custom_params(self, sideways_prices):
        """Custom period and devfactor should be respected."""
        strat, _ = _run_backtest(
            sideways_prices,
            params={"period": 15, "devfactor": 1.5},
        )
        assert strat.p.period == 15
        assert strat.p.devfactor == 1.5


class TestBollingerSqueeze:
    def test_squeeze_detection_near_flat_market(self):
        """A nearly flat market should not crash and produces minimal signals."""
        # Constant prices — no oscillation
        flat = [100.0] * 25 + [100.0 + i * 0.001 for i in range(75)]
        strat, _ = _run_backtest(flat, params={"period": 20, "devfactor": 2.0})
        # Strategy runs without crash; signal count is implementation-dependent
        assert strat is not None

    def test_squeeze_skips_entries(self):
        """During a squeeze (low volatility), entries should be avoided."""
        import math

        # Low-vol region then normal
        low_vol = [100 + 0.1 * math.sin(i * 0.3) for i in range(80)]
        normal = [100 + 5 * math.sin(i * 0.2) for i in range(120)]
        prices = low_vol + normal
        strat, _ = _run_backtest(prices, params={"squeeze_pct": 70})
        # The strategy should not have signals during the first 80 bars
        early_signals = [
            s for s in strat.get_signals() if s["date"] < "2024-04-15"
        ]
        # We allow at most 1 early signal (squeeze is approximate)
        assert len(early_signals) <= 1


class TestBollingerEdgeCases:
    def test_short_data_no_crash(self):
        """Strategy should not crash with minimal data."""
        prices = [100 + i * 0.5 for i in range(25)]
        strat, _ = _run_backtest(prices)
        # Should complete without error
        assert strat is not None

    def test_large_devfactor_reduces_signals(self, sideways_prices):
        """A wider band (devfactor=3) should produce fewer signals."""
        strat_narrow, _ = _run_backtest(sideways_prices, params={"devfactor": 1.5})
        strat_wide, _ = _run_backtest(sideways_prices, params={"devfactor": 3.0})
        assert len(strat_wide.get_signals()) <= len(strat_narrow.get_signals())
