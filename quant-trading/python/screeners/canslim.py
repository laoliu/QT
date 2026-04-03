"""
CAN SLIM Stock Screener.

Based on William O'Neil's CAN SLIM methodology:
  C = Current quarterly earnings growth (≥25% YoY)
  A = Annual earnings growth (≥25% over 3-5 years)
  N = New products, management, price highs
  S = Supply & demand (shares outstanding + volume)
  L = Leader or laggard (relative strength ≥80)
  I = Institutional sponsorship
  M = Market direction (index trend)

Simplified implementation using available financial data.
"""

from __future__ import annotations

from typing import Any, Optional

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class CANSLIMScreener(BaseScreener):
    """CAN SLIM growth + momentum screener."""

    screener_id = "canslim"
    screener_name = "CAN SLIM 选股"
    screener_description = (
        "基于欧奈尔CAN SLIM方法论的成长+动量选股策略。"
        "关注盈利增长(≥25%)、营收增长、相对强度、成交量异动等信号。"
    )
    supported_markets = ["cn_stock", "us_stock"]

    # Default thresholds (can be overridden via params)
    _DEFAULTS = {
        "min_eps_growth": 25.0,       # C: Current EPS growth % YoY
        "min_revenue_growth": 20.0,   # Revenue growth % YoY
        "min_roe": 15.0,              # Return on equity %
        "min_net_margin": 8.0,        # Net profit margin %
        "max_pe": 60.0,               # Don't chase extreme valuations
        "min_pe": 0.0,                # Filter out loss-making (PE<0)
        "price_near_high_pct": 0.85,  # N: Price within 15% of 52-week high
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
            score = 0.0
            signals: list[str] = []
            factors: dict[str, Any] = {}

            # C — Current quarterly earnings growth
            eps_g = data.get("eps_growth_yoy") or data.get("net_profit_growth_yoy")
            if eps_g is not None:
                factors["eps_growth_yoy"] = eps_g
                if eps_g >= thresholds["min_eps_growth"]:
                    score += 2.0
                    signals.append(f"C: 盈利增长{eps_g:.1f}%≥{thresholds['min_eps_growth']}%")
                elif eps_g > 0:
                    score += 0.5
            else:
                continue  # Must have earnings data

            # A — Annual/revenue growth
            rev_g = data.get("revenue_growth_yoy")
            if rev_g is not None:
                factors["revenue_growth_yoy"] = rev_g
                if rev_g >= thresholds["min_revenue_growth"]:
                    score += 1.5
                    signals.append(f"A: 营收增长{rev_g:.1f}%")
                elif rev_g > 0:
                    score += 0.3

            # 3-year CAGR bonus
            cagr_3y = data.get("revenue_growth_3y_cagr")
            if cagr_3y is not None and cagr_3y >= 15:
                score += 1.0
                factors["revenue_cagr_3y"] = cagr_3y
                signals.append(f"A: 3年营收CAGR {cagr_3y:.1f}%")

            # ROE quality
            roe = data.get("roe")
            if roe is not None:
                factors["roe"] = roe
                if roe >= thresholds["min_roe"]:
                    score += 1.0
                    signals.append(f"质量: ROE {roe:.1f}%")

            # Net margin quality
            nm = data.get("net_margin")
            if nm is not None:
                factors["net_margin"] = nm
                if nm >= thresholds["min_net_margin"]:
                    score += 0.5

            # PE filter (avoid extreme valuations and loss-making)
            pe = data.get("pe") or data.get("pe_ttm")
            if pe is not None:
                factors["pe"] = pe
                if pe < thresholds["min_pe"] or pe > thresholds["max_pe"]:
                    continue  # Skip
                if 10 <= pe <= 30:
                    score += 0.5  # Reasonable valuation bonus

            # S — Volume/turnover (higher activity = more institutional interest)
            turnover = data.get("turnover_rate")
            if turnover is not None and turnover > 2.0:
                score += 0.3
                factors["turnover_rate"] = turnover

            # Minimum score threshold
            if score >= 3.0:
                results.append(ScreenerResult(
                    stock_id=code,
                    name=data.get("name", ""),
                    market=market,
                    score=score,
                    factors=factors,
                    signals=signals,
                ))

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)

        # Assign ranks
        for i, r in enumerate(results[:limit]):
            r.rank = i + 1

        return results[:limit]
