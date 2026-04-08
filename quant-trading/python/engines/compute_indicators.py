"""
compute_indicators — compute technical indicators for a K-line series.

Called from TypeScript market_data tool via python-bridge.
Input (via QUANT_CONFIG env or stdin):
  { "bars": [{"datetime": "20210101", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100}, ...] }

Output (JSON):
  { "ma5": ..., "ma10": ..., "ma20": ..., "ma60": ..., "ma120": ..., "ma250": ...,
    "ema12": ..., "ema26": ..., "rsi14": ..., "atr14": ...,
    "volatility": ..., "high52w": ..., "low52w": ...,
    "macd": ..., "macd_signal": ..., "macd_hist": ... }
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Optional


def sma(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def ema(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(closes[:period]) / period
    for c in closes[period:]:
        e = c * k + e * (1 - k)
    return e


def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    recent = closes[-(period + 1):]
    gains = 0.0
    losses = 0.0
    for i in range(1, len(recent)):
        d = recent[i] - recent[i - 1]
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


def atr(bars: list[dict], period: int = 14) -> Optional[float]:
    n = len(bars)
    if n < period + 1:
        return None
    tr_sum = 0.0
    for i in range(n - period, n):
        h = bars[i]["high"]
        l = bars[i]["low"]
        pc = bars[i - 1]["close"]
        tr_sum += max(h - l, abs(h - pc), abs(pc - l))
    return tr_sum / period


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD line, signal line, histogram."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None

    # Compute full MACD line series for signal line calculation
    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)

    ef = sum(closes[:fast]) / fast
    es = sum(closes[:slow]) / slow

    macd_series: list[float] = []
    for i, c in enumerate(closes):
        if i < fast:
            ef = sum(closes[:i + 1]) / (i + 1)
        else:
            ef = c * k_fast + ef * (1 - k_fast)
        if i < slow:
            es = sum(closes[:i + 1]) / (i + 1)
        else:
            es = c * k_slow + es * (1 - k_slow)
        if i >= slow - 1:
            macd_series.append(ef - es)

    if len(macd_series) < signal:
        return ema_fast - ema_slow, None, None

    # Signal line (EMA of MACD)
    k_sig = 2.0 / (signal + 1)
    sig = sum(macd_series[:signal]) / signal
    for m in macd_series[signal:]:
        sig = m * k_sig + sig * (1 - k_sig)

    macd_val = macd_series[-1]
    hist = macd_val - sig
    return macd_val, sig, hist


def bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Bollinger Bands: upper, middle, lower."""
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = math.sqrt(variance)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def kdj(
    bars: list[dict], k_period: int = 9, d_period: int = 3, j_smooth: int = 3
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """KDJ Stochastic Oscillator: K, D, J values."""
    n = len(bars)
    if n < k_period:
        return None, None, None

    # Compute RSV series
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

    # K = SMA of RSV
    k_series: list[float] = []
    for i in range(len(rsv_series)):
        if i < d_period - 1:
            k_series.append(sum(rsv_series[: i + 1]) / (i + 1))
        else:
            k_series.append(sum(rsv_series[i - d_period + 1 : i + 1]) / d_period)

    if len(k_series) < j_smooth:
        return None, None, None

    # D = SMA of K
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


def dmi(
    bars: list[dict], period: int = 14
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Directional Movement Index: +DI, -DI, ADX."""
    n = len(bars)
    if n < period + 1:
        return None, None, None

    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []
    tr_list: list[float] = []

    for i in range(1, n):
        h = bars[i]["high"]
        l = bars[i]["low"]
        ph = bars[i - 1]["high"]
        pl = bars[i - 1]["low"]
        pc = bars[i - 1]["close"]

        up_move = h - ph
        down_move = pl - l

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr = max(h - l, abs(h - pc), abs(pc - l))

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)

    if len(tr_list) < period:
        return None, None, None

    # Wilder's smoothing (initial sum then recursive)
    smooth_plus_dm = sum(plus_dm_list[:period])
    smooth_minus_dm = sum(minus_dm_list[:period])
    smooth_tr = sum(tr_list[:period])

    dx_list: list[float] = []

    for i in range(period, len(tr_list)):
        smooth_plus_dm = smooth_plus_dm - smooth_plus_dm / period + plus_dm_list[i]
        smooth_minus_dm = smooth_minus_dm - smooth_minus_dm / period + minus_dm_list[i]
        smooth_tr = smooth_tr - smooth_tr / period + tr_list[i]

        if smooth_tr == 0:
            continue
        plus_di = 100.0 * smooth_plus_dm / smooth_tr
        minus_di = 100.0 * smooth_minus_dm / smooth_tr
        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx_list.append(0.0)
        else:
            dx_list.append(100.0 * abs(plus_di - minus_di) / di_sum)

    if not dx_list:
        return None, None, None

    # Final +DI, -DI
    if smooth_tr == 0:
        return None, None, None
    final_plus_di = 100.0 * smooth_plus_dm / smooth_tr
    final_minus_di = 100.0 * smooth_minus_dm / smooth_tr

    # ADX = SMA of DX over period
    if len(dx_list) < period:
        adx = sum(dx_list) / len(dx_list)
    else:
        adx = sum(dx_list[-period:]) / period

    return final_plus_di, final_minus_di, adx


def williams_r(bars: list[dict], period: int = 14) -> Optional[float]:
    """Williams %R oscillator. Range: -100 to 0."""
    n = len(bars)
    if n < period:
        return None
    window = bars[-period:]
    highest = max(b["high"] for b in window)
    lowest = min(b["low"] for b in window)
    if highest == lowest:
        return -50.0
    close = bars[-1]["close"]
    return (highest - close) / (highest - lowest) * -100.0


def cci(bars: list[dict], period: int = 20) -> Optional[float]:
    """Commodity Channel Index."""
    n = len(bars)
    if n < period:
        return None
    window = bars[-period:]
    tp_list = [(b["high"] + b["low"] + b["close"]) / 3.0 for b in window]
    tp_mean = sum(tp_list) / period
    mean_dev = sum(abs(tp - tp_mean) for tp in tp_list) / period
    if mean_dev == 0:
        return 0.0
    return (tp_list[-1] - tp_mean) / (0.015 * mean_dev)


def trix(closes: list[float], period: int = 12) -> Optional[float]:
    """TRIX: 1-period percent change of triple-smoothed EMA."""
    # Need enough data for 3x EMA smoothing
    min_len = period * 3 + 1
    if len(closes) < min_len:
        return None

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


def obv(bars: list[dict]) -> Optional[float]:
    """On Balance Volume. Returns cumulative OBV value."""
    if len(bars) < 2:
        return None
    obv_val = 0.0
    for i in range(1, len(bars)):
        if bars[i]["close"] > bars[i - 1]["close"]:
            obv_val += bars[i].get("volume", 0)
        elif bars[i]["close"] < bars[i - 1]["close"]:
            obv_val -= bars[i].get("volume", 0)
    return obv_val


def vwap(bars: list[dict]) -> Optional[float]:
    """Volume Weighted Average Price (intraday/session-based)."""
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


def parabolic_sar(
    bars: list[dict], af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.2
) -> Optional[float]:
    """Parabolic SAR. Returns current SAR value."""
    n = len(bars)
    if n < 3:
        return None

    # Initialize
    is_long = bars[1]["close"] > bars[0]["close"]
    sar = bars[0]["low"] if is_long else bars[0]["high"]
    ep = bars[0]["high"] if is_long else bars[0]["low"]
    af = af_start

    for i in range(1, n):
        h = bars[i]["high"]
        l = bars[i]["low"]

        prev_sar = sar
        sar = prev_sar + af * (ep - prev_sar)

        if is_long:
            # Clamp SAR to recent lows
            if i >= 2:
                sar = min(sar, bars[i - 1]["low"], bars[i - 2]["low"])
            else:
                sar = min(sar, bars[i - 1]["low"])

            if l < sar:
                # Flip to short
                is_long = False
                sar = ep
                ep = l
                af = af_start
            else:
                if h > ep:
                    ep = h
                    af = min(af + af_step, af_max)
        else:
            # Clamp SAR to recent highs
            if i >= 2:
                sar = max(sar, bars[i - 1]["high"], bars[i - 2]["high"])
            else:
                sar = max(sar, bars[i - 1]["high"])

            if h > sar:
                # Flip to long
                is_long = True
                sar = ep
                ep = h
                af = af_start
            else:
                if l < ep:
                    ep = l
                    af = min(af + af_step, af_max)

    return sar


def history_volatility(closes: list[float]) -> Optional[float]:
    if len(closes) < 20:
        return None
    log_returns = []
    for i in range(1, len(closes)):
        if closes[i] > 0 and closes[i - 1] > 0:
            log_returns.append(math.log(closes[i] / closes[i - 1]))
    if len(log_returns) < 2:
        return None
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / len(log_returns)
    std_dev = math.sqrt(variance)
    return std_dev * math.sqrt(252)  # annualized


def compute(config: dict) -> dict:
    bars = config.get("bars", [])
    if not bars:
        return {"error": "no bars provided"}

    closes = [b["close"] for b in bars]
    n = len(closes)

    # 52-week high/low
    lookback = min(n, 250)
    recent = bars[-lookback:]
    high52w = max(b["high"] for b in recent) if recent else None
    low52w = min(b["low"] for b in recent) if recent else None

    # MACD
    macd_val, macd_sig, macd_hist_val = macd(closes)

    def r2(v: Optional[float]) -> Optional[float]:
        return round(v, 2) if v is not None else None

    def r4(v: Optional[float]) -> Optional[float]:
        return round(v, 4) if v is not None else None

    vol = history_volatility(closes)

    # Bollinger Bands
    bb_upper, bb_mid, bb_lower = bollinger_bands(closes)

    # KDJ
    k_val, d_val, j_val = kdj(bars)

    # DMI / ADX
    plus_di, minus_di, adx_val = dmi(bars)

    # Williams %R
    wr_val = williams_r(bars)

    # CCI
    cci_val = cci(bars)

    # TRIX
    trix_val = trix(closes)

    # OBV
    obv_val = obv(bars)

    # VWAP
    vwap_val = vwap(bars)

    # Parabolic SAR
    sar_val = parabolic_sar(bars)

    return {
        "ma5": r2(sma(closes, 5)),
        "ma10": r2(sma(closes, 10)),
        "ma20": r2(sma(closes, 20)),
        "ma60": r2(sma(closes, 60)),
        "ma120": r2(sma(closes, 120)),
        "ma250": r2(sma(closes, 250)),
        "ema12": r2(ema(closes, 12)),
        "ema26": r2(ema(closes, 26)),
        "rsi14": r2(rsi(closes, 14)),
        "atr14": r2(atr(bars, 14)),
        "macd": r4(macd_val),
        "macd_signal": r4(macd_sig),
        "macd_hist": r4(macd_hist_val),
        "bb_upper": r2(bb_upper),
        "bb_mid": r2(bb_mid),
        "bb_lower": r2(bb_lower),
        "kdj_k": r2(k_val),
        "kdj_d": r2(d_val),
        "kdj_j": r2(j_val),
        "dmi_plus": r2(plus_di),
        "dmi_minus": r2(minus_di),
        "adx": r2(adx_val),
        "williams_r": r2(wr_val),
        "cci": r2(cci_val),
        "trix": r4(trix_val),
        "obv": r2(obv_val),
        "vwap": r2(vwap_val),
        "sar": r2(sar_val),
        "volatility": round(vol * 100, 2) if vol is not None else None,  # as percentage
        "high52w": r2(high52w),
        "low52w": r2(low52w),
    }


def main():
    config_str = os.environ.get("QUANT_CONFIG", "")
    if not config_str:
        config_str = sys.stdin.read()
    config = json.loads(config_str) if config_str else {}
    result = compute(config)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
