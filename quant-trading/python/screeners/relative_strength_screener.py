"""
Relative Strength Screener — 相对强度筛选 (William O'Neil RS)

Ranks stocks by their 12-month price performance relative to the market,
following O'Neil's RS Rating concept from How to Make Money in Stocks.

Algorithm:
  - RS Score = (stock 12m return) / (median 12m return of all stocks)
  - Weighted: last quarter weighted 2x for recency
  - RS Rating: percentile rank among all candidates (0-100, higher=stronger)
  - Top 20% (RS >= 80) are "market leaders"

Bonus signals:
  - Price near all-time / 52-week high (N in CAN SLIM)
  - Tight consolidation (low volatility in recent 3 months)
  - Volume expansion on up days
"""

from __future__ import annotations

import math
from typing import Any

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class RelativeStrengthScreener(BaseScreener):
    """相对强度筛选 — 欧奈尔RS评级，找市场领涨股"""

    screener_id = "relative_strength"
    screener_name = "相对强度(RS)筛选"
    screener_description = (
        "基于欧奈尔RS评级的相对强度筛选。计算个股12个月涨幅相对全市场百分位排名，"
        "RS>=80为市场领涨股。辅以近52周高位确认、缩量整理、放量上涨信号。"
        "找强者恒强的真正龙头，需要K线数据。"
    )
    supported_markets = ["cn_stock", "us_stock"]

    _DEFAULTS = {
        "min_rs_rating": 75,       # Minimum RS percentile (0-100)
        "rs_window_days": 252,     # 12-month lookback
        "rs_recent_quarter": 63,   # Recent quarter (weighted 2x)
        "min_bars": 130,           # Minimum bars required
        "tight_range_days": 63,    # 3-month consolidation window
        "max_tight_range_pct": 15, # Max price range during consolidation (%)
        "near_high_threshold": 0.85, # Price >= 85% of 52-week high
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        cfg = {**self._DEFAULTS, **self.params}

        # ── Phase 1: compute raw RS for all stocks ──────────────────
        candidates: list[dict[str, Any]] = []

        for code, data in stock_data.items():
            klines = data.get("klines", [])
            if len(klines) < cfg["min_bars"]:
                # Fallback: use 52w high/low if no klines
                high_52w = data.get("high_52w") or data.get("week52_high")
                low_52w = data.get("low_52w") or data.get("week52_low")
                price = data.get("price") or data.get("close")
                if high_52w and low_52w and price and high_52w > 0:
                    # Approximate RS from position in 52w range
                    raw_rs = (float(price) - float(low_52w)) / (float(high_52w) - float(low_52w))
                    candidates.append({
                        "code": code, "data": data,
                        "raw_rs": raw_rs,
                        "klines": [],
                    })
                continue

            closes = [float(k["close"]) for k in klines if k.get("close")]
            if len(closes) < cfg["min_bars"]:
                continue

            # Weighted RS: full period + 2x recent quarter
            full_window = closes[-cfg["rs_window_days"]:] if len(closes) >= cfg["rs_window_days"] else closes
            recent_window = closes[-cfg["rs_recent_quarter"]:]

            if len(full_window) < 2 or len(recent_window) < 2:
                continue

            rs_full = (full_window[-1] - full_window[0]) / full_window[0] if full_window[0] > 0 else 0
            rs_recent = (recent_window[-1] - recent_window[0]) / recent_window[0] if recent_window[0] > 0 else 0
            raw_rs = (rs_full + 2 * rs_recent) / 3.0

            candidates.append({
                "code": code, "data": data,
                "raw_rs": raw_rs,
                "klines": klines,
                "closes": closes,
            })

        if not candidates:
            return []

        # ── Phase 2: compute RS percentile rating ──────────────────
        raw_rs_values = sorted(c["raw_rs"] for c in candidates)
        n = len(raw_rs_values)

        results: list[ScreenerResult] = []

        for c in candidates:
            raw_rs = c["raw_rs"]
            # Percentile rank (0-100)
            rank_pos = sum(1 for v in raw_rs_values if v <= raw_rs)
            rs_rating = int(rank_pos / n * 100)

            if rs_rating < cfg["min_rs_rating"]:
                continue

            code = c["code"]
            data = c["data"]
            name = data.get("name", code)
            closes = c.get("closes", [])
            klines = c.get("klines", [])

            score = rs_rating / 10.0  # Base 0-10
            signals: list[str] = [f"RS评级={rs_rating}(前{100-rs_rating}%)"]
            factors: dict[str, Any] = {
                "rs_rating": rs_rating,
                "raw_rs_12m": round(raw_rs * 100, 1),
            }

            # ── Bonus: near 52-week high ───────────────────────────
            if closes:
                high_52w = max(closes[-252:]) if len(closes) >= 252 else max(closes)
                price = closes[-1]
                if high_52w > 0:
                    near_high_ratio = price / high_52w
                    factors["near_52w_high"] = round(near_high_ratio, 3)
                    if near_high_ratio >= cfg["near_high_threshold"]:
                        score += 2.0
                        signals.append(f"接近52周高点({near_high_ratio:.0%})")

            # ── Bonus: tight consolidation ─────────────────────────
            if closes and len(closes) >= cfg["tight_range_days"]:
                tight_window = closes[-cfg["tight_range_days"]:]
                tight_high = max(tight_window)
                tight_low = min(tight_window)
                if tight_low > 0:
                    range_pct = (tight_high - tight_low) / tight_low * 100
                    factors["consolidation_range_pct"] = round(range_pct, 1)
                    if range_pct <= cfg["max_tight_range_pct"]:
                        score += 1.5
                        signals.append(f"3月缩量整理({range_pct:.1f}%幅度)")

            # ── Bonus: volume expansion on up days ────────────────
            if klines and len(klines) >= 20:
                up_vol, down_vol = 0.0, 0.0
                for k in klines[-20:]:
                    c_price = float(k.get("close", 0))
                    o_price = float(k.get("open", 0))
                    vol = float(k.get("volume", 0))
                    if c_price > o_price:
                        up_vol += vol
                    elif c_price < o_price:
                        down_vol += vol
                if down_vol > 0 and up_vol / down_vol > 1.5:
                    score += 1.0
                    factors["up_down_vol_ratio"] = round(up_vol / down_vol, 2)
                    signals.append(f"量比↑↓={up_vol/down_vol:.1f}x(涨天量能占优)")

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
