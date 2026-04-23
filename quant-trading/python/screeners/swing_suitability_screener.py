"""
Swing Suitability Screener (持仓波段适合度评估).

Evaluates whether a currently-held stock is suitable for swing overlay
trading (高抛低吸), based on:
  1. Trend score — MA60 slope positive + price above MA60 (uptrend)
  2. Oscillation score — ATR%, Bollinger bandwidth, BIAS range
  3. Mean-reversion tendency — returns autocorrelation < 0
  4. Amplitude score — (swing_high - swing_low) / avg_price
  5. Liquidity — sufficient daily volume for swing-portion trades

Stocks with a high suitability score exhibit both upward trend and
regular oscillation — ideal for extracting swing profits while
holding a core position.
"""

from __future__ import annotations

import math
import sys
from typing import Any, Optional

from .base import BaseScreener, ScreenerResult, register_screener


def _sma(values: list[float], period: int) -> list[float]:
    """Simple moving average (returns NaN for indices < period-1)."""
    n = len(values)
    result = [float("nan")] * n
    if n < period:
        return result
    window_sum = 0.0
    for i in range(n):
        window_sum += values[i]
        if i >= period:
            window_sum -= values[i - period]
        if i >= period - 1:
            result[i] = window_sum / period
    return result


def _std(values: list[float], period: int) -> list[float]:
    """Rolling standard deviation."""
    n = len(values)
    result = [float("nan")] * n
    if n < period:
        return result
    for i in range(period - 1, n):
        window = values[i - period + 1: i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        result[i] = math.sqrt(variance)
    return result


def _atr_series(highs: list[float], lows: list[float], closes: list[float],
                period: int = 14) -> list[float]:
    """Compute ATR series."""
    n = len(closes)
    trs: list[float] = [0.0] * n
    for i in range(n):
        if i == 0:
            trs[i] = highs[i] - lows[i]
        else:
            trs[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
    return _sma(trs, period)


def _returns(closes: list[float]) -> list[float]:
    """Daily returns series."""
    result: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            result.append((closes[i] - closes[i - 1]) / closes[i - 1])
        else:
            result.append(0.0)
    return result


def _autocorrelation_lag1(data: list[float]) -> float:
    """Lag-1 autocorrelation of a series. Negative → mean-reversion tendency."""
    n = len(data)
    if n < 10:
        return 0.0
    mean = sum(data) / n
    var = sum((x - mean) ** 2 for x in data) / n
    if var < 1e-12:
        return 0.0
    cov = sum((data[i] - mean) * (data[i - 1] - mean) for i in range(1, n)) / (n - 1)
    return cov / var


@register_screener
class SwingSuitabilityScreener(BaseScreener):
    """Evaluate holdings for swing overlay trading suitability."""

    screener_id = "swing_suitability"
    screener_name = "持仓波段适合度 (Swing Suitability)"
    screener_description = (
        "评估已持有股票是否适合波段操作（高抛低吸）。"
        "综合趋势强度、振荡幅度、均值回归倾向和流动性进行打分。"
        "高分股票具有整体上涨但中间有规律波动的特征。"
    )
    supported_markets = ["cn_stock"]

    _DEFAULTS = {
        "lookback": 60,          # bars for analysis
        "ma_long": 60,           # long MA for trend detection
        "ma_short": 20,          # short MA for oscillation center
        "min_amplitude_pct": 3,  # minimum swing amplitude %
        "min_volume": 500_000,   # minimum avg daily volume (shares)
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        """Evaluate each stock's swing suitability.

        stock_data format:
          {code: {name, klines: [{close, high, low, volume, ...}], ...}}

        The 'klines' field should contain at least 60 daily bars.
        """
        lookback = self.params.get("lookback", self._DEFAULTS["lookback"])
        ma_long_period = self.params.get("ma_long", self._DEFAULTS["ma_long"])
        ma_short_period = self.params.get("ma_short", self._DEFAULTS["ma_short"])
        min_amp = self.params.get("min_amplitude_pct", self._DEFAULTS["min_amplitude_pct"])
        min_vol = self.params.get("min_volume", self._DEFAULTS["min_volume"])

        results: list[ScreenerResult] = []

        for code, info in stock_data.items():
            name = info.get("name", code)
            klines = info.get("klines", [])
            if not klines or len(klines) < max(ma_long_period, lookback):
                continue

            # Extract OHLCV arrays
            closes = [float(k.get("close", 0)) for k in klines]
            highs = [float(k.get("high", 0)) for k in klines]
            lows = [float(k.get("low", 0)) for k in klines]
            volumes = [float(k.get("volume", 0)) for k in klines]

            if closes[-1] <= 0:
                continue

            try:
                score, factors, signals = self._evaluate(
                    code, closes, highs, lows, volumes,
                    lookback, ma_long_period, ma_short_period, min_amp, min_vol,
                )
            except Exception as e:
                print(f"[swing_suitability] {code} error: {e}", file=sys.stderr)
                continue

            if score <= 0:
                continue

            results.append(ScreenerResult(
                stock_id=code,
                name=name,
                market=market,
                score=score,
                factors=factors,
                signals=signals,
            ))

        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results[:limit]):
            r.rank = i + 1
        return results[:limit]

    def _evaluate(
        self,
        code: str,
        closes: list[float],
        highs: list[float],
        lows: list[float],
        volumes: list[float],
        lookback: int,
        ma_long_period: int,
        ma_short_period: int,
        min_amp: float,
        min_vol: float,
    ) -> tuple[float, dict[str, Any], list[str]]:
        """Compute suitability score for a single stock."""
        n = len(closes)
        signals: list[str] = []
        factors: dict[str, Any] = {}

        # ── 1. Trend Score (0-30) ──
        ma60 = _sma(closes, ma_long_period)
        ma20 = _sma(closes, ma_short_period)

        # MA60 slope over last 20 bars
        trend_score = 0.0
        if not math.isnan(ma60[-1]) and not math.isnan(ma60[-21]) and ma60[-21] > 0:
            slope = (ma60[-1] - ma60[-21]) / ma60[-21] * 100
            factors["ma60_slope_pct"] = round(slope, 2)
            if slope > 0:
                trend_score += min(slope * 5, 15)  # up to 15 pts for strong uptrend
                signals.append(f"MA60上升趋势 +{slope:.1f}%")
            else:
                signals.append(f"⚠️ MA60下降 {slope:.1f}%")
                # Still allow if recent MA20 is rising
                if not math.isnan(ma20[-1]) and not math.isnan(ma20[-11]) and ma20[-11] > 0:
                    slope20 = (ma20[-1] - ma20[-11]) / ma20[-11] * 100
                    if slope20 > 0:
                        trend_score += min(slope20 * 2, 5)

        # Price above MA60
        if not math.isnan(ma60[-1]) and closes[-1] > ma60[-1]:
            premium = (closes[-1] - ma60[-1]) / ma60[-1] * 100
            trend_score += min(premium * 2, 15)
            factors["price_above_ma60_pct"] = round(premium, 2)
            signals.append(f"价格在MA60上方 +{premium:.1f}%")
        else:
            factors["price_above_ma60_pct"] = 0

        # ── 2. Oscillation Score (0-30) ──
        osc_score = 0.0

        # ATR% (ATR / close) — higher = more oscillation
        atr_vals = _atr_series(highs, lows, closes, 14)
        if not math.isnan(atr_vals[-1]) and closes[-1] > 0:
            atr_pct = atr_vals[-1] / closes[-1] * 100
            factors["atr_pct"] = round(atr_pct, 2)
            # Sweet spot: 1.5% - 5% ATR is good for swing; too high = risky
            if 1.5 <= atr_pct <= 5.0:
                osc_score += 15
                signals.append(f"ATR%={atr_pct:.1f}% 振荡适中")
            elif atr_pct > 5.0:
                osc_score += 8  # too volatile but still workable
                signals.append(f"ATR%={atr_pct:.1f}% 波动偏大")
            elif atr_pct > 0.8:
                osc_score += atr_pct * 5  # low but some oscillation
                signals.append(f"ATR%={atr_pct:.1f}% 波动偏小")

        # Bollinger bandwidth
        std_vals = _std(closes, ma_short_period)
        if not math.isnan(std_vals[-1]) and not math.isnan(ma20[-1]) and ma20[-1] > 0:
            bb_width = (std_vals[-1] * 4) / ma20[-1] * 100  # 2-sigma band width
            factors["bollinger_bandwidth_pct"] = round(bb_width, 2)
            if bb_width >= 4:
                osc_score += min(bb_width * 1.5, 15)
                signals.append(f"布林带宽 {bb_width:.1f}%")

        # ── 3. Mean-Reversion Tendency (0-20) ──
        mr_score = 0.0
        rets = _returns(closes[-lookback:])
        if len(rets) >= 10:
            ac1 = _autocorrelation_lag1(rets)
            factors["autocorrelation_lag1"] = round(ac1, 4)
            if ac1 < 0:
                # Negative autocorrelation → mean-reversion tendency
                mr_score = min(abs(ac1) * 100, 20)
                signals.append(f"均值回归倾向 AC1={ac1:.3f}")
            else:
                signals.append(f"趋势持续倾向 AC1={ac1:.3f}")

        # ── 4. Amplitude Score (0-15) ──
        amp_score = 0.0
        recent_highs = highs[-lookback:]
        recent_lows = lows[-lookback:]
        recent_closes = closes[-lookback:]
        if recent_closes:
            avg_price = sum(recent_closes) / len(recent_closes)
            max_high = max(recent_highs)
            min_low = min(recent_lows)
            if avg_price > 0:
                amplitude = (max_high - min_low) / avg_price * 100
                factors["amplitude_pct"] = round(amplitude, 2)
                if amplitude >= min_amp:
                    amp_score = min(amplitude * 0.75, 15)
                    signals.append(f"振幅 {amplitude:.1f}%")
                else:
                    signals.append(f"⚠️ 振幅不足 {amplitude:.1f}% (需>{min_amp}%)")

        # ── 5. Liquidity Check (0-5, penalty if insufficient) ──
        liq_score = 0.0
        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
        factors["avg_volume_20d"] = int(avg_vol)
        if avg_vol >= min_vol:
            liq_score = 5
        elif avg_vol > 0:
            liq_score = max(0, 5 * (avg_vol / min_vol))
            signals.append(f"⚠️ 流动性偏低 (日均{avg_vol:.0f}股)")

        # ── Composite Score ──
        total = trend_score + osc_score + mr_score + amp_score + liq_score
        factors["trend_score"] = round(trend_score, 1)
        factors["oscillation_score"] = round(osc_score, 1)
        factors["mean_reversion_score"] = round(mr_score, 1)
        factors["amplitude_score"] = round(amp_score, 1)
        factors["liquidity_score"] = round(liq_score, 1)

        # Recommended parameters based on characteristics
        if total > 30:
            atr_pct_val = factors.get("atr_pct", 2.0)
            factors["recommended_grid_pct"] = round(max(1.5, min(atr_pct_val * 1.2, 6.0)), 1)
            factors["recommended_core_ratio"] = round(
                0.7 if atr_pct_val > 3.5 else 0.6, 2
            )
            factors["recommended_bias_buy"] = round(
                max(-10, -(atr_pct_val * 2.5)), 1
            )
            factors["recommended_bias_sell"] = round(
                min(8, atr_pct_val * 1.8), 1
            )

        return total, factors, signals
