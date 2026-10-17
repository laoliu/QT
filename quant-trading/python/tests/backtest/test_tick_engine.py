from datetime import date, datetime, timedelta

import pytest

from aipha.backtest.tick_data_loader import _parse_tick_row
from aipha.backtest.tick_engine import TickAggregator, TickBacktestConfig, TickBacktestEngine, TickFeatureComputer
from aipha.domain.models import OrderIntent, Side, Tick
from aipha.strategy.base import TickStrategy


class RoundTrip(TickStrategy):
    def on_tick(self, context):
        qty = context.get_position("TEST")
        return [OrderIntent("TEST", Side.SELL if qty else Side.BUY, qty or 100)]


def _ticks():
    start = datetime(2026, 10, 12, 9, 30)
    return [Tick("TEST", start + timedelta(seconds=i), price=10, volume=(i + 1) * 100,
                 amount=(i + 1) * 1000) for i in range(3)]


def test_t_plus_one_holding_visible_but_not_sellable():
    ticks = _ticks()
    engine = TickBacktestEngine(TickBacktestConfig(slippage_bps=0), RoundTrip())
    result = engine.run({date(2026, 10, 12): {"TEST": ticks}})
    assert result.total_trades == 1  # no repeated buys while settlement is pending
    assert result.final_positions == {"TEST": 100}
    assert result.daily_snapshots[0].positions == {"TEST": 100}
    assert result.daily_snapshots[0].trade_count == 1
    assert result.final_nav == pytest.approx(1_000_000 - 5.01)


def test_settlement_and_fee_slippage_use_shared_models():
    ticks = _ticks()
    tomorrow = Tick("TEST", datetime(2026, 10, 13, 9, 30), price=10)
    result = TickBacktestEngine(TickBacktestConfig(slippage_bps=10), RoundTrip()).run({
        date(2026, 10, 12): {"TEST": ticks[:1]}, date(2026, 10, 13): {"TEST": [tomorrow]},
    })
    assert result.total_trades == 2
    assert result.final_positions == {}
    assert result.trades[0].price == pytest.approx(10.01)
    assert result.trades[1].price == pytest.approx(9.99)
    assert result.trades[1].fees.stamp_tax == pytest.approx(999 * .0005)
    assert result.final_cash == pytest.approx(1_000_000 - 2 - result.total_fees)


def test_insufficient_cash_never_overspends():
    result = TickBacktestEngine(TickBacktestConfig(initial_capital=1001, slippage_bps=0), RoundTrip()).run({
        date(2026, 10, 12): {"TEST": _ticks()},
    })
    assert result.total_trades == 0
    assert result.final_cash == 1001


def test_unimplemented_fill_mode_fails_explicitly():
    with pytest.raises(ValueError, match="fill_mode"):
        TickBacktestEngine(TickBacktestConfig(fill_mode="unknown"), RoundTrip())


def test_next_tick_fills_only_after_signal_and_expires_last_order():
    ticks = _ticks()
    ticks[1] = Tick("TEST", ticks[1].timestamp, price=12, volume=200, amount=2200)
    result = TickBacktestEngine(
        TickBacktestConfig(fill_mode="next_tick", slippage_bps=0, t_plus_n=0), RoundTrip(),
    ).run({date(2026, 10, 12): {"TEST": ticks}})
    assert [trade.price for trade in result.trades] == [12, 10]
    assert result.trades[0].tick_timestamp == ticks[1].timestamp
    assert result.expired_orders == 1
    assert result.daily_snapshots[0].trade_count == 2


def test_next_tick_does_not_use_equal_timestamp_or_another_symbol():
    class BuyOnce(TickStrategy):
        def __init__(self):
            self.sent = False

        def on_tick(self, context):
            if not self.sent:
                self.sent = True
                return [OrderIntent("TEST", Side.BUY, 100)]
            return []

    tick = _ticks()[0]
    other = Tick("OTHER", tick.timestamp + timedelta(seconds=1), price=99)
    result = TickBacktestEngine(
        TickBacktestConfig(fill_mode="next_tick", slippage_bps=0), BuyOnce(),
    ).run({date(2026, 10, 12): {"TEST": [tick, tick], "OTHER": [other]}})
    assert result.total_trades == 0
    assert result.expired_orders == 1


def test_next_tick_resting_limit_and_settlement():
    class LimitBuy(TickStrategy):
        def on_tick(self, context):
            if context.tick == _ticks()[0]:
                return [OrderIntent("TEST", Side.BUY, 100, limit_price=10)]
            return []

    ticks = _ticks()
    ticks[1] = Tick("TEST", ticks[1].timestamp, price=12)
    result = TickBacktestEngine(
        TickBacktestConfig(fill_mode="next_tick", slippage_bps=0), LimitBuy(),
    ).run({date(2026, 10, 12): {"TEST": ticks}})
    assert result.total_trades == 1
    assert result.trades[0].tick_timestamp == ticks[2].timestamp
    assert result.final_positions == {"TEST": 100}


def test_loader_parses_book_and_rejects_bad_price():
    row = {"time": "20261012 09:30:00", "lastPrice": 10, "volume": 100,
           "bidPrice": [9.99], "bidVol": [100], "askPrice": [10.01], "askVol": [200]}
    tick = _parse_tick_row("TEST", row)
    assert tick.bid_levels[0].price == 9.99
    assert tick.ask_levels[0].volume == 200
    assert _parse_tick_row("TEST", {**row, "lastPrice": -1}) is None
    assert _parse_tick_row("TEST", {**row, "lastPrice": float("nan")}) is None


def test_cumulative_ticks_are_differenced_and_reset_on_new_day():
    computer = TickFeatureComputer()
    aggregator = TickAggregator()
    ticks = _ticks()
    for tick in ticks:
        features = computer.update(tick)
        aggregator.add_tick(tick)
    assert features.volume_delta == 100
    assert features.micro_vwap == 10
    assert aggregator.flush()[0]["volume"] == 200
    tomorrow = Tick("TEST", datetime(2026, 10, 13, 9, 30), price=12, volume=1, amount=12)
    reset = computer.update(tomorrow)
    assert reset.volume_delta == 0
    assert reset.micro_vwap == 12


def test_tick_registry_is_separate_from_bar_strategies():
    import aipha.strategy.tick_strategies  # noqa: F401
    from aipha.strategy.registry import StrategyRegistry, TickStrategyRegistry
    assert isinstance(TickStrategyRegistry().create("tick_momentum_imbalance"), TickStrategy)
    assert StrategyRegistry().get("tick_momentum_imbalance") is None
