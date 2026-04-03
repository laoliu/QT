"""
Technical Screening Strategy.

Screens stocks using classic technical indicators:
  - Moving average alignment (MA5 > MA20 > MA60 = bullish)
  - Price above key MAs (price > MA20 = uptrend)
  - Volume breakout (volume > 2x average)
  - RSI (30-70 range, oversold bounce)
  - MACD golden cross / histogram rising
  - Price near 52-week high (momentum)

Requires K-line price/volume data for computation.
"""

from __future__ import annotations

import math
import sys
from typing import Any, Optional

from .base import BaseScreener, ScreenerResult, register_screener


def _compute_sma(closes: list[float], period: int) -> Optional[float]:
    """Compute simple moving average."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _compute_ema(closes: list[float], period: int) -> Optional[float]:
    """Compute exponential moving average."""
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period  # SMA seed
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def _compute_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Compute RSI (Relative Strength Index)."""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    if len(gains) < period:
        return None
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def _compute_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Compute MACD line, signal line, and histogram."""
    if len(closes) < slow + signal_period:
        return None, None, None

    fast_ema = _compute_ema(closes, fast)
    slow_ema = _compute_ema(closes, slow)
    if fast_ema is None or slow_ema is None:
        return None, None, None

    macd_line = fast_ema - slow_ema

    # Compute MACD line series for signal
    macd_series = []
    for i in range(slow, len(closes) + 1):
        sub = closes[:i]
        fe = _compute_ema(sub, fast)
        se = _compute_ema(sub, slow)
        if fe is not None and se is not None:
            macd_series.append(fe - se)

    if len(macd_series) < signal_period:
        return macd_line, None, None

    signal = sum(macd_series[-signal_period:]) / signal_period
    histogram = macd_line - signal
    return macd_line, signal, histogram


@register_screener
class TechnicalScreener(BaseScreener):
    """Technical indicator-based stock screener."""

    screener_id = "technical"
    screener_name = "技术面筛选"
    screener_description = (
        "基于技术指标的选股策略。"
        "包含均线排列(多头/空头)、RSI超卖反弹、MACD金叉、"
        "放量突破等经典技术信号。需要K线数据。"
    )
    supported_markets = ["cn_stock", "us_stock"]

    _DEFAULTS = {
        "ma_short": 5,
        "ma_mid": 20,
        "ma_long": 60,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "volume_surge_ratio": 2.0,  # Volume > 2x 20-day avg
        "min_bars": 60,  # Minimum K-line bars required
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        """Screen stocks by technical indicators.

        stock_data should include a 'klines' key with list of
        {open, high, low, close, volume} dicts sorted by date ascending.
        """
        thresholds = {**self._DEFAULTS, **self.params}
        results: list[ScreenerResult] = []

        for code, data in stock_data.items():
            klines = data.get("klines", [])
            if len(klines) < thresholds["min_bars"]:
                continue

            closes = [float(k.get("close", 0)) for k in klines if k.get("close")]
            volumes = [float(k.get("volume", 0)) for k in klines if k.get("volume")]

            if len(closes) < thresholds["min_bars"]:
                continue

            score = 0.0
            signals: list[str] = []
            factors: dict[str, Any] = {}

            current_price = closes[-1]
            factors["price"] = current_price

            # 1. Moving average alignment
            ma_s = _compute_sma(closes, thresholds["ma_short"])
            ma_m = _compute_sma(closes, thresholds["ma_mid"])
            ma_l = _compute_sma(closes, thresholds["ma_long"])

            if ma_s and ma_m and ma_l:
                factors["ma5"] = round(ma_s, 2)
                factors["ma20"] = round(ma_m, 2)
                factors["ma60"] = round(ma_l, 2)

                if ma_s > ma_m > ma_l:
                    score += 2.0
                    signals.append("均线多头排列(MA5>MA20>MA60)")
                elif ma_s > ma_m:
                    score += 1.0
                    signals.append("短中期均线多头(MA5>MA20)")

                # Price above MA20 (uptrend)
                if current_price > ma_m:
                    score += 0.5
                    signals.append("价格在MA20之上")

            # 2. RSI
            rsi = _compute_rsi(closes)
            if rsi is not None:
                factors["rsi"] = round(rsi, 2)
                if rsi < thresholds["rsi_oversold"]:
                    score += 1.5
                    signals.append(f"RSI超卖({rsi:.1f}<{thresholds['rsi_oversold']})")
                elif rsi > thresholds["rsi_overbought"]:
                    score -= 0.5  # Penalty for overbought
                    signals.append(f"RSI超买({rsi:.1f}>{thresholds['rsi_overbought']})")
                elif 40 <= rsi <= 60:
                    score += 0.3  # Neutral zone OK

            # 3. MACD
            macd_line, signal_line, histogram = _compute_macd(closes)
            if macd_line is not None and signal_line is not None and histogram is not None:
                factors["macd"] = round(macd_line, 4)
                factors["macd_signal"] = round(signal_line, 4)
                factors["macd_hist"] = round(histogram, 4)

                if histogram > 0 and macd_line > signal_line:
                    score += 1.0
                    signals.append("MACD金叉/柱状线为正")
                    # Check if histogram is increasing
                    if len(closes) > 2:
                        prev_hist = _compute_macd(closes[:-1])
                        if prev_hist[2] is not None and histogram > prev_hist[2]:
                            score += 0.5
                            signals.append("MACD柱状线递增")

            # 4. Volume analysis
            if len(volumes) >= 20:
                avg_vol_20 = sum(volumes[-20:]) / 20
                current_vol = volumes[-1]
                if avg_vol_20 > 0:
                    vol_ratio = current_vol / avg_vol_20
                    factors["volume_ratio"] = round(vol_ratio, 2)
                    if vol_ratio >= thresholds["volume_surge_ratio"]:
                        score += 1.0
                        signals.append(f"放量(成交量是20日均量的{vol_ratio:.1f}倍)")

            # 5. Price near 52-week high (momentum)
            if len(closes) >= 250:
                high_52w = max(closes[-250:])
                pct_from_high = current_price / high_52w
                factors["pct_from_52w_high"] = round(pct_from_high * 100, 2)
                if pct_from_high >= 0.90:
                    score += 0.5
                    signals.append(f"价格接近52周高点({pct_from_high * 100:.1f}%)")

            if score >= 2.0:
                results.append(ScreenerResult(
                    stock_id=code,
                    name=data.get("name", ""),
                    market=market,
                    score=score,
                    factors=factors,
                    signals=signals,
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results[:limit]):
            r.rank = i + 1
        return results[:limit]
