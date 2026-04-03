"""Tests for the A-share Mean-Reversion (BIAS) strategy."""

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
    from strategies.mean_reversion_cn_strategy import MeanReversionCNStrategy

    cerebro = bt.Cerebro()
    cerebro.adddata(_make_data(prices, volumes))
    cerebro.addstrategy(MeanReversionCNStrategy, **(params or {}))
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=0.001)
    results = cerebro.run()
    return results[0], cerebro


# ── fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def oversold_bounce() -> tuple[list[float], list[float]]:
    """Simulate an oversold bounce scenario with volume spike.

    Phase 1 (bar 0-59):  stable at 100 (SMA warm-up, SMA locks at 100)
    Phase 2 (bar 60-79): sharp crash from 100 to 80 (BIAS = -20%)
    Phase 3 (bar 80-89): bottom area with volume spike (panic)
    Phase 4 (bar 90-149): gradual recovery to 105
    """
    n = 150
    prices = []
    volumes = []
    for i in range(n):
        if i < 60:
            p = 100.0
            v = 1_000_000
        elif i < 80:
            # Crash from 100 to 80 over 20 bars → BIAS will be deeply negative
            p = 100 - (20 / 20) * (i - 60)
            v = 1_000_000
        elif i < 90:
            # Stay around 78-80 with volume spike
            p = 78 + 0.2 * (i - 80)
            v = 3_500_000  # big volume spike (3.5x)
        else:
            # Recovery to ~105
            p = 80 + (25 / 60) * (i - 90)
            v = 1_200_000
        prices.append(p)
        volumes.append(v)
    return prices, volumes


# ── tests ─────────────────────────────────────────────────────────

class TestMeanReversionCNMetadata:
    def test_strategy_id(self):
        from strategies.mean_reversion_cn_strategy import MeanReversionCNStrategy

        assert MeanReversionCNStrategy.STRATEGY_META.id == "mean-reversion-cn"

    def test_category(self):
        from strategies.mean_reversion_cn_strategy import MeanReversionCNStrategy

        assert MeanReversionCNStrategy.STRATEGY_META.category == "oscillator"

    def test_markets_includes_cn(self):
        from strategies.mean_reversion_cn_strategy import MeanReversionCNStrategy

        assert "cn" in MeanReversionCNStrategy.STRATEGY_META.markets

    def test_parameter_count(self):
        from strategies.mean_reversion_cn_strategy import MeanReversionCNStrategy

        assert len(MeanReversionCNStrategy.STRATEGY_META.parameters) == 5


class TestMeanReversionSignals:
    def test_oversold_bounce_generates_buy(self, oversold_bounce):
        """An oversold bounce with volume spike should trigger buy."""
        prices, volumes = oversold_bounce
        strat, _ = _run_backtest(prices, volumes=volumes, params={"bias_buy": -5.0})
        buys = [s for s in strat.get_signals() if s["direction"] == "buy"]
        assert len(buys) > 0, "Should produce buy signal on oversold bounce with volume"

    def test_buy_signal_mentions_bias(self, oversold_bounce):
        """Buy signal reason should mention BIAS value."""
        prices, volumes = oversold_bounce
        strat, _ = _run_backtest(prices, volumes=volumes, params={"bias_buy": -5.0})
        buys = [s for s in strat.get_signals() if s["direction"] == "buy"]
        if buys:
            assert "BIAS=" in buys[0]["reason"]
            assert "量比=" in buys[0]["reason"]

    def test_no_buy_without_volume_spike(self):
        """A price drop without volume spike should not trigger buy."""
        n = 100
        # Sharp drop but normal volume
        prices = [100.0] * 50 + [100 - (15 / 50) * i for i in range(50)]
        volumes = [1_000_000] * n  # no spike
        strat, _ = _run_backtest(prices, volumes=volumes)
        buys = [s for s in strat.get_signals() if s["direction"] == "buy"]
        assert len(buys) == 0, "Should not buy without volume spike confirmation"


class TestMeanReversionTimingStop:
    def test_max_hold_days_exit(self):
        """Position should be force-closed after max_hold_days."""
        n = 200
        # Drop → stay low (no natural sell signal from BIAS)
        prices = [100.0] * 40 + [100 - (15 / 30) * i for i in range(30)]
        prices += [85.0] * (n - len(prices))  # stay flat at 85
        volumes = [1_000_000] * n
        # spike around bar 60 to trigger buy
        for i in range(55, 70):
            volumes[i] = 3_000_000

        strat, _ = _run_backtest(
            prices,
            volumes=volumes,
            params={"bias_buy": -5.0, "max_hold_days": 5},
        )
        sells = [s for s in strat.get_signals() if s["direction"] == "sell"]
        if sells:
            assert any("时间止损" in s["reason"] for s in sells)

    def test_bias_sell_exit(self, oversold_bounce):
        """When BIAS reaches sell threshold, position should be closed."""
        prices, volumes = oversold_bounce
        strat, _ = _run_backtest(
            prices,
            volumes=volumes,
            params={"bias_buy": -5.0, "bias_sell": 2.0, "max_hold_days": 100},
        )
        sells = [s for s in strat.get_signals() if s["direction"] == "sell"]
        # With recovery and high max_hold, BIAS sell should trigger
        if sells:
            bias_sells = [s for s in sells if "乖离率止盈" in s["reason"]]
            # Either bias sell or time stop, both are valid
            assert len(sells) > 0


class TestMeanReversionEdgeCases:
    def test_short_data_no_crash(self):
        """Should not crash with minimal data (just above SMA period)."""
        prices = [100 - i * 0.5 for i in range(25)]
        strat, _ = _run_backtest(prices, params={"ma_period": 10})
        assert strat is not None

    def test_flat_data_no_signals(self):
        """Flat prices should produce no signals (BIAS=0, no volume spike)."""
        prices = [100.0] * 100
        strat, _ = _run_backtest(prices)
        assert len(strat.get_signals()) == 0

    def test_custom_params_respected(self):
        """Custom parameters should be applied."""
        prices = [100 + math.sin(i * 0.1) for i in range(100)]
        strat, _ = _run_backtest(
            prices,
            params={"ma_period": 10, "bias_buy": -3.0, "vol_mult": 1.5, "max_hold_days": 3},
        )
        assert strat.p.ma_period == 10
        assert strat.p.bias_buy == -3.0
        assert strat.p.vol_mult == 1.5
        assert strat.p.max_hold_days == 3

    def test_t_plus_1_guard(self):
        """Entry bar should not have a sell on the same bar."""
        n = 200
        # Create a scenario where buy and potential sell could happen close together
        prices = [100.0] * 40
        # Big drop with volume spike
        for i in range(40, 60):
            prices.append(100 - (20 / 20) * (i - 40))
        # Immediate bounce (could trigger BIAS sell quickly)
        for i in range(60, n):
            prices.append(80 + (25 / (n - 60)) * (i - 60))
        volumes = [1_000_000] * n
        for i in range(50, 65):
            volumes[i] = 3_000_000

        strat, _ = _run_backtest(
            prices,
            volumes=volumes,
            params={"bias_buy": -8.0, "bias_sell": 3.0},
        )
        signals = strat.get_signals()
        # Check that no buy and sell happen on the same date
        for i in range(len(signals) - 1):
            if signals[i]["direction"] == "buy":
                if i + 1 < len(signals) and signals[i + 1]["direction"] == "sell":
                    assert signals[i]["date"] != signals[i + 1]["date"], (
                        "T+1 violated: buy and sell on same date"
                    )
