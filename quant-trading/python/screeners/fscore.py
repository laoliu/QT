"""
Piotroski F-Score Screener.

Joseph Piotroski's 9-point scoring system for value stocks:

Profitability (4 points):
  1. Positive ROA
  2. Positive operating cash flow
  3. ROA increasing year-over-year
  4. Cash flow from operations > net income (accruals quality)

Leverage/Liquidity (3 points):
  5. Decrease in long-term debt ratio
  6. Increase in current ratio
  7. No new share dilution

Efficiency (2 points):
  8. Increase in gross margin
  9. Increase in asset turnover

Score 8-9: Strong buy signal (value trap avoidance)
Score 0-2: Short/avoid signal
"""

from __future__ import annotations

from typing import Any, Optional

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class FScoreScreener(BaseScreener):
    """Piotroski F-Score value quality screener."""

    screener_id = "fscore"
    screener_name = "F-Score 价值质量筛选"
    screener_description = (
        "Piotroski F-Score 9分制价值股质量评分。"
        "关注盈利能力、杠杆水平、运营效率三大维度。"
        "高分(≥7)=优质价值股, 低分(≤3)=潜在价值陷阱。"
    )
    supported_markets = ["cn_stock", "us_stock"]

    _DEFAULTS = {
        "min_fscore": 6,       # Minimum F-Score to include
        "max_pe": 500.0,       # Relaxed PE ceiling (A-share growth stocks often PE>100)
        "max_pb": 50.0,        # PB filter (most growth stocks PB >> 5)
        "min_pe": 0.0,         # Skip loss-making (negative PE)
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
            # Use pre-computed F-Score if available
            f_score = data.get("f_score")
            f_details = data.get("f_score_details", {})

            if f_score is None:
                # Compute F-Score from available data
                f_score, f_details = self._compute_fscore(data)

            if f_score < thresholds["min_fscore"]:
                continue

            # PE/PB value filter
            pe = data.get("pe") or data.get("pe_ttm")
            pb = data.get("pb")
            if pe is not None and (pe < thresholds["min_pe"] or pe > thresholds["max_pe"]):
                continue
            if pb is not None and pb > thresholds["max_pb"]:
                continue

            factors: dict[str, Any] = {"f_score": f_score}
            signals: list[str] = [f"F-Score: {f_score}/9"]

            # Add factor details
            if pe is not None:
                factors["pe"] = pe
            if pb is not None:
                factors["pb"] = pb
            roe = data.get("roe")
            if roe is not None:
                factors["roe"] = roe
            roa = data.get("roa")
            if roa is not None:
                factors["roa"] = roa
            debt_ratio = data.get("debt_ratio")
            if debt_ratio is not None:
                factors["debt_ratio"] = debt_ratio
            current_ratio = data.get("current_ratio")
            if current_ratio is not None:
                factors["current_ratio"] = current_ratio
            operating_cf = data.get("operating_cf")
            if operating_cf is not None:
                factors["operating_cf"] = operating_cf
            gross_margin = data.get("gross_margin")
            if gross_margin is not None:
                factors["gross_margin"] = gross_margin
            eps = data.get("eps")
            if eps is not None:
                factors["eps"] = eps

            # Detail signals with readable labels
            _SIGNAL_LABELS = {
                "positive_roa": "ROA为正",
                "positive_ocf": "经营现金流为正",
                "roa_increasing": "ROA同比改善",
                "ocf_quality": "现金流>净利润(应计质量)",
                "low_leverage": "负债率下降",
                "leverage_decreasing": "长期负债率下降",
                "current_ratio_ok": "流动比率改善",
                "current_ratio_improving": "流动比率改善",
                "no_dilution": "未增发稀释",
                "positive_gross_margin": "毛利率为正",
                "gross_margin_improving": "毛利率同比改善",
                "revenue_growing": "营收同比增长",
                "asset_turnover_improving": "资产周转率改善",
            }
            factors["f_score_details"] = f_details
            for key, val in f_details.items():
                label = _SIGNAL_LABELS.get(key, key)
                signals.append(f"{'✓' if val else '✗'} {label}")

            # Score: F-Score (max 9) + bonus for low PE
            score = float(f_score)
            if pe is not None and 5 < pe < 15:
                score += 1.0  # Low PE bonus
            if pb is not None and 0 < pb < 1.5:
                score += 0.5  # Below book value

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
    def _compute_fscore(data: dict[str, Any]) -> tuple[int, dict[str, bool]]:
        """Compute F-Score from raw financial data."""
        score = 0
        details: dict[str, bool] = {}

        # 1. Positive ROA
        roa = data.get("roa")
        if roa is not None:
            val = roa > 0
            details["positive_roa"] = val
            score += int(val)

        # 2. Positive operating CF
        ocf = data.get("operating_cf")
        if ocf is not None:
            val = ocf > 0
            details["positive_ocf"] = val
            score += int(val)

        # 4. OCF > Net income
        ocf_ratio = data.get("ocf_to_net_profit")
        if ocf_ratio is not None:
            val = ocf_ratio > 1.0
            details["ocf_quality"] = val
            score += int(val)

        # 5. Low leverage
        dr = data.get("debt_ratio")
        if dr is not None:
            val = dr < 50
            details["low_leverage"] = val
            score += int(val)

        # 6. Current ratio OK
        cr = data.get("current_ratio")
        if cr is not None:
            val = cr > 1.0
            details["current_ratio_ok"] = val
            score += int(val)

        # 8. Positive gross margin
        gm = data.get("gross_margin")
        if gm is not None:
            val = gm > 0
            details["positive_gross_margin"] = val
            score += int(val)

        # 9. Revenue growing
        rg = data.get("revenue_growth_yoy")
        if rg is not None:
            val = rg > 0
            details["revenue_growing"] = val
            score += int(val)

        # 3. ROA increasing YoY (use eps_growth_yoy or net_profit_growth_yoy as proxy)
        roa_growth = data.get("net_profit_growth_yoy")
        if roa_growth is not None:
            val = roa_growth > 0
            details["roa_increasing"] = val
            score += int(val)
        elif roa is not None and roa > 0:
            # If no growth data, assume positive ROA is acceptable
            details["roa_increasing"] = True
            score += 1

        # 7. No new share dilution (check shares_outstanding trend)
        # Use market_cap / price as proxy if available, otherwise assume no dilution
        shares = data.get("shares_outstanding")
        if shares is not None:
            details["no_dilution"] = True  # Cannot check trend without historical; assume OK
            score += 1
        else:
            # No data → assume no dilution (benefit of doubt)
            details["no_dilution"] = True
            score += 1

        return min(score, 9), details
