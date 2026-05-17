"""
PEG / GARP Screener (Growth at a Reasonable Price).

Peter Lynch's PEG ratio approach:
  PEG = PE / Earnings Growth Rate

  PEG < 1.0  → undervalued growth stock
  PEG 1.0-1.5 → fairly valued
  PEG > 2.0  → overvalued

Combined with GARP criteria:
  - Consistent earnings growth (10-25% sweet spot)
  - Reasonable PE (not extreme)
  - Positive ROE (quality)
  - Revenue also growing
  - Moderate debt
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class PEGScreener(BaseScreener):
    """PEG ratio + GARP screening strategy."""

    screener_id = "peg"
    screener_name = "PEG/GARP 合理价格成长股"
    screener_description = (
        "彼得·林奇PEG选股法+GARP成长策略。"
        "PEG=PE/盈利增速<1为低估, 1-1.5合理。"
        "同时考虑ROE、营收增长、负债率等质量因子。"
    )
    supported_markets = ["cn_stock", "us_stock"]

    _DEFAULTS = {
        "max_peg": 1.5,           # Max PEG to include
        "min_eps_growth": 10.0,    # Min EPS growth %
        "max_eps_growth": 100.0,   # Max (avoid hyper-growth with uncertain sustainability)
        "min_pe": 5.0,             # Too low PE may indicate problems
        "max_pe": 50.0,            # Not too expensive
        "min_roe": 10.0,           # Quality threshold
        "max_debt_ratio": 60.0,    # Conservative debt
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
            # Must have PE and earnings growth
            pe = data.get("pe") or data.get("pe_ttm")
            eps_growth = data.get("eps_growth_yoy") or data.get("net_profit_growth_yoy")

            if pe is None or eps_growth is None:
                continue
            if pe <= thresholds["min_pe"] or pe > thresholds["max_pe"]:
                continue
            if eps_growth < thresholds["min_eps_growth"] or eps_growth > thresholds["max_eps_growth"]:
                continue

            # Compute PEG
            peg = pe / eps_growth
            if peg <= 0 or not math.isfinite(peg):
                continue
            if peg > thresholds["max_peg"]:
                continue

            score = 0.0
            signals: list[str] = []
            factors: dict[str, Any] = {
                "peg": round(peg, 3),
                "pe": pe,
                "eps_growth": eps_growth,
            }

            # PEG scoring
            if peg < 0.5:
                score += 3.0
                signals.append(f"PEG极低({peg:.2f}<0.5)，严重低估")
            elif peg < 1.0:
                score += 2.0
                signals.append(f"PEG<1({peg:.2f})，低估")
            elif peg <= 1.5:
                score += 1.0
                signals.append(f"PEG合理({peg:.2f})")

            # Growth quality: prefer 15-30% sweet spot
            if 15 <= eps_growth <= 30:
                score += 1.0
                signals.append(f"盈利增速{eps_growth:.1f}%(最佳区间)")
            elif 10 <= eps_growth < 15:
                score += 0.5
            elif eps_growth > 30:
                score += 0.3  # High growth but less sustainable

            # Revenue growth alignment
            rev_growth = data.get("revenue_growth_yoy")
            if rev_growth is not None:
                factors["revenue_growth"] = rev_growth
                if rev_growth > 10:
                    score += 0.5
                    signals.append(f"营收同步增长{rev_growth:.1f}%")

            # 3-year CAGR for sustainability check
            cagr = data.get("revenue_growth_3y_cagr")
            if cagr is not None and cagr > 10:
                score += 0.5
                factors["revenue_cagr_3y"] = cagr
                signals.append(f"3年营收CAGR {cagr:.1f}%")

            # ROE quality
            roe = data.get("roe")
            if roe is not None:
                factors["roe"] = roe
                if roe >= thresholds["min_roe"]:
                    score += 1.0
                    signals.append(f"ROE {roe:.1f}%")
                elif roe > 0:
                    score += 0.3

            # Debt check
            debt_ratio = data.get("debt_ratio")
            if debt_ratio is not None:
                factors["debt_ratio"] = debt_ratio
                if debt_ratio <= thresholds["max_debt_ratio"]:
                    score += 0.5
                    if debt_ratio < 30:
                        signals.append(f"低负债({debt_ratio:.1f}%)")
                else:
                    score -= 0.5  # Penalty for high debt

            # Gross margin (quality indicator)
            gm = data.get("gross_margin")
            if gm is not None:
                factors["gross_margin"] = gm
                if gm > 30:
                    score += 0.5
                    signals.append(f"高毛利({gm:.1f}%)")

            # OCF quality
            ocf_ratio = data.get("ocf_to_net_profit")
            if ocf_ratio is not None and ocf_ratio > 0.8:
                score += 0.3
                factors["ocf_to_net_profit"] = ocf_ratio

            if score >= 3.0:
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
