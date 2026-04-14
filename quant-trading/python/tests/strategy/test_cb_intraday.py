"""CB 日内 T+0 策略基类 + 具体策略测试。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

import pytest

from aipha.data.stream import Bar, BarFrequency
from aipha.domain.models import Side
from aipha.strategy.base import OrderIntent, StrategyContext
from aipha.strategy.cb_intraday_base import (
    CBIntradayBase,
    CBIntradayConfig,
    IntradaySignal,
    IntradayState,
)
from aipha.strategy.cb_intraday_strategies import (
    CBOvernightGapDecay,
    CBVWAPDeviation,
    OvernightGapConfig,
    VWAPDeviationConfig,
)


# ------------------------------------------------------------------
# 辅助
# ------------------------------------------------------------------

def _bar(symbol: str, ts: datetime, close: float, vol: float = 100.0,
         open_: float | None = None) -> Bar:
    o = open_ if open_ is not None else close
    return Bar(
        symbol=symbol, timestamp=ts, open=o,
        high=max(o, close) + 0.01, low=min(o, close) - 0.01,
        close=close, volume=vol, amount=close * vol,
        frequency=BarFrequency.MIN_1,
    )


def _ctx(ts: datetime, nav: float = 1_000_000.0) -> StrategyContext:
    return StrategyContext(
        current_time=ts,
        positions={},
        cash=nav,
        nav=nav,
    )


# ------------------------------------------------------------------
# IntradayState 测试
# ------------------------------------------------------------------

class TestIntradayState:
    def test_update_vwap(self):
        s = IntradayState(symbol="A", trade_date=datetime(2024, 1, 15))
        s.update_vwap(100.0, 1000)
        assert s.vwap == pytest.approx(100.0)
        s.update_vwap(102.0, 1000)
        expected = (100.0 * 1000 + 102.0 * 1000) / 2000
        assert s.vwap == pytest.approx(expected)

    def test_high_low_tracking(self):
        s = IntradayState(symbol="A", trade_date=datetime(2024, 1, 15))
        s.update_vwap(100.0, 100)
        s.update_vwap(105.0, 100)
        s.update_vwap(98.0, 100)
        assert s.high_of_day == 105.0
        assert s.low_of_day == 98.0


# ------------------------------------------------------------------
# CBIntradayBase 测试
# ------------------------------------------------------------------

class _DummyIntraday(CBIntradayBase):
    """始终返回 HOLD 的测试子类。"""
    name = "dummy_intraday"

    def on_minute_bar(self, bar, state, context):
        return IntradaySignal.HOLD


class _AlwaysBuyIntraday(CBIntradayBase):
    """始终 BUY 的测试子类。"""
    name = "always_buy_intraday"

    def on_minute_bar(self, bar, state, context):
        return IntradaySignal.BUY


class TestCBIntradayBase:
    def test_config_defaults(self):
        cfg = CBIntradayConfig()
        assert cfg.lot_size == 10
        assert cfg.flat_before_close_min == 5
        assert cfg.max_intraday_trades == 10

    def test_hold_no_orders(self):
        strat = _DummyIntraday()
        ts = datetime(2024, 1, 15, 10, 0)
        bars = {"A": _bar("A", ts, 100.0)}
        orders = strat.on_bar(bars, _ctx(ts))
        assert orders == []

    def test_buy_generates_order(self):
        strat = _AlwaysBuyIntraday(CBIntradayConfig(max_position_pct=0.1))
        ts = datetime(2024, 1, 15, 10, 0)
        bars = {"A": _bar("A", ts, 100.0)}
        orders = strat.on_bar(bars, _ctx(ts, nav=100_000))
        assert len(orders) == 1
        assert orders[0].side == Side.BUY
        assert orders[0].qty > 0
        assert orders[0].qty % 10 == 0  # lot_size对齐

    def test_lot_size_alignment(self):
        qty = CBIntradayBase._calc_qty(100.0, 100_000, CBIntradayConfig(
            max_position_pct=0.05, lot_size=10))
        assert qty % 10 == 0
        assert qty == 50  # 100000*0.05/100 = 50

    def test_max_trade_limit(self):
        strat = _AlwaysBuyIntraday(CBIntradayConfig(max_intraday_trades=1))
        ts1 = datetime(2024, 1, 15, 10, 0)
        strat.on_bar({"A": _bar("A", ts1, 100)}, _ctx(ts1))
        # 第二次应被限制
        ts2 = datetime(2024, 1, 15, 10, 1)
        orders = strat.on_bar({"A": _bar("A", ts2, 101)}, _ctx(ts2))
        assert orders == []

    def test_date_reset(self):
        strat = _DummyIntraday()
        ts1 = datetime(2024, 1, 15, 10, 0)
        strat.on_bar({"A": _bar("A", ts1, 100)}, _ctx(ts1))
        assert len(strat._states) == 1
        # 新的日期
        ts2 = datetime(2024, 1, 16, 10, 0)
        strat.on_bar({"A": _bar("A", ts2, 101)}, _ctx(ts2))
        # 状态应已重置
        assert strat._current_date.date() == ts2.date()

    def test_symbol_filter(self):
        strat = _AlwaysBuyIntraday(CBIntradayConfig(symbols=["A"]))
        ts = datetime(2024, 1, 15, 10, 0)
        bars = {"A": _bar("A", ts, 100), "B": _bar("B", ts, 200)}
        orders = strat.on_bar(bars, _ctx(ts))
        symbols = [o.symbol for o in orders]
        assert "A" in symbols
        assert "B" not in symbols


# ------------------------------------------------------------------
# VWAP 偏离策略测试
# ------------------------------------------------------------------

class TestCBVWAPDeviation:
    def test_config_defaults(self):
        cfg = VWAPDeviationConfig()
        assert cfg.entry_threshold == -0.008
        assert cfg.exit_threshold == -0.001
        assert cfg.min_volume_bars == 30

    def test_hold_before_min_bars(self):
        """累积bar不足时应 HOLD。"""
        strat = CBVWAPDeviation(VWAPDeviationConfig(min_volume_bars=5))
        ts = datetime(2024, 1, 15, 9, 31)
        state = IntradayState(symbol="A", trade_date=ts)
        # 只有1根，不够5根
        state.cumulative_volume = 1
        state.vwap = 100.0
        bar = _bar("A", ts, 99.0)
        result = strat.on_minute_bar(bar, state, _ctx(ts))
        assert result == IntradaySignal.HOLD

    def test_buy_signal_below_vwap(self):
        """价格低于 VWAP entry_threshold 时买入。"""
        cfg = VWAPDeviationConfig(
            entry_threshold=-0.005, min_volume_bars=0)
        strat = CBVWAPDeviation(cfg)
        ts = datetime(2024, 1, 15, 10, 30)
        state = IntradayState(symbol="A", trade_date=ts)
        state.cumulative_volume = 100
        state.vwap = 100.0
        # 价格 99.0 → deviation = -1%, 低于 -0.5%
        bar = _bar("A", ts, 99.0)
        result = strat.on_minute_bar(bar, state, _ctx(ts))
        assert result == IntradaySignal.BUY

    def test_sell_signal_near_vwap(self):
        """有持仓且回归到 VWAP 附近时卖出。"""
        cfg = VWAPDeviationConfig(
            exit_threshold=-0.001, min_volume_bars=0)
        strat = CBVWAPDeviation(cfg)
        ts = datetime(2024, 1, 15, 11, 0)
        state = IntradayState(symbol="A", trade_date=ts)
        state.cumulative_volume = 100
        state.vwap = 100.0
        state.qty = 10  # 有持仓
        # 价格 99.95 → deviation = -0.05%, 高于 -0.1%
        bar = _bar("A", ts, 99.95)
        result = strat.on_minute_bar(bar, state, _ctx(ts))
        assert result == IntradaySignal.SELL

    def test_hold_on_max_deviation(self):
        """偏离过大（断崖）不交易。"""
        cfg = VWAPDeviationConfig(
            max_deviation=-0.02, min_volume_bars=0)
        strat = CBVWAPDeviation(cfg)
        ts = datetime(2024, 1, 15, 10, 30)
        state = IntradayState(symbol="A", trade_date=ts)
        state.cumulative_volume = 100
        state.vwap = 100.0
        # deviation = -3%, 超过 max_deviation=-2%
        bar = _bar("A", ts, 97.0)
        result = strat.on_minute_bar(bar, state, _ctx(ts))
        assert result == IntradaySignal.HOLD


# ------------------------------------------------------------------
# 隔夜跳空衰减测试
# ------------------------------------------------------------------

class TestCBOvernightGapDecay:
    def test_config_defaults(self):
        cfg = OvernightGapConfig()
        assert cfg.gap_threshold == 0.015
        assert cfg.decay_target_pct == 0.5
        assert cfg.entry_delay_bars == 5

    def test_no_prev_close_hold(self):
        """无前日收盘 → HOLD。"""
        strat = CBOvernightGapDecay()
        ts = datetime(2024, 1, 15, 9, 31)
        state = IntradayState(symbol="A", trade_date=ts)
        bar = _bar("A", ts, 100.0)
        result = strat.on_minute_bar(bar, state, _ctx(ts))
        assert result == IntradaySignal.HOLD

    def test_gap_down_buy(self):
        """跳低 > threshold 且delay后买入。"""
        cfg = OvernightGapConfig(gap_threshold=0.01, entry_delay_bars=2)
        strat = CBOvernightGapDecay(cfg)
        strat._prev_close["A"] = 100.0
        strat._open_price["A"] = 98.0   # -2% 跳低
        strat._bar_count_today["A"] = 1  # on_minute_bar会+1变成2=entry_delay

        ts = datetime(2024, 1, 15, 9, 32)
        state = IntradayState(symbol="A", trade_date=ts)
        bar = _bar("A", ts, 98.5, open_=98.0)
        result = strat.on_minute_bar(bar, state, _ctx(ts))
        assert result == IntradaySignal.BUY

    def test_small_gap_hold(self):
        """跳空不够大 → HOLD。"""
        cfg = OvernightGapConfig(gap_threshold=0.02)
        strat = CBOvernightGapDecay(cfg)
        strat._prev_close["A"] = 100.0
        strat._open_price["A"] = 99.5  # -0.5% < 2%
        strat._bar_count_today["A"] = 10

        ts = datetime(2024, 1, 15, 10, 0)
        state = IntradayState(symbol="A", trade_date=ts)
        bar = _bar("A", ts, 99.5, open_=99.5)
        result = strat.on_minute_bar(bar, state, _ctx(ts))
        assert result == IntradaySignal.HOLD

    def test_gap_decay_sell(self):
        """回填达标时卖出。"""
        cfg = OvernightGapConfig(
            gap_threshold=0.01, decay_target_pct=0.5, entry_delay_bars=1)
        strat = CBOvernightGapDecay(cfg)
        strat._prev_close["A"] = 100.0
        strat._open_price["A"] = 98.0    # -2% gap, gap*prev=2.0
        strat._bar_count_today["A"] = 5

        ts = datetime(2024, 1, 15, 10, 30)
        state = IntradayState(symbol="A", trade_date=ts)
        state.qty = 10
        state.entry_price = 98.0
        # fill_pct = (99.2 - 98.0) / |(-0.02)*100| = 1.2 / 2.0 = 0.6 > 0.5
        bar = _bar("A", ts, 99.2, open_=98.0)
        result = strat.on_minute_bar(bar, state, _ctx(ts))
        assert result == IntradaySignal.SELL
