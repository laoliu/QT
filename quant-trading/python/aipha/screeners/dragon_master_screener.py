"""
Dragon Master Screener (真龙战法选股器).

实现完整的"真龙战法"选股流程：
  1. 识别大主线板块（≥10只涨停连续2天+有连板高度）
  2. 板块内龙一/龙二/龙三排名（首封时间+封单+稳定性）
  3. 标记最佳买入时机（3板打板 = 散户最佳买点）
  4. 降级推荐（龙一买不到→龙二→龙三→放弃）

Multi-factor model (100 pts):
  - 龙头排名 (30%): 封板时间、封单、稳定性、连板高度
  - 主线强度 (25%): 板块涨停数、连续天数、连板高度
  - 买入时机 (20%): 当前板数、放量信号、打板条件
  - 市值适配 (10%): 流通市值30-150亿最优
  - 市场环境 (15%): 情绪指标
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

from aipha.engines.dragon_master import (
    evaluate_buy_timing,
    generate_board_buy_signal,
    get_fallback_dragons,
    identify_major_theme,
    rank_dragons,
)
from aipha.engines.lianban import (
    _safe_float,
    _safe_int,
    fetch_zt_pool,
    fetch_zt_pool_previous,
)
from aipha.engines.market_sentiment import sentiment_score

from .base import BaseScreener, ScreenerResult, register_screener


@register_screener
class DragonMasterScreener(BaseScreener):
    """真龙战法选股器 — 主线龙头3板打板."""

    screener_id = "dragon_master"
    screener_name = "真龙战法选股"
    screener_description = (
        "真龙战法完整选股策略：识别大主线→定位龙一→3板打板。"
        "多因子评分：龙头排名(30%)、主线强度(25%)、"
        "买入时机(20%)、市值(10%)、市场环境(15%)。"
    )
    supported_markets = ["cn_stock"]

    _DEFAULTS = {
        "min_theme_score": 40,      # 主线最低评分
        "min_zt_count": 10,         # 板块最少涨停数
        "top_themes": 3,            # 最多分析几条主线
        "max_dragon_rank": 3,       # 最多看龙几
        "min_flow_value": 30e8,     # 最小流通市值
        "max_flow_value": 150e8,    # 最大流通市值
        "min_score": 40,            # candidate 最低总分
        "exclude_st": True,
        "best_buy_board": 3,        # 3板=最佳买点
    }

    _WEIGHTS = {
        "dragon_rank": 30,
        "theme_strength": 25,
        "buy_timing": 20,
        "market_cap": 10,
        "environment": 15,
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        """Run true dragon strategy screening.

        This screener fetches its own data from akshare pools.
        stock_data can provide optional klines for volume analysis.
        """
        params = {**self._DEFAULTS, **self.params}
        results: list[ScreenerResult] = []

        # ── Step 1: Get pools ──
        today_pool = fetch_zt_pool()
        yesterday_pool = fetch_zt_pool_previous()

        if today_pool.get("error"):
            return []

        # ── Step 2: Identify themes ──
        theme_result = identify_major_theme(
            today_pool, yesterday_pool,
            min_zt_count=params["min_zt_count"],
        )
        themes = theme_result.get("themes", [])

        # Filter to useful themes
        strong_themes = [
            t for t in themes
            if t["score"] >= params["min_theme_score"]
        ][:params["top_themes"]]

        if not strong_themes:
            strong_themes = themes[:params["top_themes"]]

        # ── Step 3: Get market environment ──
        env = sentiment_score()
        env_score_raw = env.get("score", 50)

        # ── Step 4: For each theme, rank dragons and score candidates ──
        for theme in strong_themes:
            sector = theme["sector"]
            theme_score_val = theme["score"]
            today_stocks = theme.get("today_stocks", [])
            yest_stocks = theme.get("yesterday_stocks", [])

            # Skip empty themes
            if not today_stocks:
                continue

            # Rank dragons in this sector
            dragons = rank_dragons(today_stocks, yest_stocks)

            # Only consider top N dragons
            for dragon in dragons[:params["max_dragon_rank"]]:
                code = dragon["code"]
                name = dragon.get("name", "")
                dragon_score = dragon["dragon_score"]
                dragon_rank = dragon["dragon_rank"]
                d_factors = dragon.get("factors", {})

                # ST filter
                if params["exclude_st"] and ("ST" in name.upper() or "st" in name):
                    continue

                factors: dict[str, Any] = {
                    "code": code,
                    "name": name,
                    "sector": sector,
                    "dragon_rank": dragon_rank,
                    "lian_ban_count": d_factors.get("lian_ban_count", 0),
                }
                signals: list[str] = []

                # ── Factor 1: Dragon rank score (30 pts) ──
                w_dr = self._WEIGHTS["dragon_rank"]
                dr_score = (dragon_score / 100) * w_dr
                factors["dragon_rank_score"] = round(dr_score, 1)

                # Add dragon signals
                signals.extend(dragon.get("signals", []))

                # ── Factor 2: Theme strength (25 pts) ──
                w_th = self._WEIGHTS["theme_strength"]
                th_score = (theme_score_val / 100) * w_th
                factors["theme_strength_score"] = round(th_score, 1)
                factors["theme_score"] = theme_score_val
                factors["today_zt_count"] = theme.get("today_zt_count", 0)

                if theme.get("is_super_theme"):
                    signals.append(f"超级主线({sector},{theme.get('today_zt_count',0)}家涨停)")
                else:
                    signals.append(f"主线({sector},{theme.get('today_zt_count',0)}家涨停)")

                # ── Factor 3: Buy timing (20 pts) ──
                w_bt = self._WEIGHTS["buy_timing"]
                src_stock = next(
                    (s for s in today_stocks if s.get("code") == code), {}
                )
                klines = None
                if stock_data and code in stock_data:
                    klines = stock_data[code].get("klines")

                timing = evaluate_buy_timing(src_stock, klines)
                bt_score = self._score_buy_timing(timing, w_bt)
                factors["buy_timing_score"] = round(bt_score, 1)
                factors["buy_action"] = timing.get("action", "wait")
                factors["board_number"] = timing.get("board_number", 0)
                factors["is_best_timing"] = timing.get("is_best_timing", False)
                factors["volume_signal"] = timing.get("volume_signal", "δ֪")

                if timing.get("is_best_timing"):
                    signals.append(f"★3板最佳买点({timing.get('volume_signal','')})")
                elif timing.get("action") == "buy":
                    signals.append(f"{timing.get('board_number',0)}板可买入")
                elif timing.get("action") == "wait":
                    signals.append(timing.get("reason", "观望"))

                # ── Factor 4: Market cap (10 pts) ──
                w_mc = self._WEIGHTS["market_cap"]
                mc_score = self._score_market_cap(src_stock, params, w_mc)
                factors["market_cap_score"] = round(mc_score, 1)
                flow = _safe_float(src_stock.get("flow_value"))
                if flow > 0:
                    factors["flow_value_yi"] = round(flow / 1e8, 1)

                # ── Factor 5: Environment (15 pts) ──
                w_env = self._WEIGHTS["environment"]
                env_factor = (env_score_raw / 100) * w_env
                factors["environment_score"] = round(env_factor, 1)

                # Seal info
                seal = _safe_float(src_stock.get("seal_amount"))
                if seal > 0:
                    factors["seal_amount_yi"] = round(seal / 1e8, 2)

                # ── Total ──
                total = dr_score + th_score + bt_score + mc_score + env_factor
                total = min(total, 100)
                factors["total_score"] = round(total, 1)

                # Buy method hint
                if timing.get("buy_method"):
                    factors["buy_method"] = timing["buy_method"]

                # risk level
                factors["risk_level"] = timing.get("risk_level", "high")

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

    def _score_buy_timing(self, timing: dict[str, Any], weight: float) -> float:
        """Score buy timing factor."""
        action = timing.get("action", "skip")
        confidence = timing.get("confidence", 0)
        is_best = timing.get("is_best_timing", False)

        if is_best and action == "buy":
            # 3板最佳时机 → 满分
            return weight * min(confidence / 100, 1.0)
        elif action == "buy":
            return weight * min(confidence / 100, 0.8)
        elif action == "wait":
            return weight * 0.3
        return weight * 0.1  # skip

    def _score_market_cap(
        self, stock: dict[str, Any], params: dict, weight: float,
    ) -> float:
        """Score market cap in optimal range."""
        flow = _safe_float(stock.get("flow_value"))
        if flow <= 0:
            return weight * 0.5

        min_cap = params.get("min_flow_value", 30e8)
        max_cap = params.get("max_flow_value", 150e8)

        if min_cap <= flow <= max_cap:
            return weight * 1.0
        elif flow < min_cap:
            return weight * max(flow / min_cap, 0.3)
        else:
            return weight * max(max_cap / flow, 0.3)
