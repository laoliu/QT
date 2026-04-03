# coding: utf-8
"""
Event Catalyst Screener — registered screener combining US tech event mapping,
scarcity/uniqueness tags, and capital flow validation.

Integrates into the screener registry so it can be used by screener_engine.py
and the daily planner pipeline alongside existing screeners.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from .base import BaseScreener, ScreenerResult, register_screener


_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _load_json(filename: str) -> dict:
    path = os.path.join(_DATA_DIR, filename)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@register_screener
class EventCatalystScreener(BaseScreener):
    """Screen A-shares based on US tech event catalysts, scarcity, and capital flow."""

    screener_id = "event_catalyst"
    screener_name = "事件催化选股"
    screener_description = (
        "基于美股科技龙头异动事件映射A股对标标的，叠加稀缺性/唯一性标签和主力资金流入验证。"
        "适合尾盘选股，捕捉次日开盘的事件驱动行情。"
    )
    supported_markets = ["cn_stock"]

    _DEFAULTS = {
        "min_us_change_pct": 2.0,       # US stock minimum move threshold
        "require_fund_flow": False,      # Require positive capital flow (disable for screening without live data)
        "scarcity_weight": 20.0,         # Weight for scarcity score
        "catalyst_weight": 15.0,         # Weight for catalyst strength
        "flow_weight": 10.0,             # Weight for capital flow
        "sector_breadth_weight": 5.0,    # Weight for multi-sector mapping
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        params = {**self._DEFAULTS, **self.params}

        mapping_raw = _load_json("us_a_mapping.json")
        mapping = {k: v for k, v in mapping_raw.items() if not k.startswith("_")}
        scarcity_raw = _load_json("scarcity_tags.json")
        scarcity = scarcity_raw.get("stocks", {})

        # Build reverse index: A-share code → list of (us_symbol, sector)
        code_catalysts: dict[str, list[dict]] = {}
        code_sectors: dict[str, set[str]] = {}
        for us_sym, entry in mapping.items():
            a_shares = entry.get("a_shares", {})
            for sector, codes in a_shares.items():
                for code in codes:
                    if code not in code_catalysts:
                        code_catalysts[code] = []
                        code_sectors[code] = set()
                    code_catalysts[code].append({
                        "usSymbol": us_sym,
                        "usName": entry.get("name", us_sym),
                        "sector": sector,
                        "usThemes": entry.get("themes", []),
                    })
                    code_sectors[code].add(sector)

        # Screen stocks that are both in stock_data and in the mapping
        results: list[ScreenerResult] = []

        # If stock_data provided, screen from it; otherwise screen all mapped codes
        target_codes = set(stock_data.keys()) if stock_data else set(code_catalysts.keys())
        mapped_codes = target_codes & set(code_catalysts.keys())

        for code in mapped_codes:
            data = stock_data.get(code, {})
            sc = scarcity.get(code, {})
            catalysts = code_catalysts.get(code, [])
            sectors = code_sectors.get(code, set())

            # Compute scores
            scarcity_score = sc.get("scarcity_score", 0.0)
            catalyst_count = len(set(c["usSymbol"] for c in catalysts))
            sector_count = len(sectors)

            # Fund flow from stock_data if available
            flow_main_net = float(data.get("mainNetIn", data.get("main_net_in", 0)))
            flow_positive = 1.0 if flow_main_net > 0 else (0.0 if flow_main_net == 0 else -0.5)

            # Composite score
            score = (
                scarcity_score * params["scarcity_weight"]
                + catalyst_count * params["catalyst_weight"]
                + sector_count * params["sector_breadth_weight"]
                + flow_positive * params["flow_weight"]
            )

            signals: list[str] = []
            for cat in catalysts[:3]:
                signals.append(
                    f"{cat['usName']}({cat['usSymbol']}) → {cat['sector']}对标"
                )
            if scarcity_score >= 0.8:
                tags = sc.get("tags", [])
                signals.append(f"高稀缺性: {', '.join(tags[:2])}")
            if sc.get("moat"):
                signals.append(f"护城河: {sc['moat']}")
            if flow_main_net > 0:
                signals.append(f"主力资金净流入")

            factors = {
                "scarcity": scarcity_score,
                "catalystCount": catalyst_count,
                "sectorBreadth": sector_count,
                "flowDirection": flow_positive,
            }

            name = data.get("name", sc.get("name", ""))
            results.append(ScreenerResult(
                stock_id=code,
                name=name,
                market=market,
                score=round(score, 4),
                factors=factors,
                signals=signals,
            ))

        # Sort by score, take top
        results.sort(key=lambda x: -x.score)
        for i, r in enumerate(results):
            r.rank = i + 1

        return results[:limit]
