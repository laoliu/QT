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


# ── RSI ─────────────────────────────────────────────────────────


def get_rsi(bars: list[dict], period: int = 14) -> Optional[float]:
    """Relative Strength Index over `period` bars."""
    if not bars or len(bars) < period + 1:
        return None
    closes = [b["close"] for b in bars[-(period + 1):]]
    gains = 0.0
    losses = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


# ── MACD ────────────────────────────────────────────────────────


def get_macd(
    bars: list[dict], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """MACD line, signal line, histogram from bar data."""
    if not bars or len(bars) < slow + signal:
        return None, None, None
    closes = [b["close"] for b in bars]
    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)

    ef = sum(closes[:fast]) / fast
    es = sum(closes[:slow]) / slow

    macd_series: list[float] = []
    for i, c in enumerate(closes):
        if i < fast:
            ef = sum(closes[: i + 1]) / (i + 1)
        else:
            ef = c * k_fast + ef * (1 - k_fast)
        if i < slow:
            es = sum(closes[: i + 1]) / (i + 1)
        else:
            es = c * k_slow + es * (1 - k_slow)
        if i >= slow - 1:
            macd_series.append(ef - es)

    if len(macd_series) < signal:
        return macd_series[-1] if macd_series else None, None, None

    k_sig = 2.0 / (signal + 1)
    sig = sum(macd_series[:signal]) / signal
    for m in macd_series[signal:]:
        sig = m * k_sig + sig * (1 - k_sig)

    macd_val = macd_series[-1]
    hist = macd_val - sig
    return macd_val, sig, hist


# ── Bollinger Bands ─────────────────────────────────────────────


def get_bollinger_bands(
    bars: list[dict], period: int = 20, num_std: float = 2.0
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Bollinger Bands: (upper, middle, lower)."""
    if not bars or len(bars) < period:
        return None, None, None
    closes = [b["close"] for b in bars[-period:]]
    mid = sum(closes) / period
    variance = sum((x - mid) ** 2 for x in closes) / period
    std = math.sqrt(variance)
    return mid + num_std * std, mid, mid - num_std * std


# ── KDJ ─────────────────────────────────────────────────────────


def get_kdj(
    bars: list[dict], k_period: int = 9, d_period: int = 3, j_smooth: int = 3
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """KDJ Stochastic: (K, D, J)."""
    n = len(bars)
    if not bars or n < k_period:
        return None, None, None

    rsv_series: list[float] = []
    for i in range(k_period - 1, n):
        window = bars[i - k_period + 1 : i + 1]
        highest = max(b["high"] for b in window)
        lowest = min(b["low"] for b in window)
        if highest == lowest:
            rsv_series.append(50.0)
        else:
            rsv_series.append((bars[i]["close"] - lowest) / (highest - lowest) * 100)

    if len(rsv_series) < d_period:
        return None, None, None

    k_series: list[float] = []
    for i in range(len(rsv_series)):
        if i < d_period - 1:
            k_series.append(sum(rsv_series[: i + 1]) / (i + 1))
        else:
            k_series.append(sum(rsv_series[i - d_period + 1 : i + 1]) / d_period)

    if len(k_series) < j_smooth:
        return None, None, None

    d_series: list[float] = []
    for i in range(len(k_series)):
        if i < j_smooth - 1:
            d_series.append(sum(k_series[: i + 1]) / (i + 1))
        else:
            d_series.append(sum(k_series[i - j_smooth + 1 : i + 1]) / j_smooth)

    k_val = k_series[-1]
    d_val = d_series[-1]
    j_val = 3.0 * k_val - 2.0 * d_val
    return k_val, d_val, j_val


# ── DMI / ADX ───────────────────────────────────────────────────


def get_dmi(
    bars: list[dict], period: int = 14
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Directional Movement Index: (+DI, -DI, ADX)."""
    n = len(bars)
    if not bars or n < period + 1:
        return None, None, None

    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []
    tr_list: list[float] = []

    for i in range(1, n):
        h, l = bars[i]["high"], bars[i]["low"]
        ph, pl, pc = bars[i - 1]["high"], bars[i - 1]["low"], bars[i - 1]["close"]
        up_move = h - ph
        down_move = pl - l
        plus_dm_list.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm_list.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        tr_list.append(max(h - l, abs(h - pc), abs(pc - l)))

    if len(tr_list) < period:
        return None, None, None

    smooth_plus = sum(plus_dm_list[:period])
    smooth_minus = sum(minus_dm_list[:period])
    smooth_tr = sum(tr_list[:period])
    dx_list: list[float] = []

    for i in range(period, len(tr_list)):
        smooth_plus = smooth_plus - smooth_plus / period + plus_dm_list[i]
        smooth_minus = smooth_minus - smooth_minus / period + minus_dm_list[i]
        smooth_tr = smooth_tr - smooth_tr / period + tr_list[i]
        if smooth_tr == 0:
            continue
        pdi = 100.0 * smooth_plus / smooth_tr
        mdi = 100.0 * smooth_minus / smooth_tr
        di_sum = pdi + mdi
        dx_list.append(100.0 * abs(pdi - mdi) / di_sum if di_sum else 0.0)

    if not dx_list or smooth_tr == 0:
        return None, None, None

    plus_di = 100.0 * smooth_plus / smooth_tr
    minus_di = 100.0 * smooth_minus / smooth_tr
    adx = sum(dx_list[-period:]) / min(len(dx_list), period)
    return plus_di, minus_di, adx


# ── Williams %R ─────────────────────────────────────────────────


def get_williams_r(bars: list[dict], period: int = 14) -> Optional[float]:
    """Williams %R oscillator. Range: -100 to 0."""
    if not bars or len(bars) < period:
        return None
    window = bars[-period:]
    highest = max(b["high"] for b in window)
    lowest = min(b["low"] for b in window)
    if highest == lowest:
        return -50.0
    return (highest - bars[-1]["close"]) / (highest - lowest) * -100.0


# ── CCI ─────────────────────────────────────────────────────────


def get_cci(bars: list[dict], period: int = 20) -> Optional[float]:
    """Commodity Channel Index."""
    if not bars or len(bars) < period:
        return None
    window = bars[-period:]
    tp_list = [(b["high"] + b["low"] + b["close"]) / 3.0 for b in window]
    tp_mean = sum(tp_list) / period
    mean_dev = sum(abs(tp - tp_mean) for tp in tp_list) / period
    if mean_dev == 0:
        return 0.0
    return (tp_list[-1] - tp_mean) / (0.015 * mean_dev)


# ── TRIX ────────────────────────────────────────────────────────


def get_trix(bars: list[dict], period: int = 12) -> Optional[float]:
    """TRIX: 1-period percent change of triple-smoothed EMA."""
    if not bars or len(bars) < period * 3 + 1:
        return None
    closes = [b["close"] for b in bars]

    def _ema_series(data: list[float], p: int) -> list[float]:
        k = 2.0 / (p + 1)
        result = [sum(data[:p]) / p]
        for v in data[p:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    ema1 = _ema_series(closes, period)
    ema2 = _ema_series(ema1, period)
    ema3 = _ema_series(ema2, period)
    if len(ema3) < 2 or ema3[-2] == 0:
        return None
    return (ema3[-1] - ema3[-2]) / ema3[-2] * 100.0


# ── OBV ─────────────────────────────────────────────────────────


def get_obv(bars: list[dict]) -> Optional[float]:
    """On Balance Volume."""
    if not bars or len(bars) < 2:
        return None
    obv_val = 0.0
    for i in range(1, len(bars)):
        if bars[i]["close"] > bars[i - 1]["close"]:
            obv_val += bars[i].get("volume", 0)
        elif bars[i]["close"] < bars[i - 1]["close"]:
            obv_val -= bars[i].get("volume", 0)
    return obv_val


# ── VWAP ────────────────────────────────────────────────────────


def get_vwap(bars: list[dict]) -> Optional[float]:
    """Volume Weighted Average Price."""
    if not bars:
        return None
    cum_tp_vol = 0.0
    cum_vol = 0.0
    for b in bars:
        tp = (b["high"] + b["low"] + b["close"]) / 3.0
        vol = b.get("volume", 0)
        cum_tp_vol += tp * vol
        cum_vol += vol
    if cum_vol == 0:
        return None
    return cum_tp_vol / cum_vol


# ── Parabolic SAR ──────────────────────────────────────────────


def get_parabolic_sar(
    bars: list[dict], af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.2
) -> Optional[float]:
    """Parabolic SAR. Returns current SAR value."""
    if not bars or len(bars) < 3:
        return None

    is_long = bars[1]["close"] > bars[0]["close"]
    sar = bars[0]["low"] if is_long else bars[0]["high"]
    ep = bars[0]["high"] if is_long else bars[0]["low"]
    af = af_start

    for i in range(1, len(bars)):
        h, l = bars[i]["high"], bars[i]["low"]
        prev_sar = sar
        sar = prev_sar + af * (ep - prev_sar)

        if is_long:
            if i >= 2:
                sar = min(sar, bars[i - 1]["low"], bars[i - 2]["low"])
            else:
                sar = min(sar, bars[i - 1]["low"])
            if l < sar:
                is_long, sar, ep, af = False, ep, l, af_start
            else:
                if h > ep:
                    ep = h
                    af = min(af + af_step, af_max)
        else:
            if i >= 2:
                sar = max(sar, bars[i - 1]["high"], bars[i - 2]["high"])
            else:
                sar = max(sar, bars[i - 1]["high"])
            if h > sar:
                is_long, sar, ep, af = True, ep, h, af_start
            else:
                if l < ep:
                    ep = l
                    af = min(af + af_step, af_max)

    return sar


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
