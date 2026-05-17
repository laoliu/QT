"""
Volume-Price Anomaly Screener.

Identifies unusual volume-price patterns that often precede significant moves:
  - Institutional accumulation: rising price on increasing volume
  - Breakout setup: price consolidation then volume spike
  - Divergence: price new low but volume shrinking (selling exhaustion)
  - Bottom reversal: sharp volume increase at multi-month lows

Based on Wyckoff methodology and volume spread analysis (VSA) principles.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class VolumePriceScreener(BaseScreener):
    """Volume-price anomaly detection screener."""

    screener_id = "volume_price"
    screener_name = "量价异动筛选"
    screener_description = (
        "量价异动筛选策略。检测机构建仓(温和放量上涨)、"
        "突破信号(缩量盘整后放量突破)、底部反转(地量见地价)等模式。"
        "基于Wyckoff/VSA方法论。需要K线数据。"
    )
    supported_markets = ["cn_stock", "us_stock"]

    _DEFAULTS = {
        "lookback_days": 20,     # Analysis window
        "volume_spike": 2.0,     # Volume spike threshold (lowered from 2.5)
        "consolidation_days": 10,  # Min consolidation days
        "consolidation_range": 0.08,  # Max price range during consolidation (8%)
        "min_bars": 60,
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        thresholds = {**self._DEFAULTS, **self.params}
        results: list[ScreenerResult] = []

        for code, data in stock_data.items():
            klines = data.get("klines", [])
            if len(klines) < thresholds["min_bars"]:
                continue

            closes = [float(k.get("close", 0)) for k in klines if k.get("close")]
            volumes = [float(k.get("volume", 0)) for k in klines if k.get("volume")]
            highs = [float(k.get("high", 0)) for k in klines if k.get("high")]
            lows = [float(k.get("low", 0)) for k in klines if k.get("low")]

            if len(closes) < thresholds["min_bars"]:
                continue

            score = 0.0
            signals: list[str] = []
            factors: dict[str, Any] = {"price": closes[-1]}

            lb = thresholds["lookback_days"]
            avg_vol = sum(volumes[-lb:]) / lb if len(volumes) >= lb else sum(volumes) / len(volumes)
            if avg_vol <= 0:
                continue

            # 1. Institutional accumulation: rising price + rising volume trend
            acc_score = self._check_accumulation(closes, volumes, lb)
            if acc_score > 0:
                score += acc_score
                signals.append("机构建仓迹象(温和放量上涨)")
                factors["accumulation_score"] = round(acc_score, 2)

            # 2. Volume spike (today's volume >> average)
            if volumes:
                latest_vol = volumes[-1]
                vol_ratio = latest_vol / avg_vol
                factors["volume_ratio"] = round(vol_ratio, 2)
                if vol_ratio >= thresholds["volume_spike"]:
                    # Check if it's buying (close > open)
                    if len(klines) > 0:
                        last_k = klines[-1]
                        if float(last_k.get("close", 0)) > float(last_k.get("open", 0)):
                            score += 2.0
                            signals.append(f"放量阳线(量比{vol_ratio:.1f})")
                        else:
                            score += 0.5
                            signals.append(f"放量阴线(量比{vol_ratio:.1f})")
                elif vol_ratio >= 1.5:
                    # Moderate volume increase
                    score += 0.5
                    signals.append(f"温和放量(量比{vol_ratio:.1f})")

            # 3. Breakout after consolidation
            breakout = self._check_breakout(closes, volumes, highs, lows, thresholds)
            if breakout > 0:
                score += breakout
                signals.append("盘整后突破信号")
                factors["breakout_score"] = round(breakout, 2)

            # 4. Volume-price divergence (bearish exhaustion → potential reversal)
            div_score = self._check_divergence(closes, volumes, lb)
            if div_score > 0:
                score += div_score
                signals.append("量价底背离(卖压衰竭)")
                factors["divergence_score"] = round(div_score, 2)

            # 5. Bottom reversal: price at multi-month low + sudden volume
            bottom = self._check_bottom_reversal(closes, volumes, lb)
            if bottom > 0:
                score += bottom
                signals.append("底部放量反转信号")
                factors["bottom_reversal_score"] = round(bottom, 2)

            if score >= 1.5:
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

    @staticmethod
    def _check_accumulation(
        closes: list[float], volumes: list[float], lookback: int
    ) -> float:
        """Check for institutional accumulation (rising price + rising volume)."""
        if len(closes) < lookback or len(volumes) < lookback:
            return 0
        half = lookback // 2
        price_first_half = sum(closes[-lookback:-half]) / half
        price_second_half = sum(closes[-half:]) / half
        vol_first_half = sum(volumes[-lookback:-half]) / half
        vol_second_half = sum(volumes[-half:]) / half

        price_rising = price_second_half > price_first_half * 1.02  # >2% rise
        vol_rising = vol_second_half > vol_first_half * 1.15  # >15% more volume

        if price_rising and vol_rising:
            return 1.5
        elif price_rising:
            return 0.3
        return 0

    @staticmethod
    def _check_breakout(
        closes: list[float],
        volumes: list[float],
        highs: list[float],
        lows: list[float],
        thresholds: dict,
    ) -> float:
        """Check for breakout after consolidation."""
        consol_days = thresholds["consolidation_days"]
        max_range = thresholds["consolidation_range"]

        if len(closes) < consol_days + 5:
            return 0

        # Check if prior period was consolidation
        consol_highs = highs[-(consol_days + 1):-1]
        consol_lows = lows[-(consol_days + 1):-1]
        if not consol_highs or not consol_lows:
            return 0

        h = max(consol_highs)
        l = min(consol_lows)
        if l <= 0:
            return 0
        price_range = (h - l) / l

        if price_range > max_range:
            return 0  # Not consolidated enough

        # Check if latest close breaks above consolidation high
        if closes[-1] > h * 1.01:  # 1% above range
            # Volume confirmation
            avg_vol = sum(volumes[-(consol_days + 1):-1]) / consol_days
            if avg_vol > 0 and volumes[-1] > avg_vol * 1.5:
                return 2.5  # Strong breakout
            return 1.0  # Weak breakout (low volume)

        return 0

    @staticmethod
    def _check_divergence(
        closes: list[float], volumes: list[float], lookback: int
    ) -> float:
        """Check for bullish divergence (price falling but volume shrinking)."""
        if len(closes) < lookback:
            return 0
        half = lookback // 2

        price_falling = closes[-1] < closes[-lookback]
        vol_first = sum(volumes[-lookback:-half]) / max(half, 1)
        vol_second = sum(volumes[-half:]) / max(half, 1)
        vol_shrinking = vol_second < vol_first * 0.7  # Volume down 30%

        if price_falling and vol_shrinking:
            return 1.5
        return 0

    @staticmethod
    def _check_bottom_reversal(
        closes: list[float], volumes: list[float], lookback: int
    ) -> float:
        """Check for bottom reversal: price near low + sudden volume."""
        if len(closes) < lookback * 3:
            return 0

        # Price near 60-day low
        low_60 = min(closes[-60:])
        if low_60 <= 0:
            return 0
        pct_from_low = (closes[-1] - low_60) / low_60

        if pct_from_low > 0.10:  # Too far from low
            return 0

        # Volume spike at low
        avg_vol = sum(volumes[-20:-1]) / 19 if len(volumes) > 20 else sum(volumes) / len(volumes)
        if avg_vol > 0 and volumes[-1] > avg_vol * 2:
            # Bullish candle at bottom
            return 2.0

        return 0
