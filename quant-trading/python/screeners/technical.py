"""
Technical Screening Strategy.

Screens stocks using classic technical indicators:
  - Moving average alignment (MA5 > MA20 > MA60 = bullish)
  - Price above key MAs (price > MA20 = uptrend)
  - Volume breakout (volume > 2x average)
  - RSI (30-70 range, oversold bounce)
  - MACD golden cross / histogram rising
  - Bollinger Bands (price near lower band = oversold)
  - KDJ (oversold/overbought stochastic)
  - DMI/ADX (trend strength confirmation)
  - Williams %R (overbought/oversold)
  - CCI (commodity channel index)
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


def _compute_bollinger(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Compute Bollinger Bands: upper, middle, lower."""
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = math.sqrt(variance)
    return mid + num_std * std, mid, mid - num_std * std


def _compute_kdj(
    klines: list[dict], k_period: int = 9, d_period: int = 3, j_smooth: int = 3
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Compute KDJ: K, D, J values."""
    n = len(klines)
    if n < k_period:
        return None, None, None

    rsv_series: list[float] = []
    for i in range(k_period - 1, n):
        window = klines[i - k_period + 1 : i + 1]
        highest = max(float(b.get("high", 0)) for b in window)
        lowest = min(float(b.get("low", 0)) for b in window)
        close = float(klines[i].get("close", 0))
        if highest == lowest:
            rsv_series.append(50.0)
        else:
            rsv_series.append((close - lowest) / (highest - lowest) * 100)

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


def _compute_dmi(
    klines: list[dict], period: int = 14
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Compute DMI: +DI, -DI, ADX."""
    n = len(klines)
    if n < period + 1:
        return None, None, None

    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []
    tr_list: list[float] = []

    for i in range(1, n):
        h = float(klines[i].get("high", 0))
        l = float(klines[i].get("low", 0))
        ph = float(klines[i - 1].get("high", 0))
        pl = float(klines[i - 1].get("low", 0))
        pc = float(klines[i - 1].get("close", 0))

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


def _compute_williams_r(klines: list[dict], period: int = 14) -> Optional[float]:
    """Compute Williams %R. Range: -100 to 0."""
    if len(klines) < period:
        return None
    window = klines[-period:]
    highest = max(float(b.get("high", 0)) for b in window)
    lowest = min(float(b.get("low", 0)) for b in window)
    close = float(klines[-1].get("close", 0))
    if highest == lowest:
        return -50.0
    return (highest - close) / (highest - lowest) * -100.0


def _compute_cci(klines: list[dict], period: int = 20) -> Optional[float]:
    """Compute Commodity Channel Index."""
    if len(klines) < period:
        return None
    window = klines[-period:]
    tp_list = [
        (float(b.get("high", 0)) + float(b.get("low", 0)) + float(b.get("close", 0))) / 3.0
        for b in window
    ]
    tp_mean = sum(tp_list) / period
    mean_dev = sum(abs(tp - tp_mean) for tp in tp_list) / period
    if mean_dev == 0:
        return 0.0
    return (tp_list[-1] - tp_mean) / (0.015 * mean_dev)


@register_screener
class TechnicalScreener(BaseScreener):
    """Technical indicator-based stock screener."""

    screener_id = "technical"
    screener_name = "技术面筛选"
    screener_description = (
        "基于技术指标的选股策略。"
        "包含均线排列(多头/空头)、RSI超卖反弹、MACD金叉、"
        "布林带、KDJ随机指标、DMI/ADX趋势强度、威廉%R、CCI、"
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

            # 6. Bollinger Bands
            bb_upper, bb_mid, bb_lower = _compute_bollinger(closes)
            if bb_upper is not None and bb_lower is not None:
                factors["bb_upper"] = round(bb_upper, 2)
                factors["bb_mid"] = round(bb_mid, 2)
                factors["bb_lower"] = round(bb_lower, 2)
                if current_price <= bb_lower:
                    score += 1.0
                    signals.append(f"布林下轨支撑(价格{current_price:.2f}≤下轨{bb_lower:.2f})")
                elif current_price >= bb_upper:
                    score -= 0.3
                    signals.append(f"布林上轨压力(价格{current_price:.2f}≥上轨{bb_upper:.2f})")

            # 7. KDJ
            kdj_k, kdj_d, kdj_j = _compute_kdj(klines)
            if kdj_k is not None and kdj_d is not None:
                factors["kdj_k"] = round(kdj_k, 2)
                factors["kdj_d"] = round(kdj_d, 2)
                factors["kdj_j"] = round(kdj_j, 2)
                if kdj_k < 20 and kdj_j < 0:
                    score += 1.0
                    signals.append(f"KDJ超卖(K={kdj_k:.1f}, J={kdj_j:.1f}<0)")
                elif kdj_k > 80 and kdj_j > 100:
                    score -= 0.5
                    signals.append(f"KDJ超买(K={kdj_k:.1f}, J={kdj_j:.1f}>100)")
                elif kdj_k > kdj_d and kdj_k < 50:
                    score += 0.5
                    signals.append(f"KDJ金叉(低位K={kdj_k:.1f}>D={kdj_d:.1f})")

            # 8. DMI / ADX
            plus_di, minus_di, adx_val = _compute_dmi(klines)
            if plus_di is not None and minus_di is not None and adx_val is not None:
                factors["dmi_plus"] = round(plus_di, 2)
                factors["dmi_minus"] = round(minus_di, 2)
                factors["adx"] = round(adx_val, 2)
                if plus_di > minus_di and adx_val > 25:
                    score += 1.0
                    signals.append(f"DMI多头趋势(+DI={plus_di:.1f}>-DI={minus_di:.1f}, ADX={adx_val:.1f})")
                elif minus_di > plus_di and adx_val > 25:
                    score -= 0.5
                    signals.append(f"DMI空头趋势(-DI={minus_di:.1f}>+DI={plus_di:.1f})")

            # 9. Williams %R
            wr = _compute_williams_r(klines)
            if wr is not None:
                factors["williams_r"] = round(wr, 2)
                if wr < -80:
                    score += 0.5
                    signals.append(f"威廉超卖(%R={wr:.1f}<-80)")
                elif wr > -20:
                    score -= 0.3
                    signals.append(f"威廉超买(%R={wr:.1f}>-20)")

            # 10. CCI
            cci_val = _compute_cci(klines)
            if cci_val is not None:
                factors["cci"] = round(cci_val, 2)
                if cci_val < -100:
                    score += 0.5
                    signals.append(f"CCI超卖(CCI={cci_val:.1f}<-100)")
                elif cci_val > 100:
                    score -= 0.3
                    signals.append(f"CCI超买(CCI={cci_val:.1f}>100)")

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
