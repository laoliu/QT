"""
support_resistance — Algorithmic support/resistance level calculation.

Calculates key price levels from historical data using multiple methods:
- Swing highs/lows
- Volume-weighted price clusters
- Pivot points (standard, Fibonacci, Camarilla)
- Moving average levels

Input:
  {
    "action": "levels" | "pivot",
    "symbol": "600519",
    "period": "daily",
    "lookback": 60
  }

Output:
  { "symbol": "600519", "levels": {...}, "pivot": {...} }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def run_support_resistance(config: dict) -> dict:
    """Dispatch support/resistance analysis."""
    action = config.get("action", "levels")
    symbol = config.get("symbol", "")

    if not symbol:
        return {"error": "请提供股票代码 (symbol)"}

    lookback = config.get("lookback", 120)

    if action == "levels":
        return _calculate_levels(symbol, lookback)
    elif action == "pivot":
        return _calculate_pivot(symbol)
    else:
        return {"error": f"未知action: {action}"}


def _calculate_levels(symbol: str, lookback: int) -> dict:
    """Calculate comprehensive support/resistance levels."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    errors: list[str] = []

    try:
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
        if df is None or df.empty:
            return {"error": f"无法获取{symbol}的K线数据"}
    except Exception as e:
        return {"error": f"获取K线失败: {str(e)[:100]}"}

    df = df.tail(lookback)
    if len(df) < 20:
        return {"error": f"数据不足，仅有{len(df)}条记录"}

    highs = df["最高"].tolist()
    lows = df["最低"].tolist()
    closes = df["收盘"].tolist()
    volumes = df["成交量"].tolist()
    opens = df["开盘"].tolist()

    current_price = closes[-1]

    # 1. Swing highs/lows (local extrema)
    swing_levels = _find_swing_levels(highs, lows, window=5)

    # 2. Volume-weighted price clusters
    volume_levels = _volume_price_clusters(closes, volumes, n_clusters=5)

    # 3. Moving average levels
    ma_levels = _ma_levels(closes)

    # 4. Fibonacci retracement
    fib_levels = _fibonacci_levels(highs, lows)

    # 5. Recent high/low
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])

    # Combine and categorize as support or resistance
    all_levels: list[dict] = []

    for level in swing_levels:
        level["method"] = "swing"
        all_levels.append(level)

    for level in volume_levels:
        level["method"] = "volume_cluster"
        all_levels.append(level)

    for level in ma_levels:
        level["method"] = "moving_average"
        all_levels.append(level)

    for level in fib_levels:
        level["method"] = "fibonacci"
        all_levels.append(level)

    # Classify as support or resistance
    supports = []
    resistances = []
    for level in all_levels:
        price = level["price"]
        if price < current_price * 0.998:
            level["type"] = "support"
            level["distance_pct"] = round((current_price - price) / current_price * 100, 2)
            supports.append(level)
        elif price > current_price * 1.002:
            level["type"] = "resistance"
            level["distance_pct"] = round((price - current_price) / current_price * 100, 2)
            resistances.append(level)

    # Sort: supports descending (nearest first), resistances ascending
    supports.sort(key=lambda x: x["price"], reverse=True)
    resistances.sort(key=lambda x: x["price"])

    # Keep top levels
    supports = supports[:5]
    resistances = resistances[:5]

    result: dict[str, Any] = {
        "action": "levels",
        "symbol": symbol,
        "current_price": current_price,
        "supports": supports,
        "resistances": resistances,
        "recent_high_20d": round(recent_high, 2),
        "recent_low_20d": round(recent_low, 2),
        "lookback_days": len(df),
        "displayHint": (
            f"支撑位{len(supports)}个、压力位{len(resistances)}个。"
            "请按距离当前价格由近到远排列，标注距离百分比和来源方法。"
        ),
    }
    if errors:
        result["errors"] = errors

    return result


def _calculate_pivot(symbol: str) -> dict:
    """Calculate daily pivot points (Standard, Fibonacci, Camarilla)."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    try:
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
        if df is None or df.empty:
            return {"error": f"无法获取{symbol}的K线数据"}
    except Exception as e:
        return {"error": f"获取K线失败: {str(e)[:100]}"}

    # Use previous day's OHLC for today's pivot
    if len(df) < 2:
        return {"error": "数据不足，至少需要2天"}

    prev = df.iloc[-2]
    h = float(prev["最高"])
    l = float(prev["最低"])
    c = float(prev["收盘"])
    current_price = float(df.iloc[-1]["收盘"])

    # Standard pivot points
    pp = (h + l + c) / 3
    standard = {
        "PP": round(pp, 2),
        "R1": round(2 * pp - l, 2),
        "R2": round(pp + (h - l), 2),
        "R3": round(h + 2 * (pp - l), 2),
        "S1": round(2 * pp - h, 2),
        "S2": round(pp - (h - l), 2),
        "S3": round(l - 2 * (h - pp), 2),
    }

    # Fibonacci pivot points
    fibonacci = {
        "PP": round(pp, 2),
        "R1": round(pp + 0.382 * (h - l), 2),
        "R2": round(pp + 0.618 * (h - l), 2),
        "R3": round(pp + 1.000 * (h - l), 2),
        "S1": round(pp - 0.382 * (h - l), 2),
        "S2": round(pp - 0.618 * (h - l), 2),
        "S3": round(pp - 1.000 * (h - l), 2),
    }

    # Camarilla pivot points
    diff = h - l
    camarilla = {
        "R1": round(c + diff * 1.1 / 12, 2),
        "R2": round(c + diff * 1.1 / 6, 2),
        "R3": round(c + diff * 1.1 / 4, 2),
        "R4": round(c + diff * 1.1 / 2, 2),
        "S1": round(c - diff * 1.1 / 12, 2),
        "S2": round(c - diff * 1.1 / 6, 2),
        "S3": round(c - diff * 1.1 / 4, 2),
        "S4": round(c - diff * 1.1 / 2, 2),
    }

    return {
        "action": "pivot",
        "symbol": symbol,
        "current_price": current_price,
        "prev_high": round(h, 2),
        "prev_low": round(l, 2),
        "prev_close": round(c, 2),
        "standard": standard,
        "fibonacci": fibonacci,
        "camarilla": camarilla,
    }


def _find_swing_levels(highs: list[float], lows: list[float], window: int = 5) -> list[dict]:
    """Find swing highs and swing lows as support/resistance."""
    levels = []
    n = len(highs)

    for i in range(window, n - window):
        # Swing high
        if all(highs[i] >= highs[i - j] for j in range(1, window + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, min(window + 1, n - i))):
            levels.append({
                "price": round(highs[i], 2),
                "strength": _count_touches(highs + lows, highs[i], tolerance=0.015),
                "label": "前高",
            })

        # Swing low
        if all(lows[i] <= lows[i - j] for j in range(1, window + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, min(window + 1, n - i))):
            levels.append({
                "price": round(lows[i], 2),
                "strength": _count_touches(highs + lows, lows[i], tolerance=0.015),
                "label": "前低",
            })

    # Deduplicate nearby levels
    return _deduplicate_levels(levels, tolerance=0.02)


def _volume_price_clusters(closes: list[float], volumes: list[float], n_clusters: int = 5) -> list[dict]:
    """Find price levels with highest volume concentration."""
    if not closes or not volumes:
        return []

    price_min = min(closes)
    price_max = max(closes)
    if price_max == price_min:
        return []

    # Create price bins
    n_bins = 20
    bin_size = (price_max - price_min) / n_bins
    bins: dict[int, float] = {}

    for price, vol in zip(closes, volumes):
        bin_idx = min(int((price - price_min) / bin_size), n_bins - 1)
        bins[bin_idx] = bins.get(bin_idx, 0) + vol

    # Top N bins by volume
    sorted_bins = sorted(bins.items(), key=lambda x: x[1], reverse=True)[:n_clusters]

    levels = []
    for bin_idx, vol in sorted_bins:
        price = price_min + (bin_idx + 0.5) * bin_size
        levels.append({
            "price": round(price, 2),
            "strength": min(int(vol / max(sum(volumes) / n_bins, 1)), 10),
            "label": "量价密集区",
        })

    return levels


def _ma_levels(closes: list[float]) -> list[dict]:
    """Key moving average levels."""
    levels = []
    ma_periods = [
        (5, "MA5"), (10, "MA10"), (20, "MA20"),
        (60, "MA60"), (120, "MA120"), (250, "MA250"),
    ]

    for period, label in ma_periods:
        if len(closes) >= period:
            ma_val = sum(closes[-period:]) / period
            levels.append({
                "price": round(ma_val, 2),
                "strength": min(period // 10, 10),
                "label": label,
            })

    return levels


def _fibonacci_levels(highs: list[float], lows: list[float]) -> list[dict]:
    """Fibonacci retracement levels from recent swing range."""
    if not highs or not lows:
        return []

    high = max(highs)
    low = min(lows)
    diff = high - low

    if diff < 0.01:
        return []

    fib_ratios = [
        (0.236, "Fib 23.6%"),
        (0.382, "Fib 38.2%"),
        (0.500, "Fib 50.0%"),
        (0.618, "Fib 61.8%"),
        (0.786, "Fib 78.6%"),
    ]

    levels = []
    for ratio, label in fib_ratios:
        price = high - diff * ratio
        levels.append({
            "price": round(price, 2),
            "strength": 3 if ratio in (0.382, 0.500, 0.618) else 2,
            "label": label,
        })

    return levels


def _count_touches(prices: list[float], level: float, tolerance: float = 0.015) -> int:
    """Count how many times price touched a level (within tolerance)."""
    count = 0
    for p in prices:
        if abs(p - level) / max(level, 0.01) < tolerance:
            count += 1
    return min(count, 10)


def _deduplicate_levels(levels: list[dict], tolerance: float = 0.02) -> list[dict]:
    """Merge nearby levels, keeping the stronger one."""
    if not levels:
        return []
    sorted_levels = sorted(levels, key=lambda x: x["price"])
    merged = [sorted_levels[0]]

    for level in sorted_levels[1:]:
        prev = merged[-1]
        if abs(level["price"] - prev["price"]) / max(prev["price"], 0.01) < tolerance:
            if level.get("strength", 0) > prev.get("strength", 0):
                merged[-1] = level
        else:
            merged.append(level)

    return merged


# ── CLI entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    try:
        if "--json" in sys.argv:
            raw = sys.stdin.read()
            cfg = json.loads(raw) if raw.strip() else {}
        else:
            cfg = json.loads(os.environ.get("QUANT_CONFIG", "{}"))

        result = run_support_resistance(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
