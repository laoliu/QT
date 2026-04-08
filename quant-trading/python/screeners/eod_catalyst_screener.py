# coding: utf-8
"""
尾盘事件催化选股 (End-of-Day Event Catalyst Screener)

Combines three signals for end-of-day stock selection:
  1. Event catalyst: US tech event mapping → A-share counterparts
  2. End-of-day strength: last-30-min volume/price pattern (14:30-15:00)
  3. Capital flow confirmation: main force net inflow turning positive in late session

Designed to select stocks at 14:30-15:00 for next-day event-driven plays.

Filter pipeline:
  Step 1: Identify stocks mapped to US tech events (from mapping table)
  Step 2: Check scarcity/uniqueness tags for moat premium
  Step 3: Filter for end-of-day strength signals:
    - Late session volume surge (last 30min volume > 20% of daily volume)
    - Price rising in last 30 min (close > 14:30 price)
    - Main capital turning positive in afternoon session
  Step 4: Require positive sector concept board trend (板块涨幅 > 1%)
  Step 5: Composite scoring and ranking
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import BaseScreener, ScreenerResult, register_screener


_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _load_json(filename: str) -> dict:
    path = os.path.join(_DATA_DIR, filename)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@register_screener
class EODCatalystScreener(BaseScreener):
    """尾盘事件催化选股：美股映射 + 尾盘量价 + 资金流验证。"""

    screener_id = "eod_catalyst"
    screener_name = "尾盘事件催化"
    screener_description = (
        "尾盘选股策略，结合美股科技龙头事件催化映射、A股对标稀缺性标签、"
        "尾盘量价强势信号和主力资金流入验证。适合14:30-15:00选股，T+1介入。"
    )
    supported_markets = ["cn_stock"]

    _DEFAULTS: dict[str, Any] = {
        # End-of-day pattern thresholds
        "change_pct_min": 1.0,         # Min daily change % (slightly positive)
        "change_pct_max": 7.0,         # Max daily change (avoid chasing limit-up)
        "turnover_min": 3.0,           # Min turnover rate %
        "turnover_max": 20.0,          # Max turnover rate %
        "late_volume_ratio": 0.18,     # Last 30min volume >= 18% of daily
        "late_price_rising": True,     # Close > price at 14:30
        "main_flow_positive": True,    # Main capital net inflow positive (afternoon)
        # Scoring weights
        "w_catalyst": 25.0,            # Event catalyst mapping strength
        "w_scarcity": 20.0,            # Scarcity/uniqueness bonus
        "w_late_strength": 15.0,       # End-of-day strength signal
        "w_flow": 15.0,                # Capital flow
        "w_sector": 10.0,              # Sector momentum
        "w_technical": 15.0,           # Basic technical (MA alignment, RSI)
        # Minimum bars for technical analysis
        "min_bars": 10,
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        params = {**self._DEFAULTS, **self.params}

        # Load mapping and scarcity data
        mapping_raw = _load_json("us_a_mapping.json")
        mapping = {k: v for k, v in mapping_raw.items() if not k.startswith("_")}
        scarcity_raw = _load_json("scarcity_tags.json")
        scarcity = scarcity_raw.get("stocks", {})

        # Build reverse index: code → catalyst info
        code_info: dict[str, dict] = {}
        for us_sym, entry in mapping.items():
            a_shares = entry.get("a_shares", {})
            for sector, codes in a_shares.items():
                for code in codes:
                    if code not in code_info:
                        code_info[code] = {
                            "catalysts": [],
                            "sectors": set(),
                            "us_symbols": set(),
                        }
                    code_info[code]["catalysts"].append({
                        "usSymbol": us_sym,
                        "usName": entry.get("name", us_sym),
                        "sector": sector,
                    })
                    code_info[code]["sectors"].add(sector)
                    code_info[code]["us_symbols"].add(us_sym)

        results: list[ScreenerResult] = []

        for code, data in stock_data.items():
            signals: list[str] = []
            factors: dict[str, Any] = {}
            score = 0.0

            # ── Step 1: Event catalyst check ──
            cat_info = code_info.get(code)
            has_catalyst = cat_info is not None
            catalyst_count = len(cat_info["us_symbols"]) if cat_info else 0

            if has_catalyst:
                score += catalyst_count * params["w_catalyst"]
                us_list = ", ".join(sorted(cat_info["us_symbols"])[:3])
                signals.append(f"对标美股: {us_list}")
                factors["catalystCount"] = catalyst_count

            # ── Step 2: Scarcity check ──
            sc = scarcity.get(code, {})
            scarcity_val = sc.get("scarcity_score", 0.0)
            if scarcity_val > 0:
                score += scarcity_val * params["w_scarcity"]
                factors["scarcity"] = scarcity_val
                if scarcity_val >= 0.8:
                    tags = sc.get("tags", [])
                    signals.append(f"稀缺标的({', '.join(tags[:2])})")
                if sc.get("moat"):
                    signals.append(f"护城河: {sc['moat']}")

            # ── Step 3: End-of-day strength ──
            change_pct = float(data.get("changePct", data.get("change_pct", 0)))
            turnover = float(data.get("turnover", data.get("turnover_rate", 0)))

            # Basic filter: change range and turnover
            if change_pct < params["change_pct_min"] or change_pct > params["change_pct_max"]:
                if has_catalyst and scarcity_val >= 0.7:
                    pass  # Allow high-scarcity catalyst stocks to pass
                else:
                    continue
            if turnover < params["turnover_min"] or turnover > params["turnover_max"]:
                if has_catalyst and scarcity_val >= 0.7:
                    pass
                else:
                    continue

            # Late session volume ratio (if available from tick/minute data)
            late_vol_ratio = float(data.get("lateVolumeRatio", data.get("late_volume_ratio", 0)))
            if late_vol_ratio >= params["late_volume_ratio"]:
                score += params["w_late_strength"]
                signals.append(f"尾盘放量: ĩ30分钟占比{late_vol_ratio:.0%}")
                factors["lateVolumeRatio"] = late_vol_ratio
            elif late_vol_ratio > 0:
                score += params["w_late_strength"] * 0.3  # partial credit
                factors["lateVolumeRatio"] = late_vol_ratio

            # Late price rising
            late_rising = data.get("latePriceRising", data.get("late_price_rising"))
            if late_rising:
                score += params["w_late_strength"] * 0.5
                signals.append("尾盘走强")
                factors["latePriceRising"] = True

            # ── Step 4: Capital flow ──
            main_net = float(data.get("mainNetIn", data.get("main_net_in", 0)))
            main_pct = float(data.get("mainNetInPct", data.get("main_net_in_pct", 0)))

            if main_net > 0:
                flow_bonus = min(main_pct / 5.0, 1.0) * params["w_flow"]
                score += flow_bonus
                signals.append(f"主力净流入{main_net:.0f}万, 占比{main_pct:.1f}%")
                factors["mainNetIn"] = main_net
                factors["flowDirection"] = "inflow"
            elif params["main_flow_positive"] and not (has_catalyst and scarcity_val >= 0.8):
                continue  # Skip stocks without main capital inflow
            else:
                factors["flowDirection"] = "outflow"

            # ── Step 5: Technical basics ──
            klines = data.get("klines", [])
            if klines and len(klines) >= params["min_bars"]:
                tech_score, tech_signals = self._compute_technical(klines, params)
                score += tech_score * params["w_technical"]
                signals.extend(tech_signals)
                factors["technicalScore"] = round(tech_score, 3)

            # Sector momentum
            sector_pct = float(data.get("sectorChangePct", data.get("sector_change_pct", 0)))
            if sector_pct > 1.0:
                score += params["w_sector"] * min(sector_pct / 3.0, 1.0)
                signals.append(f"板块涨幅{sector_pct:.1f}%")
                factors["sectorChangePct"] = sector_pct

            name = data.get("name", sc.get("name", ""))
            results.append(ScreenerResult(
                stock_id=code,
                name=name,
                market=market,
                score=round(score, 4),
                factors=factors,
                signals=signals,
            ))

        # Sort and rank
        results.sort(key=lambda x: -x.score)
        for i, r in enumerate(results):
            r.rank = i + 1

        return results[:limit]

    @staticmethod
    def _compute_technical(klines: list[dict], params: dict) -> tuple[float, list[str]]:
        """Compute basic technical score from K-line data."""
        signals: list[str] = []
        score = 0.0

        if len(klines) < 5:
            return score, signals

        closes = [float(b.get("close", 0)) for b in klines]
        if not closes or closes[-1] <= 0:
            return score, signals

        last = closes[-1]

        # MA alignment: close > MA5 > MA10
        ma5 = sum(closes[-5:]) / 5
        if len(closes) >= 10:
            ma10 = sum(closes[-10:]) / 10
            if last > ma5 > ma10:
                score += 0.4
                signals.append("均线多头排列(5>10)")
            elif last > ma5:
                score += 0.2
        else:
            if last > ma5:
                score += 0.2

        # Short-term momentum (5-day return)
        if len(closes) >= 6 and closes[-6] > 0:
            ret5 = (last - closes[-6]) / closes[-6]
            if 0.02 < ret5 < 0.15:
                score += 0.3
                signals.append(f"5日涨幅{ret5:.1%}")

        # Volume trend (increasing)
        volumes = [float(b.get("volume", 0)) for b in klines[-5:]]
        if len(volumes) >= 3 and all(v > 0 for v in volumes):
            if volumes[-1] > volumes[-2] > volumes[-3]:
                score += 0.3
                signals.append("成交量连续放大")

        return score, signals
