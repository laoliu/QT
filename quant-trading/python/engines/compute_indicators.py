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
