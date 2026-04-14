"""
Convertible Bond Quality Screener — A-share CB selection via equity quality factors.

Inspired by R113 cb_stock_quality strategy (SR=1.009, MDD=-16.41%, 45% weight).

Core idea: Select convertible bonds whose underlying stocks are fundamentally
strong, then apply CB-specific valuation filters. This combines equity quality
analysis with CB-specific pricing metrics.

Equity quality factors (from underlying stock):
  1. ROE (Return on Equity) — profitability
  2. F-Score components — financial health
  3. Revenue growth — growth momentum
  4. Debt-to-equity ratio — leverage safety

CB-specific factors:
  5. Conversion premium rate (转股溢价率) — valuation vs underlying
  6. Yield to maturity (到期收益率) — downside protection
  7. Remaining size (剩余规模) — liquidity
  8. Credit rating — default risk
  9. Call protection / force-redemption distance (强赎距离)
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class CbStockQualityScreener(BaseScreener):
    """Convertible Bond screener based on underlying stock quality + CB valuation."""

    screener_id = "cb_stock_quality"
    screener_name = "可转债正股质量筛选"
    screener_description = (
        "结合正股基本面质量和转债估值指标的双维度筛选。"
        "正股质量因子(ROE/F-Score/营收增速/杠杆率) + 转债因子(转股溢价率/到期收益率/剩余规模)。"
        "灵感来自R113 cb_stock_quality策略，SR=1.009，占组合权重45%。"
    )
    supported_markets = ["cn_cb"]

    _DEFAULTS = {
        # Equity quality thresholds
        "min_roe": 8.0,              # ROE > 8%
        "min_fscore": 5,             # Piotroski F-Score >= 5/9
        "min_revenue_growth": 0.0,   # revenue growth > 0%
        "max_debt_equity_ratio": 1.5, # D/E < 1.5

        # CB valuation thresholds
        "max_premium_rate": 30.0,    # conversion premium < 30%
        "min_ytm": -2.0,            # YTM > -2% (some negative allowed for growth)
        "min_remaining_size_yi": 1.0, # remaining size > 1亿 (liquidity)
        "max_remaining_size_yi": 50.0, # < 50亿 (avoid mega issues with low volatility)
        "min_credit_rating": "AA-",  # minimum credit rating

        # Factor weights
        "w_equity_quality": 0.50,    # 50% weight on equity factors
        "w_cb_valuation": 0.30,      # 30% weight on CB valuation
        "w_momentum": 0.20,          # 20% weight on price momentum

        # Filters
        "exclude_forced_redeem": True,  # exclude bonds near forced redemption
        "force_redeem_pct": 130.0,     # underlying > 130% of conversion price triggers alert
        "min_remaining_years": 0.5,     # > 6 months to maturity
    }

    # Credit rating ordering (higher = better)
    _RATING_ORDER = {
        "AAA": 7, "AA+": 6, "AA": 5, "AA-": 4,
        "A+": 3, "A": 2, "A-": 1, "BBB+": 0,
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_cb",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        """Screen convertible bonds by underlying stock quality + CB valuation.

        Args:
            stock_data: {cb_code: {
                # CB fields
                "cb_name": str, "cb_price": float, "conversion_premium_rate": float,
                "ytm": float, "remaining_size_yi": float, "credit_rating": str,
                "remaining_years": float, "conversion_price": float,
                # Underlying stock fields  
                "stock_code": str, "stock_name": str, "stock_price": float,
                "roe": float, "fscore": int, "revenue_growth": float,
                "debt_equity_ratio": float, "pe_ttm": float, "pb": float,
                # Momentum
                "cb_return_20d": float, "stock_return_20d": float,
                "cb_return_60d": float,
            }}
        """
        cfg = {**self._DEFAULTS, **self.params}
        candidates: list[dict[str, Any]] = []

        for code, data in stock_data.items():
            # ── Hard filters ──
            if not self._pass_hard_filters(data, cfg):
                continue

            # ── Score each factor dimension ──
            eq_score = self._equity_quality_score(data, cfg)
            cb_score = self._cb_valuation_score(data, cfg)
            mom_score = self._momentum_score(data)

            # Weighted composite
            total_score = (
                cfg["w_equity_quality"] * eq_score
                + cfg["w_cb_valuation"] * cb_score
                + cfg["w_momentum"] * mom_score
            )

            signals = []
            if eq_score >= 0.7:
                signals.append("正股质量优秀")
            if cb_score >= 0.7:
                signals.append("转债估值合理")
            if data.get("ytm", 0) > 0:
                signals.append(f"正YTM({data.get('ytm', 0):.1f}%)")

            premium = data.get("conversion_premium_rate", 100)
            if premium < 10:
                signals.append(f"低溢价率({premium:.1f}%)")

            candidates.append({
                "code": code,
                "data": data,
                "score": total_score,
                "eq_score": eq_score,
                "cb_score": cb_score,
                "mom_score": mom_score,
                "signals": signals,
            })

        # Sort by score desc
        candidates.sort(key=lambda x: x["score"], reverse=True)

        results = []
        for rank, c in enumerate(candidates[:limit], 1):
            d = c["data"]
            results.append(ScreenerResult(
                stock_id=c["code"],
                name=d.get("cb_name", ""),
                market=market,
                score=c["score"],
                rank=rank,
                factors={
                    "equity_quality_score": round(c["eq_score"], 4),
                    "cb_valuation_score": round(c["cb_score"], 4),
                    "momentum_score": round(c["mom_score"], 4),
                    "roe": d.get("roe", 0),
                    "fscore": d.get("fscore", 0),
                    "revenue_growth": d.get("revenue_growth", 0),
                    "conversion_premium_rate": d.get("conversion_premium_rate", 0),
                    "ytm": d.get("ytm", 0),
                    "remaining_size_yi": d.get("remaining_size_yi", 0),
                    "credit_rating": d.get("credit_rating", ""),
                    "stock_code": d.get("stock_code", ""),
                },
                signals=c["signals"],
            ))

        return results

    def _pass_hard_filters(self, data: dict, cfg: dict) -> bool:
        """Apply hard filters that eliminate candidates entirely."""
        # Credit rating check
        rating = data.get("credit_rating", "")
        min_rating = cfg["min_credit_rating"]
        if rating and min_rating:
            r_val = self._RATING_ORDER.get(rating, -1)
            min_val = self._RATING_ORDER.get(min_rating, 0)
            if r_val < min_val:
                return False

        # Remaining size (liquidity)
        size = data.get("remaining_size_yi", 0)
        if size < cfg["min_remaining_size_yi"]:
            return False
        if size > cfg["max_remaining_size_yi"]:
            return False

        # Remaining years
        years = data.get("remaining_years", 0)
        if years < cfg["min_remaining_years"]:
            return False

        # Force redemption check
        if cfg["exclude_forced_redeem"]:
            conv_price = data.get("conversion_price", 0)
            stock_price = data.get("stock_price", 0)
            if conv_price > 0 and stock_price > 0:
                parity_ratio = stock_price / conv_price * 100
                if parity_ratio >= cfg["force_redeem_pct"]:
                    return False

        # Equity quality hard floors
        roe = data.get("roe")
        if roe is not None and roe < cfg["min_roe"]:
            return False

        de_ratio = data.get("debt_equity_ratio")
        if de_ratio is not None and de_ratio > cfg["max_debt_equity_ratio"]:
            return False

        return True

    def _equity_quality_score(self, data: dict, cfg: dict) -> float:
        """Score the underlying stock's fundamental quality [0, 1]."""
        scores = []

        # ROE: normalize 8% → 0, 25% → 1
        roe = data.get("roe", 0)
        roe_s = _normalize(roe, 8, 25)
        scores.append(("roe", roe_s, 0.35))

        # F-Score: 5 → 0, 9 → 1
        fscore = data.get("fscore", 5)
        fs_s = _normalize(fscore, 5, 9)
        scores.append(("fscore", fs_s, 0.25))

        # Revenue growth: 0% → 0, 30% → 1
        rev_g = data.get("revenue_growth", 0)
        rev_s = _normalize(rev_g, 0, 30)
        scores.append(("rev_growth", rev_s, 0.25))

        # D/E ratio: 1.5 → 0, 0.3 → 1 (lower is better)
        de = data.get("debt_equity_ratio", 1.0)
        de_s = _normalize(de, 1.5, 0.3)  # inverted
        scores.append(("debt_equity", de_s, 0.15))

        return sum(s * w for _, s, w in scores)

    def _cb_valuation_score(self, data: dict, cfg: dict) -> float:
        """Score the CB's valuation attractiveness [0, 1]."""
        scores = []

        # Conversion premium: lower is better. 30% → 0, 0% → 1
        premium = data.get("conversion_premium_rate", 30)
        prem_s = _normalize(premium, cfg["max_premium_rate"], 0)
        scores.append(("premium", prem_s, 0.40))

        # YTM: higher is better. -2% → 0, 3% → 1
        ytm = data.get("ytm", 0)
        ytm_s = _normalize(ytm, -2, 3)
        scores.append(("ytm", ytm_s, 0.30))

        # Remaining size: sweet spot 2-15亿. Too small = illiquid, too large = low vol
        size = data.get("remaining_size_yi", 5)
        if size < 2:
            size_s = size / 2
        elif size <= 15:
            size_s = 1.0
        else:
            size_s = max(0, 1 - (size - 15) / 35)
        scores.append(("size", size_s, 0.15))

        # Credit rating bonus
        rating = data.get("credit_rating", "AA-")
        r_val = self._RATING_ORDER.get(rating, 4)
        rating_s = min(r_val / 7, 1.0)
        scores.append(("rating", rating_s, 0.15))

        return sum(s * w for _, s, w in scores)

    def _momentum_score(self, data: dict) -> float:
        """Score recent price momentum [0, 1]."""
        # 20-day CB return
        ret_20d = data.get("cb_return_20d", 0)
        mom_20 = _normalize(ret_20d, -10, 10)

        # 60-day CB return
        ret_60d = data.get("cb_return_60d", 0)
        mom_60 = _normalize(ret_60d, -15, 20)

        return 0.6 * mom_20 + 0.4 * mom_60


def _normalize(value: float, low: float, high: float) -> float:
    """Normalize value to [0, 1] range. Works with both ascending and descending."""
    if high == low:
        return 0.5
    raw = (value - low) / (high - low)
    return max(0.0, min(1.0, raw))
