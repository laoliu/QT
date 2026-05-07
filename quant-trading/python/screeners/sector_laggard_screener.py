"""
Sector Laggard Screener — 板块滞涨补涨选股

Identifies stocks that have lagged behind their sector peers despite similar
fundamentals — classic "catch-up" / 补涨 candidates.

Algorithm:
  1. Group stocks by sector
  2. For each sector, compute median return over the lookback window
  3. Find stocks with sector_return > threshold but individual_return << sector_median
  4. Apply fundamental quality filter to avoid laggards that deserve to lag
  5. Score by: (sector momentum) - (stock lag gap) × quality_multiplier

This is the "板块内补涨" strategy: bet that the laggard will close the gap
once sector momentum continues or institutional rebalancing kicks in.
"""

from __future__ import annotations

from typing import Any

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class SectorLaggardScreener(BaseScreener):
    """板块滞涨补涨选股 — 同板块中涨幅显著落后但基本面相似的品种"""

    screener_id = "sector_laggard"
    screener_name = "板块补涨滞涨股"
    screener_description = (
        "板块补涨滞涨选股策略。在强势板块中寻找涨幅明显落后于同类的个股。"
        "板块整体上涨但个股未能跟涨，存在补涨预期。需要板块分类数据和K线。"
        "适合板块轮动后期，寻找还没启动的「被遗忘的好公司」。"
    )
    supported_markets = ["cn_stock"]

    _DEFAULTS = {
        "lookback_days": 60,           # Sector momentum window
        "min_sector_return": 0.08,     # Sector must be up >= 8% in window
        "max_stock_lag_pct": 0.40,     # Stock return < sector_return × (1 - lag_pct)
        "min_stock_return": -0.15,     # Stock not collapsing (maybe -15% floor)
        "min_fscore": 4,               # Basic quality filter
        "max_pb": 5.0,
        "min_bars": 80,
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        cfg = {**self._DEFAULTS, **self.params}

        # ── Phase 1: compute stock returns + group by sector ────────
        sector_returns: dict[str, list[float]] = {}
        stock_info: list[dict[str, Any]] = []

        for code, data in stock_data.items():
            klines = data.get("klines", [])
            sector = data.get("sector") or data.get("industry") or "未知"

            # Compute return from klines or snapshot
            stock_return = None
            if len(klines) >= cfg["min_bars"]:
                closes = [float(k["close"]) for k in klines if k.get("close")]
                if len(closes) >= cfg["lookback_days"]:
                    window = closes[-cfg["lookback_days"]:]
                    if window[0] > 0:
                        stock_return = (window[-1] - window[0]) / window[0]
            
            if stock_return is None:
                # Fallback: use pre-computed change data
                chg = data.get("change_pct_60d") or data.get("pct_change_60d")
                if chg is not None:
                    stock_return = float(chg) / 100

            if stock_return is None:
                continue

            if sector not in sector_returns:
                sector_returns[sector] = []
            sector_returns[sector].append(stock_return)

            stock_info.append({
                "code": code,
                "data": data,
                "sector": sector,
                "stock_return": stock_return,
                "klines": klines,
            })

        # ── Phase 2: compute sector medians ────────────────────────
        sector_medians: dict[str, float] = {}
        for sector, returns in sector_returns.items():
            if len(returns) >= 3:
                sorted_r = sorted(returns)
                mid = len(sorted_r) // 2
                sector_medians[sector] = sorted_r[mid]

        # ── Phase 3: filter and score ───────────────────────────────
        results: list[ScreenerResult] = []

        for info in stock_info:
            code = info["code"]
            data = info["data"]
            sector = info["sector"]
            stock_return = info["stock_return"]
            klines = info["klines"]

            sector_median = sector_medians.get(sector)
            if sector_median is None:
                continue

            # Sector must be in uptrend
            if sector_median < cfg["min_sector_return"]:
                continue

            # Stock must significantly lag sector
            lag_threshold = sector_median * (1 - cfg["max_stock_lag_pct"])
            if stock_return >= lag_threshold:
                continue  # Not lagging enough

            # Stock floor: not in free fall
            if stock_return < cfg["min_stock_return"]:
                continue

            # ── Fundamental quality filter ──────────────────────────
            pe = data.get("pe") or data.get("pe_ttm")
            pb = data.get("pb")
            f_score = data.get("f_score") or self._quick_fscore(data)

            if f_score < cfg["min_fscore"]:
                continue  # Low quality laggard — probably deserves to lag
            if pb is not None and float(pb) > cfg["max_pb"]:
                continue

            name = data.get("name", code)
            signals: list[str] = []
            factors: dict[str, Any] = {
                "sector": sector,
                "stock_return_60d": round(stock_return * 100, 1),
                "sector_median_return_60d": round(sector_median * 100, 1),
            }

            lag_gap = (sector_median - stock_return) * 100
            factors["lag_gap_pct"] = round(lag_gap, 1)
            signals.append(f"板块涨{sector_median*100:.1f}%,个股仅{stock_return*100:.1f}%(差{lag_gap:.1f}%)")

            # Score = sector strength × quality × lag opportunity
            # Higher sector return + bigger lag gap = bigger opportunity
            sector_strength = min(1.0, sector_median / 0.30)  # normalize to 30% max
            lag_opportunity = min(1.0, lag_gap / 20.0)        # normalize to 20% gap max
            quality_mult = f_score / 9.0

            score = (
                sector_strength * 4.0
                + lag_opportunity * 3.0
                + quality_mult * 3.0
            )

            # Bonus: sector count (more stocks rising = more conviction)
            sector_count = len(sector_returns.get(sector, []))
            rising_in_sector = sum(1 for r in sector_returns.get(sector, []) if r > 0.05)
            if sector_count > 0:
                breadth = rising_in_sector / sector_count
                factors["sector_breadth"] = round(breadth, 2)
                if breadth > 0.60:
                    score += 1.0
                    signals.append(f"板块宽度好({rising_in_sector}/{sector_count}只上涨)")

            # Bonus: fundamental value
            if pe is not None and 0 < float(pe) < 25:
                score += 0.5
                signals.append(f"低PE({float(pe):.1f}x)")
                factors["pe"] = pe
            if pb is not None and float(pb) < 2.0:
                score += 0.5
                signals.append(f"低PB({float(pb):.2f})")
                factors["pb"] = pb

            factors["f_score"] = f_score
            signals.append(f"F-Score={f_score}")

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

    def _quick_fscore(self, data: dict[str, Any]) -> int:
        score = 0
        if float(data.get("roe", 0) or 0) > 5: score += 2
        if float(data.get("revenue_growth_yoy", 0) or 0) > 0: score += 1
        if float(data.get("net_profit_growth_yoy", 0) or 0) > 0: score += 1
        if float(data.get("operating_cash_flow", 0) or 0) > 0: score += 1
        if float(data.get("debt_ratio", 0.6) or 0.6) < 0.6: score += 1
        if float(data.get("gross_margin", 0) or 0) > 0.2: score += 1
        return score
