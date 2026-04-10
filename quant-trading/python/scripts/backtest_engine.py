#!/usr/bin/env python3
"""
Backtest Engine — multi-strategy backtesting for investment plans.

Simulates trading over historical K-line data with configurable strategies,
calculates comprehensive performance metrics, and supports strategy comparison.

Actions:
  run       — run a single strategy backtest
  compare   — run multiple strategies and compare results

Input (JSON via stdin):
  { "action": "run",
    "initial_capital": 500000,
    "start_date": "2025-01-01",
    "end_date": "2025-12-31",
    "strategy": "momentum",       // momentum | value | growth | swing | mixed
    "risk_tolerance": "medium",   // low | medium | high | aggressive
    "symbols": [],                // optional: specific stocks; [] = auto-select from universe
    "rebalance_days": 14,         // how often to rebalance portfolio
    "commission_rate": 0.0003,    // per-trade commission (default: 万三)
    "stamp_tax": 0.001            // sell stamp tax (default: ǧһ)
  }
"""

import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from typing import Any

# Proxy bypass
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
try:
    import requests, requests.utils  # noqa: E401
    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
except ImportError:
    pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_ROOT)


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


# ── Default stock universe for backtesting ────────────────────────
# Major CSI-300 components — diversified sectors (blue chips)
_BACKTEST_UNIVERSE = {
    "large_cap": [
        "600519", "601318", "600036", "000858", "000333",
        "600276", "601166", "600900", "000651", "601012",
    ],
    "growth": [
        "300750", "002415", "300059", "603259", "002475",
        "600570", "688981", "300760", "002230", "002049",
    ],
    "value": [
        "601398", "600048", "601601", "000725", "600887",
        "600030", "600016", "000001", "601088", "601628",
    ],
    "consumer": [
        "600585", "000568", "600809", "603288", "600887",
        "000895", "002304", "601888", "600104", "002714",
    ],
    "tech": [
        "002415", "300750", "002475", "600570", "300059",
        "000063", "002230", "688981", "300760", "600588",
    ],
}


# ── Risk Profiles (same as investment_planner) ─────────────────────
RISK_PROFILES = {
    "low": {
        "label": "稳健型",
        "max_single_position_pct": 0.15,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.15,
        "max_positions": 10,
        "min_positions": 5,
    },
    "medium": {
        "label": "均衡型",
        "max_single_position_pct": 0.20,
        "stop_loss_pct": 0.08,
        "take_profit_pct": 0.25,
        "max_positions": 8,
        "min_positions": 4,
    },
    "high": {
        "label": "进取型",
        "max_single_position_pct": 0.25,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.35,
        "max_positions": 6,
        "min_positions": 3,
    },
    "aggressive": {
        "label": "激进型",
        "max_single_position_pct": 0.30,
        "stop_loss_pct": 0.12,
        "take_profit_pct": 0.50,
        "max_positions": 5,
        "min_positions": 2,
    },
}


# ── Technical Indicators ──────────────────────────────────────────

def _sma(data: list[float], period: int) -> float | None:
    if len(data) < period:
        return None
    return sum(data[-period:]) / period


def _ema(data: list[float], period: int) -> float | None:
    if len(data) < period:
        return None
    k = 2.0 / (period + 1)
    ema_val = sum(data[:period]) / period
    for v in data[period:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val


def _rsi(data: list[float], period: int = 14) -> float | None:
    if len(data) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(data)):
        d = data[i] - data[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(data: list[float]) -> tuple[float | None, float | None, float | None]:
    """Returns (macd_line, signal_line, histogram)."""
    ema12 = _ema(data, 12)
    ema26 = _ema(data, 26)
    if ema12 is None or ema26 is None:
        return None, None, None
    macd_line = ema12 - ema26
    # Approximate signal line
    if len(data) < 35:
        return macd_line, None, None
    # Compute MACD series for signal
    macd_series = []
    k12 = 2.0 / 13
    k26 = 2.0 / 27
    e12 = sum(data[:12]) / 12
    e26 = sum(data[:26]) / 26
    for i in range(26, len(data)):
        if i >= 12:
            e12 = data[i] * k12 + e12 * (1 - k12)
        if i >= 26:
            e26 = data[i] * k26 + e26 * (1 - k26)
        macd_series.append(e12 - e26)
    if len(macd_series) < 9:
        return macd_line, None, None
    k9 = 2.0 / 10
    sig = sum(macd_series[:9]) / 9
    for v in macd_series[9:]:
        sig = v * k9 + sig * (1 - k9)
    return macd_line, sig, macd_line - sig


# ── Strategy Signal Generators ────────────────────────────────────

def _strategy_momentum(closes: list[float], volumes: list[float], idx: int) -> float:
    """Momentum strategy: buy when price above MAs with trend confirmation, sell on trend breakdown."""
    if idx < 60:
        return 0
    score = 0.0
    window = closes[:idx + 1]
    ma5 = _sma(window, 5)
    ma10 = _sma(window, 10)
    ma20 = _sma(window, 20)
    ma60 = _sma(window, 60)
    rsi14 = _rsi(window, 14)
    price = closes[idx]

    # ── Market regime filter (MA60 trend) ──
    # Only go long when broader trend is up; penalize in downtrend
    regime_up = ma60 is not None and price > ma60
    if ma60:
        ma60_slope = (ma60 - _sma(closes[:max(1, idx - 9)], 60 if idx >= 69 else max(1, idx - 9))) if idx >= 69 else 0
        if ma60_slope is None:
            ma60_slope = 0
        if price < ma60:
            score -= 2  # Below long-term trend — bearish regime
            if price < ma60 * 0.95:
                score -= 1.5  # Deep below MA60

    # ── MA alignment ──
    if ma5 and ma10 and ma20:
        if price > ma5 > ma10 > ma20:
            score += 3  # Strong uptrend
        elif price > ma10 > ma20:
            score += 2  # Moderate uptrend
        elif price > ma20:
            score += 1  # Weak uptrend
        elif price < ma5 < ma10 < ma20:
            score -= 4  # Strong downtrend — stronger sell
        elif price < ma10 < ma20:
            score -= 3  # Moderate downtrend
        elif price < ma20:
            score -= 2  # Below MA20

    # ── RSI filter ──
    if rsi14:
        if rsi14 > 80:
            score -= 3  # Overbought — strong sell
        elif rsi14 > 70:
            score -= 1.5  # Near overbought
        elif rsi14 < 30:
            # Only buy oversold if broad trend is up
            score += 2 if regime_up else -0.5
        elif 40 < rsi14 < 60:
            score += 0.5  # Neutral zone

    # ── Volume confirmation ──
    if idx >= 5 and len(volumes) > idx:
        recent_vol = sum(volumes[idx - 4:idx + 1]) / 5
        prev_vol = sum(volumes[max(0, idx - 9):idx - 4]) / 5 if idx >= 10 else recent_vol
        if prev_vol > 0:
            vol_ratio = recent_vol / prev_vol
            if vol_ratio > 1.3 and regime_up:
                score += 1.5  # Volume expansion in uptrend
            elif vol_ratio > 1.5 and not regime_up:
                score -= 1  # Volume expansion in downtrend = distribution

    # ── Price momentum (5-day & 10-day) ──
    if idx >= 5 and closes[idx - 5] > 0:
        mom5 = (price / closes[idx - 5] - 1) * 100
        if mom5 > 5:
            score += 1.5
        elif mom5 > 2:
            score += 0.5
        elif mom5 < -5:
            score -= 2.5  # Stronger sell on sharp drop
        elif mom5 < -3:
            score -= 1.5

    if idx >= 10 and closes[idx - 10] > 0:
        mom10 = (price / closes[idx - 10] - 1) * 100
        if mom10 < -8:
            score -= 2  # 10-day breakdown

    return score


def _strategy_value(closes: list[float], volumes: list[float], idx: int) -> float:
    """Value strategy: mean-reversion with trend safety checks and value-trap protection.

    Buy on dips toward MA20 only when long-term trend is intact (MA60 rising).
    Protect against value traps via trend deterioration and momentum checks.
    """
    if idx < 60:
        return 0
    score = 0.0
    window = closes[:idx + 1]
    price = closes[idx]
    ma10 = _sma(window, 10)
    ma20 = _sma(window, 20)
    ma60 = _sma(window, 60)
    rsi14 = _rsi(window, 14)

    # ── Trend safety: MA60 must be intact for buy signals ──
    trend_intact = ma60 is not None and price > ma60
    ma60_rising = False
    if ma60 and idx >= 69:
        ma60_prev = _sma(closes[:idx - 9], 60)
        ma60_rising = ma60_prev is not None and ma60 > ma60_prev

    # ── Mean reversion: price below MA20 = buy opportunity (only in uptrend) ──
    if ma20 and price < ma20 and trend_intact:
        discount = (ma20 - price) / ma20 * 100
        if discount > 10:
            score += 3  # Deep discount
        elif discount > 5:
            score += 2  # Moderate discount
        elif discount > 2:
            score += 1  # Slight discount
        # Bonus if MA60 is still rising (healthy dip)
        if ma60_rising:
            score += 1

    # ── Value trap detection: penalize when long-term trend breaks ──
    if ma60 and price < ma60:
        score -= 2  # Below long-term trend — potential value trap
        if ma20 and ma20 < ma60:
            score -= 2  # MA20 also below MA60 — trend deterioration confirmed

    # ── Consecutive decline detection ──
    if idx >= 5:
        decline_days = sum(1 for i in range(idx - 4, idx + 1) if closes[i] < closes[i - 1])
        if decline_days >= 4:
            score -= 2  # 4+ declining days = panic selling, avoid catching knife

    # ── RSI signals ──
    if rsi14:
        if rsi14 < 30 and trend_intact:
            score += 2  # Oversold in uptrend = buy
        elif rsi14 < 20:
            score -= 1  # Extreme oversold even in uptrend = possible capitulation
        elif rsi14 > 75:
            score -= 2  # Overbought — sell
        elif rsi14 > 65:
            score -= 1  # Near overbought — reduce

    # ── Volume divergence (sell signal) ──
    if idx >= 10 and len(volumes) > idx:
        avg_vol_5 = sum(volumes[idx - 4:idx + 1]) / 5
        avg_vol_20 = sum(volumes[max(0, idx - 19):idx + 1]) / min(20, idx + 1)
        if avg_vol_20 > 0:
            # Rising volume on falling price = distribution
            mom5 = (price / closes[idx - 5] - 1) * 100 if idx >= 5 and closes[idx - 5] > 0 else 0
            if avg_vol_5 > avg_vol_20 * 1.5 and mom5 < -3:
                score -= 2  # Heavy selling pressure

    # ── Sell signal: price stretched above MA20 ──
    if ma20 and price > ma20 * 1.08:
        score -= 2  # Above mean — take profit
    if ma20 and price > ma20 * 1.15:
        score -= 1.5  # Far above mean — strong sell

    # ── Momentum confirmation ──
    if idx >= 10 and closes[idx - 10] > 0:
        mom10 = (price / closes[idx - 10] - 1) * 100
        if mom10 < -12:
            score -= 3  # Steep decline — value trap likely
        elif mom10 < -8:
            score -= 1.5

    return score


def _strategy_growth(closes: list[float], volumes: list[float], idx: int) -> float:
    """Growth strategy: ride trends via MACD and breakouts, with balanced sell signals."""
    if idx < 35:
        return 0
    score = 0.0
    window = closes[:idx + 1]
    price = closes[idx]
    ma10 = _sma(window, 10)
    ma20 = _sma(window, 20)
    ma60 = _sma(window, 60) if idx >= 60 else None
    macd_line, signal, hist = _macd(window)

    # ── MACD signals (balanced buy/sell) ──
    if macd_line is not None and signal is not None:
        if macd_line > signal:
            score += 2  # Bullish
        else:
            score -= 2  # Bearish — symmetric with buy

    if hist is not None:
        if hist > 0:
            score += 1
            # Check histogram momentum: shrinking = weakening
            if idx >= 36:
                prev_window = closes[:idx]
                _, _, prev_hist = _macd(prev_window)
                if prev_hist is not None and hist < prev_hist * 0.6:
                    score -= 1  # Histogram shrinking — momentum fading
        else:
            score -= 1.5  # Negative histogram — sell pressure
            if hist < -0.5:  # Deep negative histogram
                score -= 1

    # ── Breakout: new 20-day high with volume ──
    if idx >= 20:
        high_20 = max(closes[idx - 20:idx])
        if price > high_20:
            score += 2  # Breakout
            if len(volumes) > idx and idx >= 5:
                avg_vol = sum(volumes[idx - 5:idx]) / 5
                if avg_vol > 0 and volumes[idx] > avg_vol * 1.5:
                    score += 1  # Volume breakout
        # Breakdown: new 20-day low
        low_20 = min(closes[idx - 20:idx])
        if price < low_20:
            score -= 3  # 20-day breakdown — strong sell

    # ── MA20 position ──
    if ma20:
        if price > ma20:
            score += 1
        elif price < ma20:
            score -= 2  # Below MA20 — sell signal
        if price < ma20 * 0.95:
            score -= 1.5  # Deep below MA20

    # ── MA60 trend filter ──
    if ma60:
        if price < ma60:
            score -= 1.5  # Below long-term trend

    # ── MA death cross ──
    if ma10 and ma20 and ma10 < ma20:
        score -= 1.5  # MA10 below MA20 = bearish cross

    return score


def _strategy_swing(closes: list[float], volumes: list[float], idx: int) -> float:
    """Swing strategy: buy dips in uptrend, sell rips. Higher frequency."""
    if idx < 14:
        return 0
    score = 0.0
    window = closes[:idx + 1]
    price = closes[idx]
    ma5 = _sma(window, 5)
    ma10 = _sma(window, 10)
    rsi14 = _rsi(window, 14)

    # Short-term reversal signals
    if rsi14:
        if rsi14 < 25:
            score += 3  # Strong oversold bounce trade
        elif rsi14 < 35:
            score += 2  # Oversold
        elif rsi14 > 75:
            score -= 3  # Strong overbought — sell
        elif rsi14 > 65:
            score -= 1.5

    # Bollinger idea: price deviation from MA5
    if ma5 and ma5 > 0:
        dev = (price - ma5) / ma5 * 100
        if dev < -3:
            score += 2  # Below lower band
        elif dev > 3:
            score -= 1.5  # Above upper band

    # MA5/MA10 cross
    if ma5 and ma10:
        if ma5 > ma10:
            score += 0.5
        else:
            score -= 0.5

    return score


def _strategy_mixed(closes: list[float], volumes: list[float], idx: int) -> float:
    """Mixed: blend of all strategies with equal weight."""
    scores = [
        _strategy_momentum(closes, volumes, idx),
        _strategy_value(closes, volumes, idx),
        _strategy_growth(closes, volumes, idx),
        _strategy_swing(closes, volumes, idx),
    ]
    return sum(scores) / len(scores)


def _strategy_manipulation(closes: list[float], volumes: list[float], idx: int) -> float:
    """Manipulation detection strategy: follow accumulation, avoid distribution.

    Combines Wyckoff/VSA principles:
    - Accumulation (吸筹): low price position + volume patterns → buy
    - Distribution (出货): high price position + volume patterns → sell
    - Churning (对倒): high volume + no price movement → avoid
    - Volume-price confirmation: validate trends with volume analysis
    """
    if idx < 30:
        return 0
    score = 0.0
    window = closes[:idx + 1]
    vol_window = volumes[:idx + 1]
    price = closes[idx]
    n = len(window)

    # Basic technicals for context
    ma5 = _sma(window, 5)
    ma20 = _sma(window, 20)
    ma60 = _sma(window, 60) if n >= 60 else ma20
    rsi14 = _rsi(window, 14)

    # ── Price position in 60-day range ──
    lookback = min(60, n)
    high_range = max(closes[max(0, idx - lookback + 1):idx + 1])
    low_range = min(closes[max(0, idx - lookback + 1):idx + 1])
    price_range = high_range - low_range
    if price_range <= 0:
        return 0
    price_pos = (price - low_range) / price_range  # 0=bottom, 1=top

    # ── Volume analysis ──
    avg_vol_5 = sum(vol_window[max(0, idx - 4):idx + 1]) / min(5, idx + 1)
    avg_vol_20 = sum(vol_window[max(0, idx - 19):idx + 1]) / min(20, idx + 1)
    avg_vol_60 = sum(vol_window[max(0, idx - 59):idx + 1]) / min(60, idx + 1) if idx >= 20 else avg_vol_20
    vol_ratio = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 1.0

    # ── Accumulation Signals (吸筹 → buy) ──

    # 1. Volume shrinks on dips, expands on ups (缩量回调放量上涨)
    up_vols, down_vols = [], []
    for i in range(max(1, idx - 19), idx + 1):
        if closes[i] >= closes[i - 1]:
            up_vols.append(volumes[i])
        else:
            down_vols.append(volumes[i])
    if up_vols and down_vols:
        avg_up = sum(up_vols) / len(up_vols)
        avg_down = sum(down_vols) / len(down_vols)
        if avg_up > avg_down * 1.3:
            score += 2.0  # Accumulation: up-volume > down-volume

    # 2. Bottom consolidation (底部横盘整理)
    if price_pos < 0.3:
        recent_range = (max(closes[max(0, idx - 9):idx + 1]) - min(closes[max(0, idx - 9):idx + 1])) / price if price > 0 else 1
        if recent_range < 0.06:
            score += 2.5  # Tight range at bottom = accumulation

    # 3. Low position + volume picking up (低位放量建仓)
    if price_pos < 0.25 and avg_vol_5 > avg_vol_60 * 1.3:
        score += 2.0

    # 4. Lower shadows at support (下影线承接)
    if idx >= 10:
        shadow_count = 0
        for i in range(idx - 9, idx + 1):
            if i >= 1 and len(vol_window) > i:
                # Approximate open as previous close for daily bars
                open_approx = closes[i - 1]
                body_low = min(open_approx, closes[i])
                bar_low = min(closes[max(0, i - 1):i + 1])  # fallback
                # Use close/open for body, guess lower shadow
                body_size = abs(closes[i] - open_approx)
                if body_size > 0:
                    price_drop = max(0, body_low - bar_low)
                    # Lower shadow proxy: if price recovered significantly
                    recovery = closes[i] - min(closes[max(0, i - 1):i + 1])
                    if recovery > body_size * 0.5 and closes[i] > open_approx:
                        shadow_count += 1
        if shadow_count >= 3:
            score += 1.5

    # ── Distribution Signals (出货 → sell) ──

    # 1. Volume climax at highs (高位天量滞涨)
    if price_pos > 0.7 and idx >= 10:
        max_vol = max(vol_window[idx - 9:idx + 1])
        if max_vol > avg_vol_20 * 2.5:
            # Check if price moved little on that day
            max_vol_day = idx - 9 + vol_window[idx - 9:idx + 1].index(max_vol)
            if max_vol_day > 0:
                day_move = abs(closes[max_vol_day] - closes[max_vol_day - 1]) / closes[max_vol_day - 1] if closes[max_vol_day - 1] > 0 else 0
                if day_move < 0.02:
                    score -= 3.0  # Volume climax + no movement = distribution

    # 2. Volume increasing but price stalling at highs (量增价不涨)
    if price_pos > 0.6:
        price_5d = (closes[idx] / closes[max(0, idx - 5)] - 1) * 100 if idx >= 5 else 0
        if vol_ratio > 1.4 and abs(price_5d) < 2:
            score -= 2.5  # Distribution divergence

    # 3. High position + volume expansion + price falling
    if price_pos > 0.7 and vol_ratio > 1.5:
        mom_5d = (closes[idx] / closes[max(0, idx - 5)] - 1) * 100 if idx >= 5 else 0
        if mom_5d < -2:
            score -= 3.0  # Distribution dump

    # ── Churning Detection (对倒 → avoid) ──
    if idx >= 5:
        for i in range(idx - 4, idx + 1):
            if i >= 1 and volumes[i] > avg_vol_20 * 2:
                day_change = abs(closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] > 0 else 0
                if day_change < 0.01:
                    score -= 2.0  # Huge volume, no movement = churning
                    break

    # ── Volume-Price Confirmation with Tier Analysis ──
    mom_5d = (closes[idx] / closes[max(0, idx - 5)] - 1) * 100 if idx >= 5 else 0

    # Classify volume ratio into tiers
    if vol_ratio >= 5.0:
        # Extreme (>5): almost always manipulator
        day_change = (closes[idx] / closes[max(0, idx - 1)] - 1) * 100 if idx >= 1 else 0
        if price_pos > 0.7 and day_change < 3:
            score -= 4.0  # High + extreme vol + stall = distribution dump
        elif price_pos > 0.7 and day_change > 8:
            score -= 2.0  # High + extreme vol + spike = pump-and-dump lure
        elif price_pos < 0.3 and day_change > 0:
            score += 3.5  # Low + extreme vol + rising = aggressive accumulation
        else:
            score -= 2.5  # Extreme vol elsewhere = high risk
    elif vol_ratio >= 3.0:
        # Surge (3-5): significant action
        if price_pos > 0.7:
            # Check for upper shadow (test/distribution)
            if idx >= 1:
                open_approx = closes[idx - 1]
                body_high = max(open_approx, closes[idx])
                body = abs(closes[idx] - open_approx)
                if body > 0:
                    upper_shadow_est = max(0, closes[max(0, idx - 1)] * 1.02 - body_high)
                    if upper_shadow_est > body * 0.5:
                        score -= 2.5  # Surge + high + upper shadow = distribution
            if mom_5d < 1:
                score -= 2.0  # Surge + high + stalling
        elif price_pos < 0.3 and mom_5d > 0:
            score += 2.5  # Surge + low + rising = accumulation
        elif abs(mom_5d) < 1:
            score -= 1.5  # Surge + flat = churning
    elif vol_ratio >= 2.0:
        # Strong expand (2-3): breakout/turning
        if mom_5d > 0:
            score += 2.0  # Confirms uptrend
        elif mom_5d < -2:
            score -= 2.0  # Confirms downtrend
    elif vol_ratio >= 1.2:
        # Mild expand (1.2-2): normal strengthening
        if mom_5d > 0:
            score += 1.5  # Volume confirms uptrend
        elif mom_5d < -2:
            score -= 1.5  # Volume confirms downtrend
    elif vol_ratio < 0.5:
        # Extreme low (<0.5): dead market
        if price_pos < 0.3:
            score += 2.0  # Selling exhaustion at bottom = accumulation end-stage
        elif price_pos > 0.7:
            score -= 1.5  # No fuel at top = exhaustion
        else:
            score -= 0.5  # Low interest
    elif vol_ratio < 0.8 and mom_5d < 0:
        score -= 1.0  # Shrinking volume + falling = bearish

    # ── Basic Technical Filters ──
    if ma5 and ma20 and ma5 > ma20:
        score += 1.0  # Short-term trend up
    elif ma5 and ma20 and ma5 < ma20 * 0.97:
        score -= 1.0  # Short-term trend clearly down

    if rsi14:
        if rsi14 > 80:
            score -= 2.0  # Overbought
        elif rsi14 < 25:
            score += 1.5  # Deep oversold = potential accumulation zone

    # ── Price Position Weighting ──
    if price_pos < 0.2:
        score += 1.5  # Low position = safer entry
    elif price_pos > 0.85:
        score -= 1.5  # High position = risky

    return score


_STRATEGY_FN = {
    "momentum": _strategy_momentum,
    "value": _strategy_value,
    "growth": _strategy_growth,
    "swing": _strategy_swing,
    "mixed": _strategy_mixed,
    "manipulation": _strategy_manipulation,
}


def _make_composed_strategy(
    sub_strategies: list[str],
    method: str = "voting",
):
    """Build a composed strategy function from sub-strategy IDs and a composition method."""
    fns = [_STRATEGY_FN[s] for s in sub_strategies if s in _STRATEGY_FN]
    if not fns:
        return _strategy_mixed  # Fallback

    if method == "weighted-score":
        def _weighted(closes, volumes, idx):
            scores = [fn(closes, volumes, idx) for fn in fns]
            return sum(scores) / len(scores)
        return _weighted
    elif method == "gated":
        # First strategy is primary, rest are filters — all filters must agree on direction
        primary = fns[0]
        filters = fns[1:] if len(fns) > 1 else []
        def _gated(closes, volumes, idx):
            sig = primary(closes, volumes, idx)
            if not filters:
                return sig
            for f in filters:
                if sig > 0 and f(closes, volumes, idx) <= 0:
                    return 0
                if sig < 0 and f(closes, volumes, idx) >= 0:
                    return 0
            return sig
        return _gated
    elif method == "cascade":
        def _cascade(closes, volumes, idx):
            for fn in fns:
                sig = fn(closes, volumes, idx)
                if abs(sig) > 1:
                    return sig
            return fns[0](closes, volumes, idx)
        return _cascade
    else:
        # Default: voting — majority wins
        def _voting(closes, volumes, idx):
            scores = [fn(closes, volumes, idx) for fn in fns]
            buys = sum(1 for s in scores if s > 0.5)
            sells = sum(1 for s in scores if s < -0.5)
            threshold = len(fns) / 2
            if buys > threshold:
                return sum(s for s in scores if s > 0) / max(buys, 1)
            if sells > threshold:
                return sum(s for s in scores if s < 0) / max(sells, 1)
            return 0
        return _voting


# ── Position & Trade Types ────────────────────────────────────────

class Position:
    """Represents a held stock position."""
    __slots__ = ("symbol", "name", "shares", "avg_cost", "entry_date")

    def __init__(self, symbol: str, name: str, shares: int, avg_cost: float, entry_date: str):
        self.symbol = symbol
        self.name = name
        self.shares = shares
        self.avg_cost = avg_cost
        self.entry_date = entry_date


class Trade:
    """Represents a completed trade."""
    __slots__ = ("symbol", "name", "side", "shares", "price", "date", "pnl", "pnl_pct", "commission")

    def __init__(self, symbol: str, name: str, side: str, shares: int, price: float,
                 date: str, pnl: float, pnl_pct: float, commission: float):
        self.symbol = symbol
        self.name = name
        self.side = side
        self.shares = shares
        self.price = price
        self.date = date
        self.pnl = pnl
        self.pnl_pct = pnl_pct
        self.commission = commission

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "side": self.side,
            "shares": self.shares,
            "price": round(self.price, 2),
            "date": self.date,
            "pnl": round(self.pnl, 2),
            "pnlPct": round(self.pnl_pct, 2),
            "commission": round(self.commission, 2),
        }


# ── Main Backtest Runner ─────────────────────────────────────────

def _get_klines_cached(symbol: str, start_date: str, end_date: str) -> list[dict]:
    """Fetch K-line data, preferring cache, with akshare fallback."""
    try:
        from data_providers.akshare_provider import get_klines
        klines = get_klines(symbol, "1d", start_date, end_date, "cn_stock")
        return klines if klines else []
    except Exception:
        # Try cache only
        try:
            from cache.stock_cache import get_cache
            cache = get_cache()
            bars, _, _ = cache.get_klines(symbol, "cn_stock", "daily", start_date, end_date)
            return bars
        except Exception:
            return []


def _get_stock_name(symbol: str) -> str:
    """Try to get stock name from cache or Tencent."""
    try:
        from cache.stock_cache import get_cache
        cache = get_cache()
        info = cache.get_stock_info(symbol)
        if info and info.get("name"):
            return info["name"]
    except Exception:
        pass
    # Fallback: Tencent quote
    prefix = "sh" if symbol.startswith("6") or symbol.startswith("5") or symbol.startswith("9") else "sz"
    url = f"http://qt.gtimg.cn/q={prefix}{symbol}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            for enc in ("utf-8", "gbk"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                return symbol
            fields = text.split("~")
            if len(fields) > 2:
                return fields[1]
    except Exception:
        pass
    return symbol


def _select_universe(strategy: str, symbols: list[str] | None) -> list[str]:
    """Select stocks for backtesting based on strategy or user selection."""
    if symbols and len(symbols) > 0:
        return symbols

    # Mix stocks from relevant sectors
    universe = set()
    if strategy == "momentum":
        # Momentum trades across all sectors — needs diverse universe to find strength
        for group in _BACKTEST_UNIVERSE.values():
            universe.update(group)
    elif strategy == "value":
        universe.update(_BACKTEST_UNIVERSE["value"])
        universe.update(_BACKTEST_UNIVERSE["large_cap"])
        universe.update(_BACKTEST_UNIVERSE["consumer"][:5])
    elif strategy == "growth":
        universe.update(_BACKTEST_UNIVERSE["growth"])
        universe.update(_BACKTEST_UNIVERSE["tech"])
        universe.update(_BACKTEST_UNIVERSE["consumer"][:5])
    elif strategy == "swing":
        universe.update(_BACKTEST_UNIVERSE["growth"])
        universe.update(_BACKTEST_UNIVERSE["large_cap"][:5])
        universe.update(_BACKTEST_UNIVERSE["tech"][:5])
    else:  # mixed
        for group in _BACKTEST_UNIVERSE.values():
            universe.update(group[:5])

    return list(universe)


def run_backtest(
    initial_capital: float = 500000,
    start_date: str = "2025-01-01",
    end_date: str = "2025-12-31",
    strategy: str = "momentum",
    risk_tolerance: str = "medium",
    symbols: list[str] | None = None,
    rebalance_days: int = 14,
    commission_rate: float = 0.0003,
    stamp_tax: float = 0.001,
    sub_strategies: list[str] | None = None,
    composition_method: str | None = None,
) -> dict:
    """Run a full backtest simulation.

    Returns comprehensive performance metrics and trade history.
    """
    profile = RISK_PROFILES.get(risk_tolerance, RISK_PROFILES["medium"])

    # Resolve composed strategy if sub_strategies provided
    if strategy == "composed" and sub_strategies:
        strategy_fn = _make_composed_strategy(sub_strategies, composition_method or "voting")
    else:
        strategy_fn = _STRATEGY_FN.get(strategy, _strategy_momentum)

    # Per-strategy rebalance frequency: high-frequency strategies need shorter intervals
    _STRATEGY_REBALANCE = {
        "momentum": 5,       # Momentum needs fast reaction to trend changes
        "swing": 3,          # Swing trades are short-term by nature
        "growth": 7,         # Breakout signals need timely evaluation
        "value": 14,         # Value is patient — longer holding
        "mixed": 10,
        "manipulation": 7,
    }
    if rebalance_days == 14:  # Only override default; respect explicit user choice
        rebalance_days = _STRATEGY_REBALANCE.get(strategy, rebalance_days)

    # 1. Get stock universe
    universe = _select_universe(strategy, symbols)

    # 2. Fetch all historical data
    # Add 60 days of lookback for indicator warm-up
    lookback_start = datetime.strptime(start_date.replace("-", ""), "%Y%m%d") - timedelta(days=90)
    lookback_str = lookback_start.strftime("%Y%m%d")
    end_str = end_date.replace("-", "")

    all_data: dict[str, list[dict]] = {}  # symbol -> sorted bars
    stock_names: dict[str, str] = {}

    # Batch-fetch all klines at once (cache → QMT batch → fallback)
    try:
        from data_providers.akshare_provider import get_klines_batch
        batch_result = get_klines_batch(universe, lookback_str, end_str, "cn_stock")
        for sym, bars in batch_result.items():
            if bars and len(bars) >= 30:
                all_data[sym] = bars
                stock_names[sym] = _get_stock_name(sym)
    except ImportError:
        # Fallback: per-stock sequential fetch
        for sym in universe:
            try:
                bars = _get_klines_cached(sym, lookback_str, end_str)
                if bars and len(bars) >= 30:
                    all_data[sym] = bars
                    stock_names[sym] = _get_stock_name(sym)
            except Exception as e:
                print(f"[回测] {sym} 数据获取失败: {e}", file=sys.stderr)

    if not all_data:
        return {"error": "无法获取足够的历史数据进行回测", "symbols": universe}

    # 3. Build aligned date index (only dates within actual backtest period)
    start_ts = datetime.strptime(start_date.replace("-", ""), "%Y%m%d").timestamp() * 1000
    end_ts = datetime.strptime(end_date.replace("-", ""), "%Y%m%d").timestamp() * 1000

    # Collect all unique dates from bars that fall within [start, end]
    all_dates: set[int] = set()
    for bars in all_data.values():
        for bar in bars:
            ts = bar["timestamp"]
            if start_ts <= ts <= end_ts:
                all_dates.add(ts)

    sorted_dates = sorted(all_dates)
    if not sorted_dates:
        return {"error": "回测期间内无交易数据"}

    # Build per-symbol close/volume arrays indexed by bar position
    # And a map from global date index to per-symbol bar index
    sym_closes: dict[str, list[float]] = {}
    sym_volumes: dict[str, list[float]] = {}
    sym_bar_map: dict[str, dict[int, int]] = {}  # sym -> {global_date_idx -> bar_idx}

    for sym, bars in all_data.items():
        closes = [b["close"] for b in bars]
        volumes = [float(b.get("volume", 0)) for b in bars]
        sym_closes[sym] = closes
        sym_volumes[sym] = volumes
        bar_ts_map = {b["timestamp"]: i for i, b in enumerate(bars)}
        date_map = {}
        for i, ts in enumerate(sorted_dates):
            if ts in bar_ts_map:
                date_map[i] = bar_ts_map[ts]
        sym_bar_map[sym] = date_map

    # 4. Run simulation
    cash = initial_capital
    positions: dict[str, Position] = {}
    trades: list[Trade] = []
    equity_curve: list[dict] = []
    total_commission = 0.0
    last_rebalance_idx = -rebalance_days  # Trigger rebalance on first day

    max_positions = profile["max_positions"]
    max_pos_pct = profile["max_single_position_pct"]
    stop_loss_pct = profile["stop_loss_pct"]
    take_profit_pct = profile["take_profit_pct"]

    for day_idx, date_ts in enumerate(sorted_dates):
        date_str = datetime.fromtimestamp(date_ts / 1000).strftime("%Y-%m-%d")

        # Get current prices for all symbols
        current_prices: dict[str, float] = {}
        for sym, dmap in sym_bar_map.items():
            if day_idx in dmap:
                bar_idx = dmap[day_idx]
                current_prices[sym] = sym_closes[sym][bar_idx]

        # ── Check stop-loss / take-profit for existing positions ──
        exited_syms = []
        for sym, pos in list(positions.items()):
            if sym not in current_prices:
                continue
            price = current_prices[sym]
            pnl_pct = (price / pos.avg_cost - 1) if pos.avg_cost > 0 else 0

            should_exit = False
            if pnl_pct <= -stop_loss_pct:
                should_exit = True  # Stop loss
            elif pnl_pct >= take_profit_pct:
                should_exit = True  # Take profit

            if should_exit:
                sell_value = pos.shares * price
                comm = sell_value * commission_rate + sell_value * stamp_tax
                total_commission += comm
                pnl = sell_value - pos.shares * pos.avg_cost - comm
                cash += sell_value - comm
                trades.append(Trade(
                    sym, pos.name, "sell", pos.shares, price, date_str,
                    pnl, pnl_pct * 100, comm
                ))
                exited_syms.append(sym)

        for sym in exited_syms:
            del positions[sym]

        # ── Rebalance check ──
        if day_idx - last_rebalance_idx >= rebalance_days:
            last_rebalance_idx = day_idx

            # Score all available symbols
            scored: list[tuple[str, float]] = []
            for sym in all_data:
                if sym not in sym_bar_map or day_idx not in sym_bar_map[sym]:
                    continue
                bar_idx = sym_bar_map[sym][day_idx]
                if bar_idx < 20:
                    continue  # Not enough lookback
                signal = strategy_fn(sym_closes[sym], sym_volumes[sym], bar_idx)
                scored.append((sym, signal))

            scored.sort(key=lambda x: x[1], reverse=True)

            # Sell positions with negative signals
            for sym, pos in list(positions.items()):
                sym_signal = next((s for s_sym, s in scored if s_sym == sym), 0)
                if sym_signal < -1 and sym in current_prices:
                    price = current_prices[sym]
                    sell_value = pos.shares * price
                    comm = sell_value * commission_rate + sell_value * stamp_tax
                    total_commission += comm
                    pnl_pct_val = (price / pos.avg_cost - 1) if pos.avg_cost > 0 else 0
                    pnl = sell_value - pos.shares * pos.avg_cost - comm
                    cash += sell_value - comm
                    trades.append(Trade(
                        sym, pos.name, "sell", pos.shares, price, date_str,
                        pnl, pnl_pct_val * 100, comm
                    ))
                    del positions[sym]

            # Buy top-scored stocks (fill open slots)
            open_slots = max_positions - len(positions)
            if open_slots > 0:
                buy_candidates = [
                    (sym, sig) for sym, sig in scored
                    if sig > 1 and sym not in positions and sym in current_prices
                ][:open_slots]

                for sym, sig in buy_candidates:
                    price = current_prices[sym]
                    if price <= 0:
                        continue
                    # Position sizing
                    total_equity = cash + sum(
                        p.shares * current_prices.get(s, p.avg_cost) for s, p in positions.items()
                    )
                    max_alloc = total_equity * max_pos_pct
                    alloc = min(cash * 0.9 / max(1, open_slots), max_alloc)
                    if alloc < price * 100:
                        continue  # Can't afford a round lot
                    shares = int(alloc / price / 100) * 100
                    if shares < 100:
                        continue
                    cost = shares * price
                    comm = cost * commission_rate
                    total_commission += comm
                    cash -= cost + comm
                    positions[sym] = Position(
                        sym, stock_names.get(sym, sym), shares, price, date_str
                    )
                    trades.append(Trade(
                        sym, stock_names.get(sym, sym), "buy", shares, price, date_str,
                        0, 0, comm
                    ))

        # ── Record daily equity ──
        portfolio_value = sum(
            pos.shares * current_prices.get(sym, pos.avg_cost)
            for sym, pos in positions.items()
        )
        total_equity = cash + portfolio_value
        equity_curve.append({
            "date": date_str,
            "timestamp": date_ts,
            "equity": round(total_equity, 2),
            "cash": round(cash, 2),
            "positions": len(positions),
            "portfolioValue": round(portfolio_value, 2),
        })

    # 5. Calculate performance metrics
    if not equity_curve:
        return {"error": "回测生成了空的净值曲线"}

    final_equity = equity_curve[-1]["equity"]
    total_return = (final_equity / initial_capital - 1) * 100
    trading_days = len(equity_curve)
    annual_factor = 252 / max(1, trading_days)
    annualized_return = ((final_equity / initial_capital) ** annual_factor - 1) * 100

    # Max drawdown
    peak = initial_capital
    max_dd = 0.0
    max_dd_start = ""
    max_dd_end = ""
    dd_start_temp = equity_curve[0]["date"]
    for pt in equity_curve:
        if pt["equity"] > peak:
            peak = pt["equity"]
            dd_start_temp = pt["date"]
        dd = (peak - pt["equity"]) / peak * 100
        if dd > max_dd:
            max_dd = dd
            max_dd_start = dd_start_temp
            max_dd_end = pt["date"]

    # Sharpe ratio (assume risk-free rate = 3%)
    if len(equity_curve) >= 2:
        daily_returns = []
        for i in range(1, len(equity_curve)):
            prev_eq = equity_curve[i - 1]["equity"]
            curr_eq = equity_curve[i]["equity"]
            if prev_eq > 0:
                daily_returns.append(curr_eq / prev_eq - 1)
        if daily_returns:
            avg_ret = sum(daily_returns) / len(daily_returns)
            n = len(daily_returns)
            std_ret = (sum((r - avg_ret) ** 2 for r in daily_returns) / max(1, n - 1)) ** 0.5
            sharpe = (avg_ret - 0.03 / 252) / std_ret * (252 ** 0.5) if std_ret > 0 else 0
        else:
            sharpe = 0
    else:
        sharpe = 0

    # Win rate
    closed_sells = [t for t in trades if t.side == "sell"]
    winning_trades = [t for t in closed_sells if t.pnl > 0]
    losing_trades = [t for t in closed_sells if t.pnl < 0]
    win_rate = len(winning_trades) / len(closed_sells) * 100 if closed_sells else 0
    avg_win = sum(t.pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
    avg_loss = sum(t.pnl for t in losing_trades) / len(losing_trades) if losing_trades else 0
    profit_factor = abs(sum(t.pnl for t in winning_trades)) / abs(sum(t.pnl for t in losing_trades)) \
        if losing_trades and sum(t.pnl for t in losing_trades) != 0 else 0

    # Monthly returns
    monthly_returns: dict[str, float] = {}
    prev_month_eq = initial_capital
    for pt in equity_curve:
        month_key = pt["date"][:7]  # "YYYY-MM"
        monthly_returns[month_key] = pt["equity"]
    monthly_ret_list = []
    prev_eq = initial_capital
    for mk in sorted(monthly_returns.keys()):
        eq = monthly_returns[mk]
        ret = (eq / prev_eq - 1) * 100 if prev_eq > 0 else 0
        monthly_ret_list.append({"month": mk, "return": round(ret, 2), "equity": round(eq, 2)})
        prev_eq = eq

    # Downsample equity curve for chart (max 250 points)
    if len(equity_curve) > 250:
        step = len(equity_curve) // 250
        chart_curve = equity_curve[::step]
        if equity_curve[-1] not in chart_curve:
            chart_curve.append(equity_curve[-1])
    else:
        chart_curve = equity_curve

    # Final positions (still held)
    final_positions = []
    for sym, pos in positions.items():
        last_price = current_prices.get(sym, pos.avg_cost) if 'current_prices' in dir() else pos.avg_cost
        pnl = (last_price - pos.avg_cost) * pos.shares
        final_positions.append({
            "symbol": sym,
            "name": pos.name,
            "shares": pos.shares,
            "avgCost": round(pos.avg_cost, 2),
            "currentPrice": round(last_price, 2) if last_price else pos.avg_cost,
            "pnl": round(pnl, 2),
            "pnlPct": round((last_price / pos.avg_cost - 1) * 100, 2) if pos.avg_cost > 0 else 0,
        })

    strategy_labels = {
        "momentum": "趋势动量", "value": "价值投资",
        "growth": "成长投资", "swing": "波段交易", "mixed": "混合策略",
        "manipulation": "跟庄策略", "composed": "组合策略",
    }

    return {
        "summary": {
            "strategy": strategy,
            "strategyLabel": strategy_labels.get(strategy, strategy),
            "riskTolerance": risk_tolerance,
            "riskProfile": profile["label"],
            "initialCapital": initial_capital,
            "finalEquity": round(final_equity, 2),
            "totalReturn": round(total_return, 2),
            "annualizedReturn": round(annualized_return, 2),
            "maxDrawdown": round(max_dd, 2),
            "maxDrawdownStart": max_dd_start,
            "maxDrawdownEnd": max_dd_end,
            "sharpeRatio": round(sharpe, 2),
            "winRate": round(win_rate, 2),
            "profitFactor": round(profit_factor, 2),
            "totalTrades": len(trades),
            "winningTrades": len(winning_trades),
            "losingTrades": len(losing_trades),
            "avgWin": round(avg_win, 2),
            "avgLoss": round(avg_loss, 2),
            "totalCommission": round(total_commission, 2),
            "tradingDays": trading_days,
            "startDate": equity_curve[0]["date"],
            "endDate": equity_curve[-1]["date"],
        },
        "equityCurve": chart_curve,
        "monthlyReturns": monthly_ret_list,
        "trades": [t.to_dict() for t in trades[-100:]],  # Last 100 trades
        "finalPositions": final_positions,
    }


def compare_strategies(
    initial_capital: float = 500000,
    start_date: str = "2025-01-01",
    end_date: str = "2025-12-31",
    strategies: list[str] | None = None,
    risk_tolerance: str = "medium",
    symbols: list[str] | None = None,
    rebalance_days: int = 14,
    commission_rate: float = 0.0003,
    stamp_tax: float = 0.001,
    composed_strategies: list[dict] | None = None,
) -> dict:
    """Compare multiple strategies on the same period and universe.

    composed_strategies: optional list of dicts with keys:
      - id, name, sub_strategies (list of strategy IDs), composition_method
    """
    if not strategies:
        strategies = ["momentum", "value", "growth", "swing", "mixed", "manipulation"]

    _LABELS = {
        "momentum": "趋势动量", "value": "价值投资",
        "growth": "成长投资", "swing": "波段交易",
        "mixed": "混合策略", "manipulation": "跟庄策略",
    }

    results = []
    for strat in strategies:
        result = run_backtest(
            initial_capital=initial_capital,
            start_date=start_date,
            end_date=end_date,
            strategy=strat,
            risk_tolerance=risk_tolerance,
            symbols=symbols,
            rebalance_days=rebalance_days,
            commission_rate=commission_rate,
            stamp_tax=stamp_tax,
        )
        if "error" not in result:
            results.append(result)
        else:
            results.append({
                "summary": {
                    "strategy": strat,
                    "strategyLabel": _LABELS.get(strat, strat),
                    "error": result.get("error", "回测失败"),
                },
                "equityCurve": [],
                "monthlyReturns": [],
                "trades": [],
            })

    # Run composed strategies
    for comp in (composed_strategies or []):
        comp_id = comp.get("id", "composed")
        comp_name = comp.get("name", "组合策略")
        sub_strats = comp.get("sub_strategies", [])
        comp_method = comp.get("composition_method", "voting")
        result = run_backtest(
            initial_capital=initial_capital,
            start_date=start_date,
            end_date=end_date,
            strategy="composed",
            risk_tolerance=risk_tolerance,
            symbols=symbols,
            rebalance_days=rebalance_days,
            commission_rate=commission_rate,
            stamp_tax=stamp_tax,
            sub_strategies=sub_strats,
            composition_method=comp_method,
        )
        if "error" not in result:
            result["summary"]["strategy"] = f"composed:{comp_id}"
            result["summary"]["strategyLabel"] = comp_name
            results.append(result)
        else:
            results.append({
                "summary": {
                    "strategy": f"composed:{comp_id}",
                    "strategyLabel": comp_name,
                    "error": result.get("error", "回测失败"),
                },
                "equityCurve": [],
                "monthlyReturns": [],
                "trades": [],
            })

    # Find best strategy
    valid_results = [r for r in results if "error" not in r.get("summary", {})]
    if valid_results:
        best = max(valid_results, key=lambda x: x["summary"]["totalReturn"])
        best_strategy = best["summary"]["strategy"]
    else:
        best_strategy = None

    return {
        "comparison": results,
        "bestStrategy": best_strategy,
        "period": {"startDate": start_date, "endDate": end_date},
        "initialCapital": initial_capital,
        "riskTolerance": risk_tolerance,
    }


if __name__ == "__main__":
    _raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
    config = json.loads(_raw)
    action = config.get("action", "run")

    try:
        if action == "run":
            result = run_backtest(
                initial_capital=config.get("initial_capital", 500000),
                start_date=config.get("start_date", "2025-01-01"),
                end_date=config.get("end_date", "2025-12-31"),
                strategy=config.get("strategy", "momentum"),
                risk_tolerance=config.get("risk_tolerance", "medium"),
                symbols=config.get("symbols"),
                rebalance_days=config.get("rebalance_days", 14),
                commission_rate=config.get("commission_rate", 0.0003),
                stamp_tax=config.get("stamp_tax", 0.001),
                sub_strategies=config.get("sub_strategies"),
                composition_method=config.get("composition_method"),
            )
        elif action == "compare":
            result = compare_strategies(
                initial_capital=config.get("initial_capital", 500000),
                start_date=config.get("start_date", "2025-01-01"),
                end_date=config.get("end_date", "2025-12-31"),
                strategies=config.get("strategies"),
                risk_tolerance=config.get("risk_tolerance", "medium"),
                symbols=config.get("symbols"),
                rebalance_days=config.get("rebalance_days", 14),
                commission_rate=config.get("commission_rate", 0.0003),
                stamp_tax=config.get("stamp_tax", 0.001),
                composed_strategies=config.get("composed_strategies"),
            )
        else:
            result = {"error": f"未知操作: {action}"}
    except Exception as e:
        import traceback
        result = {"error": str(e), "traceback": traceback.format_exc()}

    print(json.dumps(result, ensure_ascii=False))
