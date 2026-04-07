#!/usr/bin/env python3
"""
VeighNa (vnpy) backtest engine bridge — runs vnpy CTA strategies from Node.js.

Input: JSON via stdin or QUANT_CONFIG env var.
Output: JSON to stdout with backtest results.

Supports two modes:
  1. Built-in CTA templates (double-ma, atr-rsi, boll-channel, turtle, king-keltner)
  2. User-defined strategy files (loaded from ~/.openclaw/quant-trading/vnpy_strategies/)

Usage:
  echo '{"strategyId": "vnpy-double-ma", ...}' | python -m engines.vnpy_backtest_engine --json

Requirements:
  pip install vnpy vnpy-ctastrategy vnpy-datamanager akshare pandas
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Optional

# Ensure the python/ directory is on sys.path
_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)


def get_input() -> dict:
    """Read input from stdin or QUANT_CONFIG env."""
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


# ── Standalone Backtest Engine (no vnpy dependency) ──────────────
# When vnpy is not installed, we use a lightweight built-in engine
# that mirrors vnpy CTA strategy behavior for portability.


class BarData:
    """Lightweight bar data compatible with vnpy CTA strategy interface."""

    def __init__(self, row: dict):
        self.datetime = row.get("datetime")
        self.open_price = row.get("open", 0.0)
        self.high_price = row.get("high", 0.0)
        self.low_price = row.get("low", 0.0)
        self.close_price = row.get("close", 0.0)
        self.volume = row.get("volume", 0.0)
        self.turnover = row.get("amount", 0.0)
        self.open_interest = 0.0


class CtaTemplate:
    """Minimal CTA strategy template compatible with vnpy interface.

    Concrete strategies override on_bar() and call buy/sell/short/cover.
    """

    # Strategy parameters — subclasses override via class attributes
    author: str = ""
    parameters: list[str] = []
    variables: list[str] = []

    def __init__(self, params: dict | None = None):
        self._signals: list[dict] = []
        self._pos: int = 0
        self._current_bar: BarData | None = None
        self._bars_cache: list[BarData] = []
        if params:
            for k, v in params.items():
                if hasattr(self, k):
                    setattr(self, k, v)

    @property
    def pos(self) -> int:
        return self._pos

    def on_init(self):
        """Called once before backtest starts."""
        pass

    def on_start(self):
        """Called when backtest starts."""
        pass

    def on_bar(self, bar: BarData):
        """Main strategy logic — called once per bar."""
        raise NotImplementedError

    def on_stop(self):
        """Called when backtest ends."""
        pass

    def buy(self, price: float, volume: int, stop: bool = False):
        """Open long position."""
        self._pos += volume
        if self._current_bar:
            self._signals.append({
                "datetime": str(self._current_bar.datetime),
                "direction": "buy",
                "price": price,
                "volume": volume,
            })

    def sell(self, price: float, volume: int, stop: bool = False):
        """Close long position."""
        self._pos -= volume
        if self._current_bar:
            self._signals.append({
                "datetime": str(self._current_bar.datetime),
                "direction": "sell",
                "price": price,
                "volume": volume,
            })

    def short(self, price: float, volume: int, stop: bool = False):
        """Open short position."""
        self._pos -= volume
        if self._current_bar:
            self._signals.append({
                "datetime": str(self._current_bar.datetime),
                "direction": "short",
                "price": price,
                "volume": volume,
            })

    def cover(self, price: float, volume: int, stop: bool = False):
        """Close short position."""
        self._pos += volume
        if self._current_bar:
            self._signals.append({
                "datetime": str(self._current_bar.datetime),
                "direction": "cover",
                "price": price,
                "volume": volume,
            })

    def get_signals(self) -> list[dict]:
        return self._signals


# ── Technical Indicator Helpers ──────────────────────────────────


def sma(data: list[float], period: int) -> list[float]:
    """Simple moving average."""
    result = [0.0] * len(data)
    for i in range(period - 1, len(data)):
        result[i] = sum(data[i - period + 1: i + 1]) / period
    return result


def ema(data: list[float], period: int) -> list[float]:
    """Exponential moving average."""
    result = [0.0] * len(data)
    if not data:
        return result
    mult = 2.0 / (period + 1)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = data[i] * mult + result[i - 1] * (1 - mult)
    return result


def atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
    """Average True Range."""
    n = len(closes)
    result = [0.0] * n
    if n < 2:
        return result
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    # SMA of TR
    for i in range(period - 1, n):
        result[i] = sum(trs[i - period + 1: i + 1]) / period
    return result


def rsi(closes: list[float], period: int) -> list[float]:
    """Relative Strength Index."""
    n = len(closes)
    result = [50.0] * n
    if n < period + 1:
        return result
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains[i] = diff
        else:
            losses[i] = -diff

    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period

    for i in range(period, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100.0 - 100.0 / (1.0 + rs)
    return result


def bollinger_bands(
    closes: list[float], period: int, num_std: float = 2.0
) -> tuple[list[float], list[float], list[float]]:
    """Bollinger Bands: returns (upper, middle, lower)."""
    n = len(closes)
    upper = [0.0] * n
    middle = [0.0] * n
    lower = [0.0] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1: i + 1]
        avg = sum(window) / period
        std = math.sqrt(sum((x - avg) ** 2 for x in window) / period)
        middle[i] = avg
        upper[i] = avg + num_std * std
        lower[i] = avg - num_std * std
    return upper, middle, lower


def donchian_channel(
    highs: list[float], lows: list[float], period: int
) -> tuple[list[float], list[float]]:
    """Donchian channel: returns (upper, lower)."""
    n = len(highs)
    upper = [0.0] * n
    lower = [0.0] * n
    for i in range(period - 1, n):
        upper[i] = max(highs[i - period + 1: i + 1])
        lower[i] = min(lows[i - period + 1: i + 1])
    return upper, lower


def keltner_channel(
    closes: list[float], highs: list[float], lows: list[float],
    ema_period: int, atr_period: int, atr_mult: float
) -> tuple[list[float], list[float], list[float]]:
    """Keltner Channel: returns (upper, middle, lower)."""
    mid = ema(closes, ema_period)
    atr_vals = atr(highs, lows, closes, atr_period)
    n = len(closes)
    upper = [mid[i] + atr_mult * atr_vals[i] for i in range(n)]
    lower = [mid[i] - atr_mult * atr_vals[i] for i in range(n)]
    return upper, mid, lower


# ── Built-in vnpy-style CTA Strategies ───────────────────────────


class DoubleMaStrategy(CtaTemplate):
    """Double Moving Average crossover — vnpy CTA style."""

    author = "vnpy-bridge"
    fast_window: int = 10
    slow_window: int = 30
    parameters = ["fast_window", "slow_window"]

    def __init__(self, params=None):
        super().__init__(params)
        self._fast_ma = 0.0
        self._slow_ma = 0.0
        self._closes: list[float] = []

    def on_bar(self, bar: BarData):
        self._closes.append(bar.close_price)
        n = len(self._closes)
        if n < self.slow_window:
            return

        fast_ma = sum(self._closes[-self.fast_window:]) / self.fast_window
        slow_ma = sum(self._closes[-self.slow_window:]) / self.slow_window

        if self.pos == 0:
            if fast_ma > slow_ma:
                size = self._calc_size(bar.close_price)
                if size > 0:
                    self.buy(bar.close_price, size)
        elif self.pos > 0:
            if fast_ma < slow_ma:
                self.sell(bar.close_price, abs(self.pos))

        self._fast_ma = fast_ma
        self._slow_ma = slow_ma

    def _calc_size(self, price: float) -> int:
        # Will be set by engine
        return getattr(self, "_fixed_size", 100)


class AtrRsiStrategy(CtaTemplate):
    """ATR-RSI strategy — combines ATR for stop and RSI for entry."""

    author = "vnpy-bridge"
    atr_period: int = 22
    atr_ma_period: int = 10
    rsi_period: int = 5
    rsi_entry: float = 16.0
    rsi_exit: float = 12.0
    parameters = ["atr_period", "atr_ma_period", "rsi_period", "rsi_entry", "rsi_exit"]

    def __init__(self, params=None):
        super().__init__(params)
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []

    def on_bar(self, bar: BarData):
        self._closes.append(bar.close_price)
        self._highs.append(bar.high_price)
        self._lows.append(bar.low_price)
        n = len(self._closes)

        required = max(self.atr_period, self.rsi_period) + self.atr_ma_period
        if n < required:
            return

        atr_vals = atr(self._highs, self._lows, self._closes, self.atr_period)
        atr_ma = sum(atr_vals[-self.atr_ma_period:]) / self.atr_ma_period
        rsi_vals = rsi(self._closes, self.rsi_period)
        cur_rsi = rsi_vals[-1]
        cur_atr = atr_vals[-1]

        if self.pos == 0:
            if cur_atr > atr_ma and cur_rsi > 50 + self.rsi_entry:
                size = getattr(self, "_fixed_size", 100)
                self.buy(bar.close_price, size)
            elif cur_atr > atr_ma and cur_rsi < 50 - self.rsi_entry:
                size = getattr(self, "_fixed_size", 100)
                self.short(bar.close_price, size)
        elif self.pos > 0:
            if cur_rsi < 50 - self.rsi_exit:
                self.sell(bar.close_price, abs(self.pos))
        elif self.pos < 0:
            if cur_rsi > 50 + self.rsi_exit:
                self.cover(bar.close_price, abs(self.pos))

    on_init = CtaTemplate.on_init


class BollChannelStrategy(CtaTemplate):
    """Bollinger Band channel breakout strategy."""

    author = "vnpy-bridge"
    boll_window: int = 18
    boll_dev: float = 3.4
    cci_window: int = 10
    atr_window: int = 30
    sl_multiplier: float = 5.2
    parameters = ["boll_window", "boll_dev", "cci_window", "atr_window", "sl_multiplier"]

    def __init__(self, params=None):
        super().__init__(params)
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._entry_price: float = 0.0

    def on_bar(self, bar: BarData):
        self._closes.append(bar.close_price)
        self._highs.append(bar.high_price)
        self._lows.append(bar.low_price)
        n = len(self._closes)

        required = max(self.boll_window, self.atr_window) + 1
        if n < required:
            return

        upper, mid, lower = bollinger_bands(self._closes, self.boll_window, self.boll_dev)
        atr_vals = atr(self._highs, self._lows, self._closes, self.atr_window)
        cur_atr = atr_vals[-1]

        if self.pos == 0:
            if bar.close_price > upper[-1]:
                size = getattr(self, "_fixed_size", 100)
                self.buy(bar.close_price, size)
                self._entry_price = bar.close_price
            elif bar.close_price < lower[-1]:
                size = getattr(self, "_fixed_size", 100)
                self.short(bar.close_price, size)
                self._entry_price = bar.close_price
        elif self.pos > 0:
            stop_price = self._entry_price - self.sl_multiplier * cur_atr
            if bar.close_price < mid[-1] or bar.close_price < stop_price:
                self.sell(bar.close_price, abs(self.pos))
        elif self.pos < 0:
            stop_price = self._entry_price + self.sl_multiplier * cur_atr
            if bar.close_price > mid[-1] or bar.close_price > stop_price:
                self.cover(bar.close_price, abs(self.pos))


class TurtleSignalStrategy(CtaTemplate):
    """Turtle trading strategy using Donchian channel breakout."""

    author = "vnpy-bridge"
    entry_window: int = 20
    exit_window: int = 10
    atr_window: int = 20
    parameters = ["entry_window", "exit_window", "atr_window"]

    def __init__(self, params=None):
        super().__init__(params)
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []

    def on_bar(self, bar: BarData):
        self._closes.append(bar.close_price)
        self._highs.append(bar.high_price)
        self._lows.append(bar.low_price)
        n = len(self._closes)

        required = max(self.entry_window, self.exit_window) + 1
        if n < required:
            return

        entry_up, entry_dn = donchian_channel(self._highs, self._lows, self.entry_window)
        exit_up, exit_dn = donchian_channel(self._highs, self._lows, self.exit_window)

        if self.pos == 0:
            if bar.close_price > entry_up[-2]:
                size = getattr(self, "_fixed_size", 100)
                self.buy(bar.close_price, size)
            elif bar.close_price < entry_dn[-2]:
                size = getattr(self, "_fixed_size", 100)
                self.short(bar.close_price, size)
        elif self.pos > 0:
            if bar.close_price < exit_dn[-1]:
                self.sell(bar.close_price, abs(self.pos))
        elif self.pos < 0:
            if bar.close_price > exit_up[-1]:
                self.cover(bar.close_price, abs(self.pos))


class KingKeltnerStrategy(CtaTemplate):
    """King Keltner — Keltner Channel breakout strategy."""

    author = "vnpy-bridge"
    kk_period: int = 40
    kk_dev: float = 2.0
    atr_period: int = 40
    parameters = ["kk_period", "kk_dev", "atr_period"]

    def __init__(self, params=None):
        super().__init__(params)
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []

    def on_bar(self, bar: BarData):
        self._closes.append(bar.close_price)
        self._highs.append(bar.high_price)
        self._lows.append(bar.low_price)
        n = len(self._closes)

        required = max(self.kk_period, self.atr_period) + 1
        if n < required:
            return

        upper, mid, lower = keltner_channel(
            self._closes, self._highs, self._lows,
            self.kk_period, self.atr_period, self.kk_dev,
        )

        if self.pos == 0:
            if bar.close_price > upper[-1]:
                size = getattr(self, "_fixed_size", 100)
                self.buy(bar.close_price, size)
            elif bar.close_price < lower[-1]:
                size = getattr(self, "_fixed_size", 100)
                self.short(bar.close_price, size)
        elif self.pos > 0:
            if bar.close_price < mid[-1]:
                self.sell(bar.close_price, abs(self.pos))
        elif self.pos < 0:
            if bar.close_price > mid[-1]:
                self.cover(bar.close_price, abs(self.pos))


class MultiTimeframeStrategy(CtaTemplate):
    """Multi-timeframe trend following: weekly trend + daily entry."""

    author = "vnpy-bridge"
    fast_period: int = 5
    slow_period: int = 20
    weekly_period: int = 10
    parameters = ["fast_period", "slow_period", "weekly_period"]

    def __init__(self, params=None):
        super().__init__(params)
        self._closes: list[float] = []
        self._weekly_closes: list[float] = []
        self._bar_count: int = 0

    def on_bar(self, bar: BarData):
        self._closes.append(bar.close_price)
        self._bar_count += 1

        # Aggregate weekly bars (every 5 daily bars)
        if self._bar_count % 5 == 0:
            self._weekly_closes.append(bar.close_price)

        n = len(self._closes)
        wn = len(self._weekly_closes)
        if n < self.slow_period or wn < self.weekly_period:
            return

        # Weekly trend
        weekly_ma = sum(self._weekly_closes[-self.weekly_period:]) / self.weekly_period
        weekly_trend_up = bar.close_price > weekly_ma

        # Daily fast/slow MA
        fast_ma = sum(self._closes[-self.fast_period:]) / self.fast_period
        slow_ma = sum(self._closes[-self.slow_period:]) / self.slow_period

        if self.pos == 0:
            if weekly_trend_up and fast_ma > slow_ma:
                size = getattr(self, "_fixed_size", 100)
                self.buy(bar.close_price, size)
            elif not weekly_trend_up and fast_ma < slow_ma:
                size = getattr(self, "_fixed_size", 100)
                self.short(bar.close_price, size)
        elif self.pos > 0:
            if fast_ma < slow_ma:
                self.sell(bar.close_price, abs(self.pos))
        elif self.pos < 0:
            if fast_ma > slow_ma:
                self.cover(bar.close_price, abs(self.pos))


# ── Strategy Registry ────────────────────────────────────────────

VNPY_STRATEGIES: dict[str, dict] = {
    "vnpy-double-ma": {
        "class": DoubleMaStrategy,
        "name": "vnpy双均线 (Double MA)",
        "description": "vnpy经典双均线策略：快慢均线交叉开平仓，适合趋势行情。",
        "category": "trend",
        "parameters": [
            {"name": "fast_window", "type": "number", "default": 10, "description": "快速均线周期", "min": 2, "max": 120},
            {"name": "slow_window", "type": "number", "default": 30, "description": "慢速均线周期", "min": 5, "max": 250},
        ],
    },
    "vnpy-atr-rsi": {
        "class": AtrRsiStrategy,
        "name": "vnpy ATR-RSI",
        "description": "ATR波动率过滤 + RSI超买超卖信号，适合震荡和趋势行情。",
        "category": "composite",
        "parameters": [
            {"name": "atr_period", "type": "number", "default": 22, "description": "ATR周期", "min": 5, "max": 60},
            {"name": "rsi_period", "type": "number", "default": 5, "description": "RSI周期", "min": 2, "max": 30},
            {"name": "rsi_entry", "type": "number", "default": 16, "description": "RSI入场阈值", "min": 5, "max": 30},
        ],
    },
    "vnpy-boll-channel": {
        "class": BollChannelStrategy,
        "name": "vnpy布林通道 (Bollinger Channel)",
        "description": "布林带突破策略，结合ATR动态止损。",
        "category": "trend",
        "parameters": [
            {"name": "boll_window", "type": "number", "default": 18, "description": "布林带周期", "min": 5, "max": 60},
            {"name": "boll_dev", "type": "number", "default": 3.4, "description": "标准差倍数", "min": 1.0, "max": 5.0},
            {"name": "sl_multiplier", "type": "number", "default": 5.2, "description": "止损ATR倍数", "min": 1.0, "max": 10.0},
        ],
    },
    "vnpy-turtle": {
        "class": TurtleSignalStrategy,
        "name": "vnpy海龟交易 (Turtle)",
        "description": "经典海龟交易法则：唐奇安通道突破入场，反向通道出场。",
        "category": "trend",
        "parameters": [
            {"name": "entry_window", "type": "number", "default": 20, "description": "入场通道周期", "min": 5, "max": 60},
            {"name": "exit_window", "type": "number", "default": 10, "description": "出场通道周期", "min": 3, "max": 30},
        ],
    },
    "vnpy-king-keltner": {
        "class": KingKeltnerStrategy,
        "name": "vnpy肯特纳通道 (King Keltner)",
        "description": "Keltner通道突破策略，利用ATR构建波动通道。",
        "category": "trend",
        "parameters": [
            {"name": "kk_period", "type": "number", "default": 40, "description": "通道周期", "min": 10, "max": 80},
            {"name": "kk_dev", "type": "number", "default": 2.0, "description": "ATR倍数", "min": 1.0, "max": 4.0},
        ],
    },
    "vnpy-multi-timeframe": {
        "class": MultiTimeframeStrategy,
        "name": "vnpy多周期趋势 (Multi-Timeframe)",
        "description": "周线判趋势，日线找入场；大小周期共振时开仓。",
        "category": "trend",
        "parameters": [
            {"name": "fast_period", "type": "number", "default": 5, "description": "日线快速均线", "min": 2, "max": 30},
            {"name": "slow_period", "type": "number", "default": 20, "description": "日线慢速均线", "min": 5, "max": 60},
            {"name": "weekly_period", "type": "number", "default": 10, "description": "周线均线周期", "min": 3, "max": 30},
        ],
    },
}


# ── Data Fetching ────────────────────────────────────────────────


def _fetch_data(symbol: str, start_date: str, end_date: str, market: str | None = None) -> list[dict]:
    """Fetch daily OHLCV data as list of dicts."""
    try:
        from data_providers.provider_factory import fetch_data_for_backtest
        df = fetch_data_for_backtest(symbol, start_date, end_date, market)
        if df is not None and not df.empty:
            records = []
            for idx, row in df.iterrows():
                records.append({
                    "datetime": str(idx) if hasattr(idx, "strftime") else str(idx),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": float(row.get("volume", 0)),
                    "amount": float(row.get("amount", 0)) if "amount" in row.index else 0,
                })
            return records
    except ImportError:
        print("[vnpy_engine] provider_factory not available, using akshare fallback", file=sys.stderr)

    # Fallback: akshare for A-shares
    try:
        import akshare as ak
        import pandas as pd

        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",
        )
        if df is None or df.empty:
            return []

        records = []
        for _, row in df.iterrows():
            records.append({
                "datetime": str(row.get("日期", "")),
                "open": float(row.get("开盘", 0)),
                "high": float(row.get("最高", 0)),
                "low": float(row.get("最低", 0)),
                "close": float(row.get("收盘", 0)),
                "volume": float(row.get("成交量", 0)),
                "amount": float(row.get("成交额", 0)),
            })
        return records
    except Exception as e:
        print(f"[vnpy_engine] Failed to fetch data for {symbol}: {e}", file=sys.stderr)
        return []


# ── Backtest Engine ──────────────────────────────────────────────


def run_vnpy_backtest(config: dict) -> dict:
    """Run a CTA strategy backtest using the vnpy-compatible engine.

    Config:
    {
        "strategyId": "vnpy-double-ma",
        "symbols": ["600519"],
        "startDate": "2024-01-01",
        "endDate": "2025-01-01",
        "initialCash": 100000,
        "commission": 0.0003,
        "slippage": 0.001,
        "parameters": {},
        "market": "cn_stock"
    }
    """
    strategy_id = config.get("strategyId", "vnpy-double-ma")
    symbols = config.get("symbols", ["000300"])
    start_date = config.get("startDate", "2024-01-01")
    end_date = config.get("endDate", datetime.now().strftime("%Y-%m-%d"))
    initial_cash = config.get("initialCash", 100000)
    commission_rate = config.get("commission", 0.0003)
    slippage_rate = config.get("slippage", 0.001)
    stamp_tax_rate = 0.0005  # A-share sell-side stamp duty
    parameters = config.get("parameters", {})
    market = config.get("market")

    # Resolve strategy
    strategy_info = VNPY_STRATEGIES.get(strategy_id)
    if not strategy_info:
        return {"ok": False, "error": f"Unknown vnpy strategy: {strategy_id}"}

    strategy_class = strategy_info["class"]

    # Convert camelCase params to snake_case
    py_params = {}
    import re
    for k, v in parameters.items():
        snake = re.sub(r"([A-Z])", r"_\1", k).lower()
        py_params[snake] = v

    strategy = strategy_class(py_params)

    # Fetch data for first symbol
    symbol = symbols[0] if symbols else "000300"
    bars_data = _fetch_data(symbol, start_date, end_date, market)
    if not bars_data:
        return {"ok": False, "error": f"No data available for {symbol}"}

    # Calculate position size
    first_close = bars_data[0]["close"] if bars_data else 10.0
    lot_size = 100  # A-share lot
    fixed_size = int(initial_cash * 0.95 / first_close / lot_size) * lot_size
    if fixed_size < lot_size:
        fixed_size = lot_size
    strategy._fixed_size = fixed_size

    # Run strategy
    strategy.on_init()
    strategy.on_start()

    cash = initial_cash
    position = 0
    total_fees = 0.0
    equity_curve = []
    peak_value = initial_cash
    max_drawdown = 0.0
    max_dd_duration = 0
    dd_start = 0
    entry_price = 0.0
    signals_out = []

    for i, bar_dict in enumerate(bars_data):
        bar = BarData(bar_dict)
        strategy._current_bar = bar

        prev_pos = strategy.pos
        strategy.on_bar(bar)
        new_signals = strategy._signals[len(signals_out):]

        # Process new signals
        for sig in new_signals:
            direction = sig["direction"]
            price = sig["price"]
            volume = sig["volume"]

            if direction == "buy":
                exec_price = price * (1 + slippage_rate)
                notional = volume * exec_price
                comm = max(notional * commission_rate, 5.0)
                transfer = notional * 0.00002
                cost = notional + comm + transfer
                if cost <= cash:
                    cash -= cost
                    position += volume
                    entry_price = exec_price
                    total_fees += comm + transfer

            elif direction == "sell":
                exec_price = price * (1 - slippage_rate)
                sell_vol = min(volume, position)
                if sell_vol > 0:
                    notional = sell_vol * exec_price
                    comm = max(notional * commission_rate, 5.0)
                    stamp = notional * stamp_tax_rate
                    transfer = notional * 0.00002
                    proceeds = notional - comm - stamp - transfer
                    cash += proceeds
                    position -= sell_vol
                    total_fees += comm + stamp + transfer

            elif direction == "short":
                exec_price = price * (1 - slippage_rate)
                notional = volume * exec_price
                comm = max(notional * commission_rate, 5.0)
                transfer = notional * 0.00002
                cash -= comm + transfer
                position -= volume
                entry_price = exec_price
                total_fees += comm + transfer

            elif direction == "cover":
                exec_price = price * (1 + slippage_rate)
                cover_vol = min(volume, abs(position))
                if cover_vol > 0:
                    notional = cover_vol * exec_price
                    comm = max(notional * commission_rate, 5.0)
                    transfer = notional * 0.00002
                    pnl = (entry_price - exec_price) * cover_vol
                    cash += pnl - comm - transfer
                    position += cover_vol
                    total_fees += comm + transfer

            # Record signal for output
            signals_out.append({
                "timestamp": int(datetime.strptime(
                    bar_dict["datetime"][:10], "%Y-%m-%d"
                ).timestamp() * 1000) if len(bar_dict["datetime"]) >= 10 else 0,
                "symbol": symbol,
                "direction": direction,
                "price": round(price, 4),
                "reason": f"vnpy {strategy_id}",
                "strategyId": strategy_id,
            })

        # Equity
        equity = cash + position * bar.close_price
        date_str = bar_dict["datetime"][:10]
        equity_curve.append({"date": date_str, "value": round(equity, 2)})

        # Drawdown
        if equity > peak_value:
            peak_value = equity
            dd_start = i
        dd = (peak_value - equity) / peak_value if peak_value > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd
            max_dd_duration = i - dd_start

    strategy.on_stop()

    # Final metrics
    last_price = bars_data[-1]["close"] if bars_data else 0
    final_value = cash + position * last_price
    total_return = (final_value - initial_cash) / initial_cash
    trading_days = len(bars_data)
    years = trading_days / 252

    annualized_return = 0.0
    if years > 0 and final_value > 0 and initial_cash > 0:
        annualized_return = (final_value / initial_cash) ** (1 / years) - 1

    # Sharpe ratio
    daily_returns = []
    for j in range(1, len(equity_curve)):
        prev_val = equity_curve[j - 1]["value"]
        if prev_val > 0:
            daily_returns.append(equity_curve[j]["value"] / prev_val - 1)

    sharpe = 0.0
    if len(daily_returns) > 1:
        avg_ret = sum(daily_returns) / len(daily_returns)
        variance = sum((r - avg_ret) ** 2 for r in daily_returns) / len(daily_returns)
        std_dev = math.sqrt(variance)
        if std_dev > 0:
            sharpe = (avg_ret / std_dev) * math.sqrt(252)

    # Win rate
    buy_prices = [s["price"] for s in signals_out if s["direction"] == "buy"]
    sell_prices = [s["price"] for s in signals_out if s["direction"] == "sell"]
    trades = min(len(buy_prices), len(sell_prices))
    wins = sum(1 for i in range(trades) if sell_prices[i] > buy_prices[i])
    win_rate = wins / trades * 100 if trades > 0 else 0

    # Drawdown curve
    drawdown_curve = []
    dd_peak = equity_curve[0]["value"] if equity_curve else initial_cash
    for pt in equity_curve:
        if pt["value"] > dd_peak:
            dd_peak = pt["value"]
        dd_val = ((dd_peak - pt["value"]) / dd_peak * 100) if dd_peak > 0 else 0
        drawdown_curve.append({"date": pt["date"], "drawdown": round(dd_val, 4)})

    # Monthly returns
    monthly_map: dict[str, dict] = {}
    for pt in equity_curve:
        month = pt["date"][:7]
        if month not in monthly_map:
            monthly_map[month] = {"start": pt["value"], "end": pt["value"]}
        else:
            monthly_map[month]["end"] = pt["value"]

    monthly_returns = []
    for month, vals in monthly_map.items():
        ret = ((vals["end"] - vals["start"]) / vals["start"] * 100) if vals["start"] > 0 else 0
        monthly_returns.append({"month": month, "return": round(ret, 4)})

    return {
        "ok": True,
        "strategyId": strategy_id,
        "symbols": symbols,
        "startDate": start_date,
        "endDate": end_date,
        "initialCash": initial_cash,
        "finalValue": round(final_value, 2),
        "totalReturn": round(total_return * 100, 2),
        "annualizedReturn": round(annualized_return * 100, 2),
        "maxDrawdown": round(max_drawdown * 100, 2),
        "maxDrawdownDuration": max_dd_duration,
        "sharpeRatio": round(sharpe, 3),
        "winRate": round(win_rate, 1),
        "totalTrades": trades,
        "totalFees": round(total_fees, 2),
        "feeImpact": round(total_fees / initial_cash * 100, 4),
        "signals": signals_out,
        "equityCurve": equity_curve,
        "drawdownCurve": drawdown_curve,
        "monthlyReturns": monthly_returns,
    }


# ── Strategy Listing ─────────────────────────────────────────────


def list_vnpy_strategies() -> list[dict]:
    """Return metadata for all available vnpy strategies."""
    result = []
    for sid, info in VNPY_STRATEGIES.items():
        result.append({
            "id": sid,
            "name": info["name"],
            "description": info["description"],
            "category": info["category"],
            "parameters": info["parameters"],
            "engine": "vnpy",
        })
    return result


# ── CLI Entry Point ──────────────────────────────────────────────


if __name__ == "__main__":
    config = get_input()
    action = config.get("action")

    if action == "list_strategies":
        result = list_vnpy_strategies()
    else:
        result = run_vnpy_backtest(config)

    print(json.dumps(result, ensure_ascii=False))
