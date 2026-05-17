# coding: utf-8
"""
ST Restructuring Screener — ST070505

Screens ST stocks by their restructuring lifecycle stage, identifying:
- Stocks executing restructuring plans (MAIN-6) — potential turnaround plays
- Stocks with completed restructuring (MAIN-7) — possible cap removal candidates
- Stocks at risk of delisting with no restructuring progress
- Timeline analysis (days in process, deadline proximity)

Integrates with st_restructuring engine for announcement-based tracking.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Optional

from .base import BaseScreener, ScreenerResult, register_screener

# Ensure engines/ is importable
_ENGINE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "engines")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@register_screener
class STRestructuringScreener(BaseScreener):
    """Screen ST stocks by restructuring lifecycle stage."""

    screener_id = "st_restructuring"
    screener_name = "ST重整跟踪"
    screener_description = (
        "跟踪ST0708000100120405"
        "0718040009030001"
        "13000407(MAIN-6)和重整完毕(MAIN-7)0308"
    )
    supported_markets = ["cn_stock"]

    _DEFAULTS = {
        "scan_days": 90,              # Look back N days for announcements
        "target_stages": None,        # Filter specific stages (None = all)
        "min_days_in_process": 0,     # Minimum days since first restructuring signal
        "highlight_executing": True,  # Boost score for MAIN-6/MAIN-7 stocks
        "deadline_alert": True,       # Alert if approaching year-end deadline
    }

    # Stage scoring: higher score = further along in restructuring (more actionable)
    _STAGE_SCORES = {
        "UNKNOWN": 0.0,
        "PRE-0": 10.0,
        "PRE-1": 15.0,
        "PRE-2": 20.0,
        "PRE-3": 25.0,
        "PRE-4": 30.0,
        "PRE-5": 40.0,
        "PRE-6": 45.0,
        "MAIN-1": 55.0,
        "MAIN-2": 60.0,
        "MAIN-3": 65.0,
        "MAIN-4": 70.0,
        "MAIN-5": 80.0,
        "MAIN-6": 90.0,
        "MAIN-7": 95.0,
        "REMOVED": 100.0,
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 50,
    ) -> list[ScreenerResult]:
        """Screen ST stocks based on restructuring tracking data.

        stock_data can be:
          1. Pre-loaded restructuring records: {code: {name, current_stage, ...}}
          2. Empty dict: will trigger a live scan via engine
        """
        from aipha.engines.st_restructuring import scan_all_restructuring, get_status, STAGES

        target_stages = self.params.get("target_stages") or self._DEFAULTS.get("target_stages")
        scan_days = self.params.get("scan_days", self._DEFAULTS["scan_days"])
        min_days = self.params.get("min_days_in_process", self._DEFAULTS["min_days_in_process"])
        highlight = self.params.get("highlight_executing", self._DEFAULTS["highlight_executing"])
        deadline_alert = self.params.get("deadline_alert", self._DEFAULTS["deadline_alert"])

        # If stock_data already contains restructuring records, use them directly
        if stock_data and any("current_stage" in v for v in stock_data.values()):
            records = stock_data
        else:
            # Live scan
            tracked = scan_all_restructuring(days=scan_days)
            records = {code: r.to_dict() for code, r in tracked.items()}

        results: list[ScreenerResult] = []
        now = datetime.now()
        year_end = datetime(now.year, 12, 31)
        days_to_year_end = (year_end - now).days
        # 保壳生死线: Nov 25 deadline for court acceptance
        deadline = datetime(now.year, 11, 25)
        days_to_deadline = (deadline - now).days

        for code, data in records.items():
            stage = data.get("current_stage", "UNKNOWN")
            name = data.get("name", "")
            days_in_process = data.get("days_in_process", 0)

            # Filter by target stages
            if target_stages and stage not in target_stages:
                continue

            # Filter by minimum days
            if days_in_process < min_days:
                continue

            # Base score from stage progression
            score = self._STAGE_SCORES.get(stage, 0.0)

            signals: list[str] = []
            factors: dict[str, Any] = {
                "stage": stage,
                "stage_label": data.get("stage_label", STAGES.get(stage, "")),
                "days_in_process": days_in_process,
                "risk_level": data.get("risk_level", ""),
                "is_st": data.get("is_st", False),
                "announcement_count": len(data.get("announcements", [])),
            }

            # Signal generation
            if stage == "MAIN-7":
                signals.append("73 重整计划执行完毕 — 00000900")
                score += 5.0
            elif stage == "MAIN-6":
                signals.append("7515 正在执行重整计划")
                if highlight:
                    score += 3.0
            elif stage == "MAIN-5":
                signals.append("77 020607")
            elif stage == "REMOVED":
                signals.append("95 030005/0009")
            elif stage.startswith("PRE-"):
                signals.append(f"77 0000: {data.get('stage_label', '')}")

            # Deadline alert for pre-restructuring stocks
            if deadline_alert and stage.startswith("PRE-"):
                if days_to_deadline < 60:
                    signals.append(f"7215 距保壳生死线(11/25)仅{days_to_deadline}天")
                    factors["deadline_risk"] = True
                    score -= 10.0  # Penalize high deadline risk
                elif days_to_deadline < 120:
                    signals.append(f"91 距保壳生死线{days_to_deadline}天")

            # Duration analysis
            if days_in_process > 365:
                signals.append(f"74 重整已持续{days_in_process}天(>{days_in_process//365}年)")
            elif days_in_process > 180:
                signals.append(f"96 重整持续{days_in_process}天")

            # Risk scoring
            risk = data.get("risk_level", "")
            if risk == "delisting":
                signals.append("92 面临退市")
                factors["delisting_imminent"] = True
            elif risk == "delisting_risk":
                signals.append("07 *ST退市风险")

            # Stage history richness: more history = more transparent process
            history = data.get("stage_history", [])
            if len(history) >= 3:
                factors["stage_transitions"] = len(history)
                signals.append(f"97 04{len(history)}00")

            # Latest announcement
            anns = data.get("announcements", [])
            if anns:
                factors["latest_announcement"] = anns[0].get("title", "")[:60]
                factors["latest_date"] = anns[0].get("date", "")

            results.append(ScreenerResult(
                stock_id=code,
                name=name,
                market="cn_stock",
                score=score,
                factors=factors,
                signals=signals,
            ))

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results[:limit], 1):
            r.rank = i
        return results[:limit]
