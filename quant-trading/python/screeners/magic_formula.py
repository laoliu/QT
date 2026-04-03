"""
Magic Formula Screener (Joel Greenblatt).

Ranks stocks by two factors:
  1. Earnings Yield = EBIT / Enterprise Value (higher is better)
  2. Return on Capital = EBIT / (Net Working Capital + Net Fixed Assets)
     Simplified as ROIC (higher is better)

Final rank = average of EY rank + ROC rank.
Top-ranked stocks represent cheap, high-quality businesses.

Excludes: financials/banks (different capital structure), utilities, PE < 0.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class MagicFormulaScreener(BaseScreener):
    """Joel Greenblatt's Magic Formula screener."""

    screener_id = "magic_formula"
    screener_name = "Magic Formula 魔法公式"
    screener_description = (
        "Greenblatt魔法公式：按盈利收益率(EBIT/EV)和资本回报率(ROIC)双因子排名。"
        "找到便宜的好公司：高盈利+低估值。"
    )
    supported_markets = ["cn_stock", "us_stock"]

    _DEFAULTS = {
        "min_market_cap_yi": 10.0,  # Minimum market cap (亿)
        "exclude_financials": True,
        "min_pe": 0.0,  # Skip loss-making
    }

    # Industries to exclude (financials, utilities)
    _EXCLUDED_INDUSTRIES = {"银行", "保险", "证券", "多元金融", "电力", "公用事业",
                            "Financial", "Banks", "Insurance", "Utilities"}

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        thresholds = {**self._DEFAULTS, **self.params}
        candidates: list[dict[str, Any]] = []

        for code, data in stock_data.items():
            # Skip loss-making
            pe = data.get("pe") or data.get("pe_ttm")
            if pe is not None and pe <= thresholds["min_pe"]:
                continue

            # Skip financials
            if thresholds["exclude_financials"]:
                industry = data.get("industry", "")
                if any(exc in industry for exc in self._EXCLUDED_INDUSTRIES):
                    continue

            # Market cap filter
            mcap = data.get("total_market_val_yi")
            if mcap is not None and mcap < thresholds["min_market_cap_yi"]:
                continue

            # Need EBIT/EV or at least PE for earnings yield
            ev_to_ebit = data.get("ev_to_ebit")
            ebit = data.get("ebit")
            ev = data.get("enterprise_value")
            roic = data.get("roic")
            roe = data.get("roe")

            # Compute earnings yield
            earnings_yield = None
            if ev_to_ebit and ev_to_ebit > 0:
                earnings_yield = 1.0 / ev_to_ebit * 100  # Convert to %
            elif ebit and ev and ev > 0:
                earnings_yield = ebit / ev * 100
            elif pe and pe > 0:
                earnings_yield = 1.0 / pe * 100  # E/P as fallback

            # Return on capital (ROIC preferred, ROE as fallback)
            roc = roic if roic is not None else roe

            if earnings_yield is None or roc is None:
                continue

            # Skip negative/unreasonable values
            if earnings_yield <= 0 or roc <= 0:
                continue
            if not math.isfinite(earnings_yield) or not math.isfinite(roc):
                continue

            candidates.append({
                "code": code,
                "data": data,
                "earnings_yield": earnings_yield,
                "roc": roc,
            })

        if not candidates:
            return []

        n = len(candidates)

        # Rank by earnings yield (higher = better → lower rank number)
        candidates.sort(key=lambda c: c["earnings_yield"], reverse=True)
        for i, c in enumerate(candidates):
            c["ey_rank"] = i + 1

        # Rank by return on capital (higher = better)
        candidates.sort(key=lambda c: c["roc"], reverse=True)
        for i, c in enumerate(candidates):
            c["roc_rank"] = i + 1

        # Combined rank (lower = better)
        for c in candidates:
            c["combined_rank"] = c["ey_rank"] + c["roc_rank"]

        candidates.sort(key=lambda c: c["combined_rank"])

        results: list[ScreenerResult] = []
        for i, c in enumerate(candidates[:limit]):
            data = c["data"]
            # Score: inverse of combined rank percentile
            score = max(0, (n - c["combined_rank"]) / max(n - 1, 1) * 10)

            factors = {
                "earnings_yield": round(c["earnings_yield"], 2),
                "roc": round(c["roc"], 2),
                "ey_rank": c["ey_rank"],
                "roc_rank": c["roc_rank"],
                "combined_rank": c["combined_rank"],
            }
            pe = data.get("pe") or data.get("pe_ttm")
            if pe:
                factors["pe"] = pe

            signals = [
                f"盈利收益率: {c['earnings_yield']:.1f}% (排名{c['ey_rank']}/{n})",
                f"资本回报率: {c['roc']:.1f}% (排名{c['roc_rank']}/{n})",
                f"综合排名: {c['combined_rank']} / {n * 2}",
            ]

            results.append(ScreenerResult(
                stock_id=c["code"],
                name=data.get("name", ""),
                market=market,
                score=score,
                rank=i + 1,
                factors=factors,
                signals=signals,
            ))

        return results
