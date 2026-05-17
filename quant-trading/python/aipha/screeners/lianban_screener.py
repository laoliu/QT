"""
Lianban Relay Screener (连板接力选股器).

Scans the market for consecutive limit-up (连板) stocks and scores them
as candidates for N→(N+1) relay trading, with focus on 二进三 (2→3 boards).

Uses a multi-factor quantitative scoring model inspired by
systematic quant approaches:
  - Board quality (板质): turnover rate, board type, open count
  - Volume trend (量能): volume momentum across boards
  - Theme/sector (题材): hot sector membership, sector board density
  - Market cap (市值): optimal circulation market cap range
  - Market environment (市场环境): composite sentiment score
  - Institutional activity (机构): dragon tiger / fund flow signals
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from typing import Any, Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_ROOT)

from aipha.engines.lianban import (
    _safe_float,
    _safe_int,
    board_quality_score,
    count_consecutive_boards,
    detect_limit_up,
    fetch_zt_pool,
    fetch_zt_pool_previous,
)
from aipha.engines.market_sentiment import lianban_ladder, sentiment_score

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class LianbanRelayScreener(BaseScreener):
    """Consecutive limit-up board relay screener (连板接力选股器)."""

    screener_id = "lianban_relay"
    screener_name = "连板接力选股"
    screener_description = (
        "连板接力选股策略，专注N进N+1候选（默认二进三）。"
        "多因子量化评分：板质(30%)、量能(20%)、题材(15%)、"
        "市值(10%)、市场环境(15%)、机构(10%)。"
        "基于统计量化方法，非主观打板。"
    )
    supported_markets = ["cn_stock"]

    _DEFAULTS = {
        "min_boards": 2,          # Minimum consecutive boards to consider
        "target_boards": 3,       # Target (N+1)
        "min_flow_value": 30e8,   # Min circulation market cap (30亿)
        "max_flow_value": 150e8,  # Max circulation market cap (150亿)
        "min_score": 40,          # Minimum total score to include
        "exclude_st": True,       # Exclude ST stocks
        "exclude_one_word": True, # Exclude all-一字板 streaks (can't buy)
    }

    # Factor weights (sum = 100)
    _WEIGHTS = {
        "board_quality": 30,
        "volume": 20,
        "sector": 15,
        "market_cap": 10,
        "environment": 15,
        "institution": 10,
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        """Screen for relay candidates using multi-factor scoring.

        stock_data is optional here — this screener fetches its own data
        from akshare limit-up pool APIs. If stock_data contains "klines",
        they will be used to enhance scoring.
        """
        params = {**self._DEFAULTS, **self.params}
        results: list[ScreenerResult] = []
        warnings: list[str] = []

        min_boards = params["min_boards"]
        exclude_st = params["exclude_st"]
        exclude_one_word = params["exclude_one_word"]

        # ── Step 1: Get today's limit-up pool ──
        pool = fetch_zt_pool()
        if pool.get("error"):
            warnings.append(f"涨停池: {pool['error']}")

        pool_stocks = pool.get("stocks", [])

        # Build lookup by code
        pool_map = {s["code"]: s for s in pool_stocks}

        # ── Step 2: Get market sentiment ──
        env = sentiment_score()
        env_score_raw = env.get("score", 50)
        suitable = env.get("suitable_for_relay", True)

        if not suitable:
            # Market environment is too weak — still screen but note it
            warnings.append(
                f"市场情绪偏弱({env.get('detail', '')}), 二进三风险较高"
            )

        # ── Step 3: Filter candidates (连板 >= min_boards) ──
        candidates: list[dict[str, Any]] = []
        for s in pool_stocks:
            code = s.get("code", "")
            name = s.get("name", "")
            lian_ban = _safe_int(s.get("lian_ban_count"))

            # Must have enough boards
            if lian_ban < min_boards:
                continue

            # ST filter
            if exclude_st and ("ST" in name.upper() or "st" in name):
                continue

            candidates.append(s)

        if not candidates:
            return []

        # ── Step 4: Score each candidate ──
        # Build sector density map (how many ZT stocks per industry)
        sector_counts: dict[str, int] = {}
        sector_board_sum: dict[str, int] = {}
        for s in pool_stocks:
            ind = s.get("industry", "")
            if ind:
                sector_counts[ind] = sector_counts.get(ind, 0) + 1
                sector_board_sum[ind] = (
                    sector_board_sum.get(ind, 0) + max(_safe_int(s.get("lian_ban_count")), 1)
                )

        for s in candidates:
            code = s["code"]
            name = s.get("name", "")
            lian_ban = _safe_int(s.get("lian_ban_count"))

            factors: dict[str, Any] = {
                "code": code,
                "name": name,
                "lian_ban_count": lian_ban,
                "price": _safe_float(s.get("price")),
            }
            signals: list[str] = []

            # ── Factor 1: Board quality (30 pts) ──
            board_q = self._score_board_quality(s, stock_data.get(code))
            if exclude_one_word and board_q.get("all_one_word"):
                continue  # Skip stocks we can't actually buy
            bq_score = board_q["score"]
            factors["board_quality"] = round(bq_score, 1)
            if board_q.get("warnings"):
                for w in board_q["warnings"]:
                    signals.append(f"⚠ {w}")

            # ── Factor 2: Volume (20 pts) ──
            vol_score = self._score_volume(s, stock_data.get(code))
            factors["volume_score"] = round(vol_score, 1)

            # ── Factor 3: Sector/theme (15 pts) ──
            sector = s.get("industry", "")
            sec_score = self._score_sector(sector, sector_counts, sector_board_sum)
            factors["sector_score"] = round(sec_score, 1)
            if sec_score >= 10:
                signals.append(f"热门板块({sector}, {sector_counts.get(sector, 0)}家涨停)")

            # ── Factor 4: Market cap (10 pts) ──
            cap_score = self._score_market_cap(s, params)
            factors["market_cap_score"] = round(cap_score, 1)
            flow_value = _safe_float(s.get("flow_value"))
            if flow_value > 0:
                factors["flow_value_yi"] = round(flow_value / 1e8, 1)

            # ── Factor 5: Market environment (15 pts) ──
            # Scale sentiment 0-100 → 0-15
            env_factor = (env_score_raw / 100) * self._WEIGHTS["environment"]
            factors["environment_score"] = round(env_factor, 1)

            # ── Factor 6: Institutional hint (10 pts) ──
            inst_score = self._score_institutional(s)
            factors["institution_score"] = round(inst_score, 1)

            # ── Total ──
            total = bq_score + vol_score + sec_score + cap_score + env_factor + inst_score
            total = min(total, 100)
            factors["total_score"] = round(total, 1)

            # Board details
            if lian_ban >= 2:
                signals.insert(0, f"{lian_ban}连板")
            turnover = _safe_float(s.get("turnover_rate"))
            if turnover > 0:
                signals.append(f"换手率{turnover:.1f}%")
                factors["turnover_rate"] = round(turnover, 1)
            seal_amount = _safe_float(s.get("seal_amount"))
            if seal_amount > 0:
                factors["seal_amount_yi"] = round(seal_amount / 1e8, 2)
                signals.append(f"封单{seal_amount / 1e8:.2f}亿")
            open_count = _safe_int(s.get("open_count"))
            if open_count > 0:
                factors["open_count"] = open_count
                signals.append(f"开板{open_count}次")

            if total >= params["min_score"]:
                results.append(ScreenerResult(
                    stock_id=code,
                    name=name,
                    market=market,
                    score=total,
                    factors=factors,
                    signals=signals,
                ))

        # Sort and rank
        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results[:limit]):
            r.rank = i + 1
        return results[:limit]

    # ── Scoring sub-methods ──────────────────────────────────────

    def _score_board_quality(
        self, pool_stock: dict[str, Any], extra_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Score board quality using pool data + optional klines.

        Returns {"score": 0-30, "all_one_word": bool, "warnings": []}
        """
        w = self._WEIGHTS["board_quality"]
        warnings: list[str] = []
        lian_ban = _safe_int(pool_stock.get("lian_ban_count"))
        turnover = _safe_float(pool_stock.get("turnover_rate"))
        open_count = _safe_int(pool_stock.get("open_count"))

        # If we have klines, use the full board_quality_score engine
        if extra_data and extra_data.get("klines"):
            klines = extra_data["klines"]
            symbol = pool_stock.get("code", "")
            bq = board_quality_score(klines, symbol)
            # Scale from 0-100 to 0-w
            raw = bq.get("total_score", 50)
            return {
                "score": (raw / 100) * w,
                "all_one_word": all(
                    b.get("board_type") == "一字板" for b in bq.get("boards", [])
                ) if bq.get("boards") else False,
                "warnings": bq.get("warnings", []),
            }

        # Fallback: score from pool data only
        score = w * 0.5  # baseline

        # Turnover: 5-15% optimal
        if 5 <= turnover <= 15:
            score += w * 0.3
        elif 3 <= turnover < 5 or 15 < turnover <= 25:
            score += w * 0.15
        elif turnover > 25:
            warnings.append(f"换手率过高({turnover:.1f}%)")
        elif turnover > 0:
            score += w * 0.1

        # Open count penalty
        if open_count > 3:
            score -= w * 0.15
            warnings.append(f"开板{open_count}次，封板不稳")
        elif open_count > 0:
            score -= w * 0.05 * open_count

        # Board count bonus
        if lian_ban >= 3:
            score += w * 0.1
        if lian_ban >= 5:
            score += w * 0.1

        return {
            "score": max(0, min(score, w)),
            "all_one_word": False,  # can't determine without klines
            "warnings": warnings,
        }

    def _score_volume(
        self, pool_stock: dict[str, Any], extra_data: dict[str, Any] | None = None,
    ) -> float:
        """Score volume trend (0 to weight max)."""
        w = self._WEIGHTS["volume"]
        amount = _safe_float(pool_stock.get("amount"))

        # With klines we could check volume trend; without, use amount as proxy
        if extra_data and extra_data.get("klines"):
            klines = extra_data["klines"]
            volumes = [_safe_float(k.get("volume")) for k in klines[-10:] if k.get("volume")]
            if len(volumes) >= 3:
                # Check last 3 bars for increasing volume
                increasing = sum(
                    1 for i in range(1, len(volumes)) if volumes[i] > volumes[i - 1]
                )
                ratio = increasing / (len(volumes) - 1)
                return ratio * w
            return w * 0.5

        # Fallback: score by amount tier
        if amount >= 20e8:
            return w * 0.9
        elif amount >= 10e8:
            return w * 0.8
        elif amount >= 5e8:
            return w * 0.6
        elif amount >= 1e8:
            return w * 0.4
        return w * 0.2

    def _score_sector(
        self,
        industry: str,
        sector_counts: dict[str, int],
        sector_board_sum: dict[str, int],
    ) -> float:
        """Score sector heat by limit-up density."""
        w = self._WEIGHTS["sector"]
        if not industry:
            return w * 0.3

        count = sector_counts.get(industry, 0)
        board_sum = sector_board_sum.get(industry, 0)

        if count >= 5:
            return w * 1.0  # Very hot sector
        elif count >= 3:
            return w * 0.8
        elif count >= 2:
            return w * 0.6
        elif count >= 1:
            return w * 0.4
        return w * 0.2

    def _score_market_cap(self, pool_stock: dict[str, Any], params: dict) -> float:
        """Score market cap — 30-150亿 optimal."""
        w = self._WEIGHTS["market_cap"]
        flow_value = _safe_float(pool_stock.get("flow_value"))
        if flow_value <= 0:
            return w * 0.5  # Unknown, neutral

        min_cap = params.get("min_flow_value", 30e8)
        max_cap = params.get("max_flow_value", 150e8)

        if min_cap <= flow_value <= max_cap:
            return w * 1.0  # Optimal range
        elif flow_value < min_cap:
            # Too small — higher risk
            ratio = flow_value / min_cap
            return w * max(ratio, 0.3)
        else:
            # Too large — lower volatility, harder to hit board
            ratio = max_cap / flow_value
            return w * max(ratio, 0.3)

    def _score_institutional(self, pool_stock: dict[str, Any]) -> float:
        """Score institutional participation signals.

        Uses seal_amount (封单金额) as a proxy when we don't have
        full dragon-tiger / fund-flow data.
        """
        w = self._WEIGHTS["institution"]
        seal = _safe_float(pool_stock.get("seal_amount"))

        if seal >= 10e8:
            return w * 1.0  # 封单超10亿，大单封板
        elif seal >= 5e8:
            return w * 0.8
        elif seal >= 2e8:
            return w * 0.6
        elif seal >= 1e8:
            return w * 0.4
        elif seal > 0:
            return w * 0.3
        return w * 0.2  # No seal data
