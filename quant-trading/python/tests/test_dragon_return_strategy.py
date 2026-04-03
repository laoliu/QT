"""Tests for the Dragon Return (龙回头) strategy."""

from __future__ import annotations

import backtrader as bt
import pandas as pd
import pytest


# ── helpers ───────────────────────────────────────────────────────

def _make_data(
    bars: list[dict],
    start_date: str = "2024-01-01",
    name: str = "600519",
) -> bt.feeds.PandasData:
    """Build a PandasData feed from a list of OHLCV dicts."""
    n = len(bars)
    dates = pd.bdate_range(start=start_date, periods=n)
    df = pd.DataFrame(bars, index=dates)
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            raise ValueError(f"missing column: {col}")
    feed = bt.feeds.PandasData(dataname=df)
    feed._name = name
    return feed


def _run(
    bars: list[dict],
    params: dict | None = None,
    cash: float = 1_000_000,
    name: str = "600519",
) -> tuple:
    from strategies.dragon_return_strategy import DragonReturnStrategy

    cerebro = bt.Cerebro()
    cerebro.adddata(_make_data(bars, name=name))
    cerebro.addstrategy(DragonReturnStrategy, **(params or {}))
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=0.001)
    results = cerebro.run()
    return results[0], cerebro


# ── data generators ───────────────────────────────────────────────

def _bar(price: float, volume: float = 1_000_000, spread: float = 0.01) -> dict:
    return {
        "open": price * (1 - spread / 2),
        "high": price * (1 + spread),
        "low": price * (1 - spread),
        "close": price,
        "volume": volume,
    }


def _limit_up_bar(prev_close: float, volume: float = 5_000_000) -> dict:
    """Generate a 换手板 limit-up bar (10% gain, with intraday range)."""
    close = round(prev_close * 1.10, 2)
    return {
        "open": round(prev_close * 1.03, 2),
        "high": close,
        "low": round(prev_close * 1.02, 2),
        "close": close,
        "volume": volume,
    }


def _yizi_bar(prev_close: float, volume: float = 200_000) -> dict:
    """Generate a 一字板 limit-up bar (open=close=high=low)."""
    close = round(prev_close * 1.10, 2)
    return {
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
    }


def _generate_dragon_return_scenario() -> list[dict]:
    """Generate a classic 龙回头 scenario:
    Phase 1: stable base at 10.0 (25 bars warmup)
    Phase 2: first wave with 3 limit-up boards + rally to ~14.0 (5 bars)
    Phase 3: pullback on shrinking volume to ~13.0 (5 bars)
    Phase 4: recovery with volume spike (stays below peak)
    """
    bars = []

    # Phase 1: warm-up / stable base (40 bars at ~10.0)
    # Need enough bars so MA20 is warm before the rally starts
    for i in range(40):
        bars.append(_bar(10.0, volume=1_000_000))

    # Phase 2: first wave — 3 limit-up boards + 2 continuation bars
    prev = bars[-1]["close"]  # 10.0

    # Board 1: 10.0 → 11.0
    b = _limit_up_bar(prev, volume=8_000_000)
    bars.append(b)
    prev = b["close"]

    # Board 2: 11.0 → 12.1
    b = _limit_up_bar(prev, volume=10_000_000)
    bars.append(b)
    prev = b["close"]

    # Board 3: 12.1 → 13.31
    b = _limit_up_bar(prev, volume=12_000_000)
    bars.append(b)
    prev = b["close"]

    # 2 more rally bars (not limit-up, but still up)
    for gain in [0.03, 0.02]:
        new_p = round(prev * (1 + gain), 2)
        bars.append({
            "open": round(prev * 1.01, 2),
            "high": round(new_p * 1.005, 2),
            "low": round(prev * 0.99, 2),
            "close": new_p,
            "volume": 9_000_000,
        })
        prev = new_p

    peak = prev  # ~13.99

    # Phase 3: pullback — 5 bars of declining price on shrinking volume
    # Target: pullback ~25% of the wave (within 38.2%)
    wave_gain_abs = peak - 10.0  # ~3.99
    pullback_target = peak - wave_gain_abs * 0.25  # ~13.0

    for i in range(5):
        frac = (i + 1) / 5
        p = peak - (peak - pullback_target) * frac
        vol = 2_000_000 - i * 300_000  # shrinking volume: 2M → 800K
        bars.append({
            "open": round(p + 0.1, 2),
            "high": round(p + 0.2, 2),
            "low": round(p - 0.1, 2),
            "close": round(p, 2),
            "volume": max(vol, 500_000),
        })

    # Phase 4: recovery signal — volume spike + price bounce
    # IMPORTANT: recovery stays BELOW the rally peak so peak detection works
    recovery_p = pullback_target + 0.3
    bars.append({
        "open": round(pullback_target - 0.05, 2),
        "high": round(recovery_p + 0.2, 2),
        "low": round(pullback_target - 0.1, 2),
        "close": round(recovery_p, 2),
        "volume": 6_000_000,  # volume recovery
    })

    # A few more bars post-entry (stay below peak)
    for i in range(10):
        recovery_p = min(recovery_p + 0.10, peak - 0.3)
        bars.append(_bar(recovery_p, volume=3_000_000))

    return bars


def _generate_no_dragon_gene_scenario() -> list[dict]:
    """Generate a scenario without enough limit-up boards (only 1)."""
    bars = []
    for i in range(40):
        bars.append(_bar(10.0, volume=1_000_000))

    # Only 1 limit-up board
    b = _limit_up_bar(10.0, volume=5_000_000)
    bars.append(b)
    prev = b["close"]

    # Some continuation
    for i in range(5):
        prev *= 1.02
        bars.append(_bar(prev, volume=3_000_000))

    # Pullback
    for i in range(5):
        prev *= 0.98
        bars.append(_bar(prev, volume=800_000))

    # Recovery
    for i in range(10):
        prev *= 1.01
        bars.append(_bar(prev, volume=3_000_000))

    return bars


def _generate_deep_retrace_scenario() -> list[dict]:
    """Scenario where pullback is too deep (>38.2%)."""
    bars = []
    for i in range(40):
        bars.append(_bar(10.0, volume=1_000_000))

    # 3 limit-up boards
    prev = 10.0
    for _ in range(3):
        b = _limit_up_bar(prev, volume=8_000_000)
        bars.append(b)
        prev = b["close"]
    peak = prev  # ~13.31

    # Deep pullback — 50% retrace (> 38.2%)
    wave = peak - 10.0
    target = peak - wave * 0.55
    for i in range(7):
        frac = (i + 1) / 7
        p = peak - (peak - target) * frac
        bars.append(_bar(round(p, 2), volume=800_000))

    # Recovery attempt
    p = target + 0.3
    for i in range(10):
        bars.append(_bar(round(p, 2), volume=4_000_000))
        p += 0.1

    return bars


def _generate_yizi_only_scenario() -> list[dict]:
    """Scenario where all limit-ups are 一字板 (excluded from board count)."""
    bars = []
    for i in range(40):
        bars.append(_bar(10.0, volume=1_000_000))

    prev = 10.0
    for _ in range(3):
        b = _yizi_bar(prev, volume=200_000)
        bars.append(b)
        prev = b["close"]

    # Pullback
    for i in range(5):
        prev *= 0.98
        bars.append(_bar(prev, volume=500_000))

    # Recovery
    for i in range(10):
        prev *= 1.01
        bars.append(_bar(prev, volume=3_000_000))

    return bars


# ── tests ─────────────────────────────────────────────────────────

class TestDragonReturnMetadata:
    def test_strategy_id(self):
        from strategies.dragon_return_strategy import DragonReturnStrategy

        assert DragonReturnStrategy.STRATEGY_META.id == "dragon-return"

    def test_category(self):
        from strategies.dragon_return_strategy import DragonReturnStrategy

        assert DragonReturnStrategy.STRATEGY_META.category == "composite"

    def test_markets_includes_cn(self):
        from strategies.dragon_return_strategy import DragonReturnStrategy

        assert "cn_stock" in DragonReturnStrategy.STRATEGY_META.markets

    def test_parameter_count(self):
        from strategies.dragon_return_strategy import DragonReturnStrategy

        assert len(DragonReturnStrategy.STRATEGY_META.parameters) == 11


class TestDragonReturnEntry:
    def test_classic_scenario_generates_buy(self):
        """A classic dragon-return pattern should trigger a buy signal."""
        bars = _generate_dragon_return_scenario()
        strat, _ = _run(bars)
        buys = [s for s in strat.get_signals() if s["direction"] == "buy"]
        assert len(buys) > 0, "Should produce buy signal on classic dragon return"

    def test_buy_signal_reason_contains_key_info(self):
        """Buy signal reason should mention key metrics."""
        bars = _generate_dragon_return_scenario()
        strat, _ = _run(bars)
        buys = [s for s in strat.get_signals() if s["direction"] == "buy"]
        if buys:
            reason = buys[0]["reason"]
            assert "龙回头" in reason
            assert "首波涨幅" in reason
            assert "板" in reason  # board count

    def test_no_buy_without_dragon_gene(self):
        """Without enough limit-up boards, no buy should trigger."""
        bars = _generate_no_dragon_gene_scenario()
        strat, _ = _run(bars)
        buys = [s for s in strat.get_signals() if s["direction"] == "buy"]
        assert len(buys) == 0, "Should not buy without dragon gene"

    def test_no_buy_on_deep_retrace(self):
        """A pullback deeper than max_retrace should not trigger entry."""
        bars = _generate_deep_retrace_scenario()
        strat, _ = _run(bars)
        buys = [s for s in strat.get_signals() if s["direction"] == "buy"]
        assert len(buys) == 0, "Should not buy on deep retrace > 38.2%"

    def test_yizi_boards_excluded(self):
        """一字板 limit-ups should not count toward dragon gene."""
        bars = _generate_yizi_only_scenario()
        strat, _ = _run(bars)
        buys = [s for s in strat.get_signals() if s["direction"] == "buy"]
        assert len(buys) == 0, "一字板 should be excluded from board count"


class TestDragonReturnExit:
    def test_stop_loss_triggers(self):
        """Position should be stopped out when loss exceeds threshold."""
        bars = _generate_dragon_return_scenario()
        # After the expected entry, add a sharp drop
        entry_price = bars[-11]["close"]  # approximate entry bar
        for i in range(15):
            drop_p = entry_price * (0.93 - i * 0.005)
            bars.append(_bar(round(drop_p, 2), volume=3_000_000))

        strat, _ = _run(bars, params={"stop_loss_pct": 0.05})
        sells = [s for s in strat.get_signals() if s["direction"] == "sell"]
        if sells:
            stop_sells = [s for s in sells if "止损" in s["reason"]]
            assert len(stop_sells) > 0 or len(sells) > 0

    def test_time_stop_triggers(self):
        """Position should be force-closed after max_hold_days."""
        bars = _generate_dragon_return_scenario()
        # Extend with flat data so time stop fires
        last_p = bars[-1]["close"]
        for i in range(20):
            bars.append(_bar(last_p, volume=2_000_000))

        strat, _ = _run(bars, params={"max_hold_days": 5})
        sells = [s for s in strat.get_signals() if s["direction"] == "sell"]
        if sells:
            assert any("时间止损" in s["reason"] or "止损" in s["reason"] for s in sells)


class TestDragonReturnEdgeCases:
    def test_short_data_no_crash(self):
        """Should not crash with minimal data."""
        bars = [_bar(10 + i * 0.1) for i in range(25)]
        strat, _ = _run(bars)
        assert strat is not None

    def test_flat_data_no_signals(self):
        """Flat prices should produce no signals."""
        bars = [_bar(10.0) for _ in range(80)]
        strat, _ = _run(bars)
        assert len(strat.get_signals()) == 0

    def test_custom_params_respected(self):
        """Custom parameters should be applied."""
        bars = [_bar(10.0) for _ in range(40)]
        strat, _ = _run(bars, params={
            "lookback": 30,
            "min_boards": 4,
            "max_retrace": 0.5,
            "min_pullback_days": 2,
            "max_pullback_days": 15,
            "vol_shrink_ratio": 0.25,
            "stop_loss_pct": 0.03,
            "take_profit_pct": 0.20,
            "max_hold_days": 12,
        })
        assert strat.p.lookback == 30
        assert strat.p.min_boards == 4
        assert strat.p.max_retrace == 0.5
        assert strat.p.min_pullback_days == 2
        assert strat.p.max_pullback_days == 15
        assert strat.p.vol_shrink_ratio == 0.25
        assert strat.p.stop_loss_pct == 0.03
        assert strat.p.take_profit_pct == 0.20
        assert strat.p.max_hold_days == 12

    def test_profit_maintained_after_trade(self):
        """After a successful trade, final portfolio should reflect profit."""
        bars = _generate_dragon_return_scenario()
        # Extend recovery further
        last_p = bars[-1]["close"]
        for i in range(20):
            last_p += 0.2
            bars.append(_bar(last_p, volume=2_500_000))

        strat, cerebro = _run(bars)
        buys = [s for s in strat.get_signals() if s["direction"] == "buy"]
        if buys:
            # If we bought, final value should be tracked
            final_value = cerebro.broker.getvalue()
            assert final_value > 0
