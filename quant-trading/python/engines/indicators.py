"""
Technical indicators: MA, EMA, ATR, historical volatility, K-line helpers.

Migrated from jtstock/stock_tools/indicatrix.py, history_volatility.py,
and stock_container.py, modernized for Python 3.
"""

import math
from typing import Optional


# ── K-line Data Structure ─────────────────────────────────────────
# Expected dict format per bar:
# { "datetime": 20210101, "open": 10.0, "high": 11.0, "low": 9.5,
#   "close": 10.5, "volume": 123456 }


def get_ma(bars: list[dict], period: int) -> Optional[float]:
    """Simple Moving Average (SMA) of the last `period` bars' close prices.

    Uses bars[-period-1:-1] (the previous `period` bars, excluding current).
    If not enough bars, returns None.
    """
    if not bars or len(bars) < period + 1:
        return None
    data = bars[-period - 1 : -1]
    total = sum(d["close"] for d in data)
    return total / period


def get_ema(bars: list[dict], period: int) -> Optional[float]:
    """Exponential Moving Average over `period` bars.

    Computes from the full bar sequence up to the most recent close.
    """
    if not bars or len(bars) < period:
        return None
    multiplier = 2.0 / (period + 1)
    # seed with SMA of first `period` bars
    ema = sum(b["close"] for b in bars[:period]) / period
    for b in bars[period:]:
        ema = (b["close"] - ema) * multiplier + ema
    return ema


def get_high(bars: list[dict], period: int) -> Optional[float]:
    """Highest close in the last `period` bars (excluding current)."""
    if not bars or len(bars) <= period:
        return None
    data = bars[-period - 1 : -1]
    return max(d["close"] for d in data)


def get_low(bars: list[dict], period: int) -> Optional[float]:
    """Lowest close in the last `period` bars (excluding current)."""
    if not bars or len(bars) <= period:
        return None
    data = bars[-period - 1 : -1]
    return min(d["close"] for d in data)


def calc_tr(high: float, low: float, prev_close: float) -> float:
    """True Range = max(H-L, |H-PrevClose|, |PrevClose-L|)."""
    return max(high - low, abs(high - prev_close), abs(prev_close - low))


def get_atr(bars: list[dict], period: int) -> Optional[float]:
    """Average True Range over `period` bars.

    Bars must contain 'high', 'low', and 'close' fields.
    """
    if not bars or len(bars) < period + 1:
        return None
    tr_sum = 0.0
    for i in range(len(bars) - period, len(bars)):
        prev_close = bars[i - 1]["close"]
        tr_sum += calc_tr(bars[i]["high"], bars[i]["low"], prev_close)
    return tr_sum / period


def get_weighted_ma(bars: list[dict], period: int) -> Optional[float]:
    """Weighted Moving Average (WMA). More recent bars get higher weight.

    Weight[i] = i+1 for i in 0..period-1 (oldest to newest).
    """
    if not bars or len(bars) < period:
        return None
    data = bars[-period:]
    weight_total = period * (period + 1) / 2
    wma = sum(d["close"] * (i + 1) for i, d in enumerate(data)) / weight_total
    return wma


# ── Historical Volatility ────────────────────────────────────────


def get_history_volatility(bars: list[dict]) -> Optional[float]:
    """Annualized historical volatility from daily close prices.

    HV = StdDev(ln(C_t / C_{t-1})) * sqrt(N)

    Bars must be sorted chronologically.
    Returns None if insufficient data or any close <= 0.
    """
    if not bars or len(bars) < 3:
        return None

    log_returns: list[float] = []
    prev_close = bars[0]["close"]
    for bar in bars[1:]:
        c = bar["close"]
        if c <= 0 or prev_close <= 0:
            return None
        log_returns.append(math.log(c / prev_close))
        prev_close = c

    if len(log_returns) < 2:
        return None

    mean = sum(log_returns) / len(log_returns)
    variance = sum((x - mean) ** 2 for x in log_returns) / len(log_returns)
    std_dev = math.sqrt(variance)
    # annualize: sqrt(trading days per year)
    return std_dev * math.sqrt(len(log_returns) + 1)


# ── K-line Container ─────────────────────────────────────────────


class KlineContainer:
    """A sliding-window container for K-line data per stock.

    Migrated from jtstock/stock_tools/stock_container.py.
    """

    def __init__(self, capacity: int = 250):
        self.capacity = capacity
        self._data: dict[str, list[dict]] = {}  # stock_id → list of bars

    def push(self, stock_id: str, bar: dict) -> None:
        """Add a bar. If capacity exceeded, oldest bar is evicted."""
        if stock_id not in self._data:
            self._data[stock_id] = []
        bars = self._data[stock_id]
        bars.append(bar)
        if len(bars) > self.capacity:
            self._data[stock_id] = bars[-self.capacity :]

    def count(self, stock_id: str) -> int:
        """Number of bars stored for a stock."""
        return len(self._data.get(stock_id, []))

    def get_bars(self, stock_id: str) -> Optional[list[dict]]:
        """Get all stored bars for a stock."""
        return self._data.get(stock_id)

    def get_latest_price(self, stock_id: str) -> Optional[float]:
        """Latest close price."""
        bars = self.get_bars(stock_id)
        if not bars:
            return None
        return bars[-1]["close"]

    def get_ma(self, stock_id: str, period: int) -> Optional[float]:
        """SMA for a stock."""
        return get_ma(self.get_bars(stock_id) or [], period)

    def get_atr(self, stock_id: str, period: int) -> Optional[float]:
        """ATR for a stock."""
        return get_atr(self.get_bars(stock_id) or [], period)

    def get_all_ids(self) -> list[str]:
        """All stock IDs in the container."""
        return list(self._data.keys())
