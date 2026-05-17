"""Technical indicators library (bar-dict based).

Provides SMA, EMA, RSI, MACD, Bollinger Bands, KDJ, DMI, ATR,
Williams %R, CCI, TRIX, OBV, VWAP and more. All functions operate
on lists of bar dicts with keys: open, high, low, close, volume.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

__all__ = [
    "get_ma", "get_ema", "get_rsi", "get_macd",
    "get_bollinger_bands", "get_kdj", "get_dmi", "get_atr",
    "get_williams_r", "get_cci", "get_trix", "get_obv", "get_vwap",
    "get_high", "get_low", "get_weighted_ma",
    "get_history_volatility", "calc_tr",
]


def get_ma(bars: List[Dict], period: int = 20) -> float:
    """Simple moving average of close prices (excludes current bar)."""
    if len(bars) < period + 1:
        return 0.0
    subset = bars[-(period + 1):-1]
    closes = [b.get("close", 0) for b in subset]
    return sum(closes) / period if period > 0 else 0.0


def get_ema(bars: List[Dict], period: int = 20) -> float:
    """Exponential moving average of close prices."""
    closes = [b.get("close", 0) for b in bars]
    if len(closes) < period:
        return sum(closes) / len(closes) if closes else 0.0
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema


def get_high(bars: List[Dict], period: int = 20) -> float:
    """Highest close in the recent period (excludes current)."""
    subset = bars[-(period + 1):-1] if len(bars) > period else bars[:-1]
    if not subset:
        return 0.0
    return max(b.get("close", 0) for b in subset)


def get_low(bars: List[Dict], period: int = 20) -> float:
    """Lowest close in the recent period (excludes current)."""
    subset = bars[-(period + 1):-1] if len(bars) > period else bars[:-1]
    if not subset:
        return 0.0
    return min(b.get("close", 0) for b in subset)


def calc_tr(high: float, low: float, prev_close: float) -> float:
    """True Range."""
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def get_atr(bars: List[Dict], period: int = 14) -> float:
    """Average True Range."""
    if len(bars) < period + 1:
        return 0.0
    trs: List[float] = []
    for i in range(1, len(bars)):
        h = bars[i].get("high", 0)
        lo = bars[i].get("low", 0)
        pc = bars[i - 1].get("close", 0)
        trs.append(calc_tr(h, lo, pc))
    recent = trs[-period:]
    return sum(recent) / len(recent) if recent else 0.0


def get_weighted_ma(bars: List[Dict], period: int = 20) -> float:
    """Weighted moving average (linear weights)."""
    if len(bars) < period:
        return 0.0
    subset = bars[-period:]
    total_w = period * (period + 1) / 2
    wma = sum((i + 1) * subset[i].get("close", 0) for i in range(period))
    return wma / total_w if total_w > 0 else 0.0


def get_history_volatility(bars: List[Dict]) -> float:
    """Annualized historical volatility from log returns."""
    closes = [b.get("close", 0) for b in bars if b.get("close", 0) > 0]
    if len(closes) < 3:
        return 0.0
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    n = len(log_rets)
    avg = sum(log_rets) / n
    var = sum((r - avg) ** 2 for r in log_rets) / (n - 1) if n > 1 else 0
    return math.sqrt(var) * math.sqrt(min(n, TRADING_DAYS_PER_YEAR))


def get_rsi(bars: List[Dict], period: int = 14) -> float:
    """Relative Strength Index."""
    closes = [b.get("close", 0) for b in bars]
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(d if d > 0 else 0)
        losses.append(-d if d < 0 else 0)
    if len(gains) < period:
        return 50.0
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def get_macd(
    bars: List[Dict], fast: int = 12, slow: int = 26, signal: int = 9,
) -> Tuple[float, float, float]:
    """MACD, Signal, Histogram."""
    closes = [b.get("close", 0) for b in bars]
    if len(closes) < slow:
        return 0.0, 0.0, 0.0

    def _ema_series(data: List[float], period: int) -> List[float]:
        k = 2.0 / (period + 1)
        result = [sum(data[:period]) / period]
        for v in data[period:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)
    offset = slow - fast
    macd_line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    if len(macd_line) < signal:
        return macd_line[-1] if macd_line else 0, 0, 0
    sig_line = _ema_series(macd_line, signal)
    m = macd_line[-1]
    s = sig_line[-1]
    return m, s, m - s


def get_bollinger_bands(
    bars: List[Dict], period: int = 20, num_std: float = 2.0,
) -> Tuple[float, float, float]:
    """Bollinger Bands: (upper, middle, lower)."""
    closes = [b.get("close", 0) for b in bars]
    if len(closes) < period:
        return 0.0, 0.0, 0.0
    recent = closes[-period:]
    mid = sum(recent) / period
    var = sum((c - mid) ** 2 for c in recent) / period
    std = math.sqrt(var)
    return mid + num_std * std, mid, mid - num_std * std


def get_kdj(
    bars: List[Dict], k_period: int = 9, d_period: int = 3, j_smooth: int = 3,
) -> Tuple[float, float, float]:
    """KDJ indicator."""
    if len(bars) < k_period:
        return 50.0, 50.0, 50.0
    rsv_list: List[float] = []
    for i in range(k_period - 1, len(bars)):
        window = bars[i - k_period + 1: i + 1]
        lowest = min(b.get("low", 0) for b in window)
        highest = max(b.get("high", 0) for b in window)
        c = bars[i].get("close", 0)
        rsv = (c - lowest) / (highest - lowest) * 100 if highest != lowest else 50
        rsv_list.append(rsv)
    k = 50.0
    d = 50.0
    for rsv in rsv_list:
        k = (2 * k + rsv) / 3
        d = (2 * d + k) / 3
    j = 3 * k - 2 * d
    return round(k, 2), round(d, 2), round(j, 2)


def get_dmi(bars: List[Dict], period: int = 14) -> Tuple[float, float, float]:
    """+DI, -DI, ADX."""
    if len(bars) < period + 1:
        return 0.0, 0.0, 0.0
    plus_dm, minus_dm, tr_list = [], [], []
    for i in range(1, len(bars)):
        h, lo = bars[i].get("high", 0), bars[i].get("low", 0)
        ph, plo = bars[i - 1].get("high", 0), bars[i - 1].get("low", 0)
        pc = bars[i - 1].get("close", 0)
        up = h - ph
        down = plo - lo
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        tr_list.append(calc_tr(h, lo, pc))
    # Wilder smoothing
    atr = sum(tr_list[:period]) / period
    apd = sum(plus_dm[:period]) / period
    amd = sum(minus_dm[:period]) / period
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        apd = (apd * (period - 1) + plus_dm[i]) / period
        amd = (amd * (period - 1) + minus_dm[i]) / period
    pdi = (apd / atr * 100) if atr > 0 else 0
    mdi = (amd / atr * 100) if atr > 0 else 0
    dx = abs(pdi - mdi) / (pdi + mdi) * 100 if (pdi + mdi) > 0 else 0
    return round(pdi, 2), round(mdi, 2), round(dx, 2)


def get_williams_r(bars: List[Dict], period: int = 14) -> float:
    """Williams %R."""
    if len(bars) < period:
        return -50.0
    window = bars[-period:]
    highest = max(b.get("high", 0) for b in window)
    lowest = min(b.get("low", 0) for b in window)
    close = bars[-1].get("close", 0)
    if highest == lowest:
        return -50.0
    return (highest - close) / (highest - lowest) * -100


def get_cci(bars: List[Dict], period: int = 20) -> float:
    """Commodity Channel Index."""
    if len(bars) < period:
        return 0.0
    tp_list = [(b.get("high", 0) + b.get("low", 0) + b.get("close", 0)) / 3
               for b in bars[-period:]]
    avg_tp = sum(tp_list) / period
    md = sum(abs(tp - avg_tp) for tp in tp_list) / period
    if md == 0:
        return 0.0
    return (tp_list[-1] - avg_tp) / (0.015 * md)


def get_trix(bars: List[Dict], period: int = 12) -> float:
    """TRIX — triple-smoothed EMA rate of change."""
    closes = [b.get("close", 0) for b in bars]
    if len(closes) < period * 3:
        return 0.0

    def _ema_list(data: List[float], p: int) -> List[float]:
        k = 2.0 / (p + 1)
        res = [sum(data[:p]) / p]
        for v in data[p:]:
            res.append(v * k + res[-1] * (1 - k))
        return res

    e1 = _ema_list(closes, period)
    e2 = _ema_list(e1, period)
    e3 = _ema_list(e2, period)
    if len(e3) < 2 or e3[-2] == 0:
        return 0.0
    return (e3[-1] - e3[-2]) / e3[-2] * 100


def get_obv(bars: List[Dict]) -> float:
    """On-Balance Volume."""
    obv = 0.0
    for i in range(1, len(bars)):
        v = bars[i].get("volume", 0)
        c = bars[i].get("close", 0)
        pc = bars[i - 1].get("close", 0)
        if c > pc:
            obv += v
        elif c < pc:
            obv -= v
    return obv


def get_vwap(bars: List[Dict]) -> float:
    """Volume-Weighted Average Price."""
    total_vp = 0.0
    total_v = 0.0
    for b in bars:
        tp = (b.get("high", 0) + b.get("low", 0) + b.get("close", 0)) / 3
        v = b.get("volume", 0)
        total_vp += tp * v
        total_v += v
    return total_vp / total_v if total_v > 0 else 0.0
