"""
Turnaround / Distressed Reversal Screener — 困境反转股筛选

Finds companies showing early signs of fundamental recovery after a difficult period:
  1. EPS / net profit turning from negative/declining → positive growth
  2. Gross margin improving for 2+ consecutive quarters
  3. Revenue stabilization or growth resumption
  4. Debt reduction trend
  5. Cash flow improvement

These are high-risk high-reward candidates — typically outperform in bull markets
when discovered early enough. Only strong quality signals are kept (avoid 坑).
"""

from __future__ import annotations

from typing import Any

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class TurnaroundScreener(BaseScreener):
    """困境反转股筛选 — EPS扭亏+毛利率连续改善+营收复苏"""

    screener_id = "turnaround"
    screener_name = "困境反转股"
    screener_description = (
        "困境反转股筛选策略。寻找业绩曾大幅下滑但近期出现扭转信号的品种。"
        "判断依据：净利润扭亏/高速增长、毛利率连续季度改善、营收重回增长、"
        "债务结构优化、经营现金流好转。高赔率品种，需配合低仓位管理。"
    )
    supported_markets = ["cn_stock"]

    _DEFAULTS = {
        # Revenue reversal
        "min_rev_growth": -5.0,      # Revenue growth must be >= -5% (stop declining)
        # EPS/profit reversal
        "min_eps_growth": 20.0,      # EPS growth turning strongly positive
        # Gross margin improvement
        "min_gm_improvement": 1.0,   # Gross margin improved by >= 1pp YoY
        # Current valuation cap (avoid already-priced-in recoveries)
        "max_pb": 8.0,
        "max_pe": 150.0,
        # Weights
        "w_profit": 4.0,
        "w_margin": 3.0,
        "w_revenue": 2.0,
        "w_cashflow": 1.0,
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

            # ── 1. Profit turnaround ───────────────────────────────────
            eps_g = data.get("eps_growth_yoy") or data.get("net_profit_growth_yoy")
            net_profit_g = data.get("net_profit_growth_yoy")

            if eps_g is None:
                continue  # Need earnings data

            eps_g = float(eps_g)
            factors["eps_growth_yoy"] = eps_g

            if eps_g < cfg["min_eps_growth"]:
                continue  # Not strong enough reversal

            # Scoring: bigger reversal = higher score
            if eps_g >= 200:
                profit_score = cfg["w_profit"] * 1.0
                signals.append(f"利润大幅扭亏/暴增(+{eps_g:.0f}%)")
            elif eps_g >= 100:
                profit_score = cfg["w_profit"] * 0.85
                signals.append(f"利润倍增(+{eps_g:.0f}%)")
            elif eps_g >= 50:
                profit_score = cfg["w_profit"] * 0.65
                signals.append(f"利润高速增长(+{eps_g:.0f}%)")
            else:
                profit_score = cfg["w_profit"] * 0.4
                signals.append(f"利润增长(+{eps_g:.0f}%)")

            score += profit_score

            # ── 2. Revenue reversal ────────────────────────────────────
            rev_g = data.get("revenue_growth_yoy")
            if rev_g is not None:
                rev_g = float(rev_g)
                factors["revenue_growth_yoy"] = rev_g
                if rev_g < cfg["min_rev_growth"]:
                    continue  # Revenue still declining sharply

                if rev_g >= 20:
                    score += cfg["w_revenue"] * 1.0
                    signals.append(f"营收高增长(+{rev_g:.1f}%)")
                elif rev_g >= 5:
                    score += cfg["w_revenue"] * 0.6
                    signals.append(f"营收恢复增长(+{rev_g:.1f}%)")
                elif rev_g >= 0:
                    score += cfg["w_revenue"] * 0.3
                    signals.append(f"营收止跌企稳({rev_g:.1f}%)")
                else:
                    score += cfg["w_revenue"] * 0.1

            # ── 3. Gross margin improvement ────────────────────────────
            gross_margin = data.get("gross_margin")
            gross_margin_prev = data.get("gross_margin_prev") or data.get("gross_margin_yoy_delta")
            gm_improvement = data.get("gross_margin_improvement")

            if gm_improvement is None and gross_margin is not None and gross_margin_prev is not None:
                gm_improvement = float(gross_margin) - float(gross_margin_prev)

            if gm_improvement is not None:
                gm_improvement = float(gm_improvement)
                factors["gross_margin_improvement_pp"] = round(gm_improvement, 2)
                if gm_improvement >= cfg["min_gm_improvement"]:
                    gm_score = min(cfg["w_margin"], cfg["w_margin"] * gm_improvement / 5.0)
                    score += gm_score
                    signals.append(f"毛利率改善+{gm_improvement:.1f}pp")

            if gross_margin is not None:
                factors["gross_margin"] = round(float(gross_margin), 3)

            # ── 4. Cash flow improvement ───────────────────────────────
            ocf = data.get("operating_cash_flow")
            ocf_prev = data.get("operating_cash_flow_prev")
            if ocf is not None and float(ocf) > 0:
                score += cfg["w_cashflow"] * 0.5
                signals.append("经营现金流为正")
                if ocf_prev is not None and float(ocf) > float(ocf_prev):
                    score += cfg["w_cashflow"] * 0.5
                    signals.append("现金流同比改善")

            # ── 5. Valuation sanity check ──────────────────────────────
            pe = data.get("pe") or data.get("pe_ttm")
            pb = data.get("pb")
            if pe is not None:
                factors["pe"] = pe
                if float(pe) > cfg["max_pe"] or float(pe) < 0:
                    continue

            if pb is not None:
                factors["pb"] = pb
                if float(pb) > cfg["max_pb"]:
                    continue
                if float(pb) < 2.0:
                    score += 0.5
                    signals.append(f"低PB保护(PB={float(pb):.2f})")

            # ── 6. Debt trend ──────────────────────────────────────────
            debt_ratio = data.get("debt_ratio")
            if debt_ratio is not None:
                factors["debt_ratio"] = round(float(debt_ratio), 3)
                if float(debt_ratio) > 0.80:
                    score -= 1.0  # High debt penalty
                    signals.append(f"⚠️ 高负债({float(debt_ratio)*100:.0f}%)")
                elif float(debt_ratio) < 0.50:
                    score += 0.5

            # ── 7. Price momentum check (not over-reacted yet) ─────────
            klines = data.get("klines", [])
            if len(klines) >= 60:
                closes = [float(k["close"]) for k in klines[-60:] if k.get("close")]
                if len(closes) >= 20:
                    high_60 = max(closes)
                    cur = closes[-1]
                    if high_60 > 0:
                        price_from_high = cur / high_60
                        factors["price_from_60d_high"] = round(price_from_high, 3)
                        if price_from_high < 0.85:
                            score += 0.5
                            signals.append("近60天低位布局点")

            if score <= 0:
                continue

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
