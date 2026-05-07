"""
Low-Position Potential Stock Screener — 低位潜力股筛选

Identifies stocks at historically low price levels with solid fundamentals
and early signs of capital inflow. Core logic:

  1. Price near 52-week low (打折股): current / 52w-high < 0.65
  2. PB < 2.5 (reasonable book value)
  3. Piotroski F-Score >= 5 (fundamental quality, avoid value traps)
  4. Volume-weighted smart money signal: rising capital flow while price flat/falling
  5. Low debt + positive cash flow (survivor quality)

Score = position_score + fundamental_score + capital_flow_score
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class LowPositionScreener(BaseScreener):
    """低位潜力股筛选 — 历史低位 + 基本面健康 + 资金流入迹象"""

    screener_id = "low_position"
    screener_name = "低位潜力股"
    screener_description = (
        "低位潜力股筛选策略。寻找股价处于历史低位(打6折)但基本面未恶化的潜力品种。"
        "综合判断：52周低位打折度、PB估值、F-Score基本面质量、资金流向异动。"
        "下行空间有限，上行弹性大，是均值回归的核心品种。"
    )
    supported_markets = ["cn_stock"]

    _DEFAULTS = {
        # Price position filters
        "max_price_ratio": 0.65,     # price / 52w-high < 65% (打35折以上)
        "min_price_ratio": 0.10,     # Avoid near-delisting stocks
        # Valuation
        "max_pb": 2.5,
        "max_pe": 80.0,
        "min_pe": 0.0,               # Skip loss-making
        # Fundamental quality
        "min_fscore": 5,
        "max_debt_ratio": 0.70,
        # Position score weight
        "w_position": 4.0,
        "w_fundamental": 3.0,
        "w_capital": 3.0,
        # Kline analysis
        "min_bars": 60,
        "lookback": 20,
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        cfg = {**self._DEFAULTS, **self.params}
        results: list[ScreenerResult] = []

        for code, data in stock_data.items():
            name = data.get("name", code)
            score = 0.0
            signals: list[str] = []
            factors: dict[str, Any] = {}

            # ── 1. Price position (52-week) ────────────────────────────
            high_52w = data.get("high_52w") or data.get("week52_high")
            low_52w = data.get("low_52w") or data.get("week52_low")
            price = data.get("price") or data.get("close") or data.get("current_price")

            # Try to derive from klines if snapshot fields missing
            klines = data.get("klines", [])
            if (high_52w is None or low_52w is None) and len(klines) >= cfg["min_bars"]:
                closes = [float(k["close"]) for k in klines if k.get("close")]
                window = closes[-252:] if len(closes) >= 252 else closes
                high_52w = max(window)
                low_52w = min(window)
                if price is None and closes:
                    price = closes[-1]

            if high_52w is None or price is None or high_52w <= 0:
                continue

            price_ratio = float(price) / float(high_52w)
            factors["price"] = round(float(price), 3)
            factors["high_52w"] = round(float(high_52w), 3)
            factors["price_ratio_52w"] = round(price_ratio, 3)

            if price_ratio > cfg["max_price_ratio"]:
                continue  # Not low enough
            if price_ratio < cfg["min_price_ratio"]:
                continue  # Possible delisting risk

            # Position score: lower ratio → higher score (max 4 pts)
            position_score = cfg["w_position"] * (1.0 - price_ratio / cfg["max_price_ratio"])
            score += position_score
            discount_pct = round((1.0 - price_ratio) * 100, 1)
            signals.append(f"低位: 距52周高点打{discount_pct}%折扣({price_ratio:.2f}×高点)")

            # Distance from 52w low
            if low_52w and low_52w > 0:
                low_ratio = (float(price) - float(low_52w)) / float(low_52w) * 100
                factors["pct_above_52w_low"] = round(low_ratio, 1)
                if low_ratio < 20:
                    score += 1.0
                    signals.append(f"贴近年内低点(+{low_ratio:.1f}%)")

            # ── 2. Valuation ───────────────────────────────────────────
            pb = data.get("pb")
            pe = data.get("pe") or data.get("pe_ttm")

            if pe is not None:
                factors["pe"] = pe
                if pe <= cfg["min_pe"] or pe > cfg["max_pe"]:
                    continue  # Loss-making or extreme overvaluation
                if pe < 15:
                    score += 1.5
                    signals.append(f"低PE({pe:.1f}x)")
                elif pe < 30:
                    score += 0.8

            if pb is not None:
                factors["pb"] = pb
                if pb > cfg["max_pb"]:
                    continue
                if pb < 1.0:
                    score += 1.5
                    signals.append(f"接近净资产(PB={pb:.2f})")
                elif pb < 1.5:
                    score += 0.8
                    signals.append(f"低PB({pb:.2f})")

            # ── 3. Fundamental quality (F-Score proxy) ─────────────────
            f_score = data.get("f_score")
            if f_score is None:
                f_score = self._estimate_fscore(data)

            factors["f_score"] = f_score
            if f_score < cfg["min_fscore"]:
                continue  # Value trap risk

            fscore_bonus = cfg["w_fundamental"] * (f_score / 9.0)
            score += fscore_bonus
            signals.append(f"F-Score={f_score}/9")

            # ── 4. Debt / cash flow safety ─────────────────────────────
            debt_ratio = data.get("debt_ratio") or data.get("liab_to_asset")
            if debt_ratio is not None:
                factors["debt_ratio"] = round(float(debt_ratio), 3)
                if float(debt_ratio) > cfg["max_debt_ratio"]:
                    continue
                if float(debt_ratio) < 0.40:
                    score += 0.5
                    signals.append(f"低负债率({float(debt_ratio)*100:.0f}%)")

            cash_flow_pos = data.get("operating_cash_flow", 0)
            if cash_flow_pos and float(cash_flow_pos) > 0:
                score += 0.5
                signals.append("经营现金流为正")

            # ── 5. Capital flow signal from klines ────────────────────
            if len(klines) >= cfg["min_bars"]:
                cap_score = self._capital_flow_signal(klines, cfg["lookback"])
                if cap_score > 0:
                    score += cfg["w_capital"] * cap_score
                    signals.append(f"资金流入信号(强度{cap_score:.2f})")
                    factors["capital_flow_signal"] = round(cap_score, 3)

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

    def _estimate_fscore(self, data: dict[str, Any]) -> int:
        """Estimate F-Score from available snapshot data."""
        score = 0
        # Profitability
        roa = data.get("roa", 0) or 0
        if float(roa) > 0:
            score += 1
        ocf = data.get("operating_cash_flow", 0) or 0
        if float(ocf) > 0:
            score += 1
        roa_growth = data.get("roa_growth_yoy", 0) or 0
        if float(roa_growth) > 0:
            score += 1
        # Leverage
        debt_ratio = data.get("debt_ratio", 0.5) or 0.5
        if float(debt_ratio) < 0.5:
            score += 1
        current_ratio = data.get("current_ratio", 1.0) or 1.0
        if float(current_ratio) > 1.0:
            score += 1
        # Efficiency
        gross_margin = data.get("gross_margin", 0) or 0
        if float(gross_margin) > 0.2:
            score += 1
        rev_growth = data.get("revenue_growth_yoy", 0) or 0
        if float(rev_growth) > 0:
            score += 1
        roe = data.get("roe", 0) or 0
        if float(roe) > 5:
            score += 1
        return min(score, 9)

    def _capital_flow_signal(self, klines: list[dict], lookback: int) -> float:
        """Detect smart money accumulation: price flat/down but volume trending up."""
        if len(klines) < lookback * 2:
            return 0.0

        recent = klines[-lookback:]
        prior = klines[-lookback * 2:-lookback]

        closes_r = [float(k["close"]) for k in recent if k.get("close")]
        closes_p = [float(k["close"]) for k in prior if k.get("close")]
        vols_r = [float(k.get("volume", 0)) for k in recent]
        vols_p = [float(k.get("volume", 0)) for k in prior]

        if not closes_r or not closes_p or not vols_r or not vols_p:
            return 0.0

        avg_vol_r = sum(vols_r) / len(vols_r)
        avg_vol_p = sum(vols_p) / len(vols_p)
        price_change = (closes_r[-1] - closes_r[0]) / closes_r[0] if closes_r[0] > 0 else 0
        vol_change = (avg_vol_r - avg_vol_p) / avg_vol_p if avg_vol_p > 0 else 0

        # Accumulation: volume rising while price barely moving or slight down
        if vol_change > 0.3 and -0.10 <= price_change <= 0.05:
            return min(1.0, vol_change * 0.8)
        # Mild signal: any volume increase during price consolidation
        if vol_change > 0.1 and -0.05 <= price_change <= 0.03:
            return 0.4
        return 0.0
