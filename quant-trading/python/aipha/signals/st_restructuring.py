"""ST restructuring lifecycle tracker.

Tracks ST/delisting risk stocks through restructuring stages,
classifies announcements into lifecycle stages via keyword matching,
and maintains persistent state.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["STRestructuringTracker", "RestructuringRecord", "STAGES"]

logger = logging.getLogger(__name__)

# 16 lifecycle stages
STAGES: Dict[str, str] = {
    "PRE-0": "前期筹划",
    "PRE-1": "停牌筹划",
    "PLAN-2": "方案公告",
    "PLAN-3": "股东大会",
    "REG-4": "证监会/交易所审核",
    "REG-5": "审核通过",
    "EXEC-6": "实施中",
    "MAIN-7": "完成/摘帽",
    "FAIL-8": "失败/终止",
    "REMOVED": "已摘帽/退市",
}

# Regex patterns for stage classification
_STAGE_PATTERNS: List[Tuple[str, str]] = [
    (r"摘帽|撤销.?ST|撤销其他风险警示", "REMOVED"),
    (r"重整计划.{0,6}执行完毕|重组完成|资产交割", "MAIN-7"),
    (r"实施.{0,4}重[组整]|过户登记|资产注入", "EXEC-6"),
    (r"核准|审核通过|注册生效|无条件通过", "REG-5"),
    (r"证监会|交易所.{0,4}受理|审[核查]|反馈意见", "REG-4"),
    (r"股东大会.{0,4}通过|临时股东大会|表决", "PLAN-3"),
    (r"重[组整]方案|重[组整]预案|重大资产|发行股份购买", "PLAN-2"),
    (r"停牌.{0,4}筹划|停牌公告|筹划重[组整]", "PRE-1"),
    (r"筹划|初步方案|意向", "PRE-0"),
    (r"终止|失败|未通过|否决|撤回", "FAIL-8"),
]

_STAGE_SCORES: Dict[str, float] = {
    "PRE-0": 10, "PRE-1": 20, "PLAN-2": 35, "PLAN-3": 45,
    "REG-4": 55, "REG-5": 70, "EXEC-6": 80, "MAIN-7": 95,
    "FAIL-8": -20, "REMOVED": 100,
}


@dataclass
class RestructuringRecord:
    """Tracks a single stock's restructuring lifecycle."""

    code: str = ""
    name: str = ""
    current_stage: str = "PRE-0"
    stage_label: str = "前期筹划"
    first_seen: str = ""
    last_updated: str = ""
    days_in_process: int = 0
    announcements: List[Dict[str, str]] = field(default_factory=list)
    stage_history: List[Dict[str, str]] = field(default_factory=list)
    is_st: bool = False
    risk_level: str = "中"


def classify_stage(title: str) -> Optional[str]:
    """Classify an announcement title into a restructuring stage."""
    for pattern, stage in _STAGE_PATTERNS:
        if re.search(pattern, title):
            return stage
    return None


class STRestructuringTracker:
    """Track ST stocks through restructuring lifecycle stages.

    Parameters
    ----------
    state_file : str or None
        Path to JSON state file for persistence.
    """

    def __init__(self, state_file: Optional[str] = None) -> None:
        self._state_file = state_file
        self._records: Dict[str, RestructuringRecord] = {}
        if state_file and os.path.isfile(state_file):
            self._load_state()

    @property
    def records(self) -> Dict[str, RestructuringRecord]:
        return self._records

    def update_from_announcements(
        self, announcements: List[Dict[str, str]],
    ) -> List[str]:
        """Process a list of announcements and update records.

        Parameters
        ----------
        announcements : list of dict
            Each dict has keys: code, name, title, date.

        Returns
        -------
        List of codes whose stage changed.
        """
        changed: List[str] = []
        today = datetime.now().strftime("%Y-%m-%d")
        for ann in announcements:
            code = ann.get("code", "")
            if not code:
                continue
            stage = classify_stage(ann.get("title", ""))
            if stage is None:
                continue
            rec = self._records.get(code)
            if rec is None:
                rec = RestructuringRecord(
                    code=code,
                    name=ann.get("name", ""),
                    first_seen=ann.get("date", today),
                    is_st="ST" in ann.get("name", "").upper(),
                )
                self._records[code] = rec
            old_stage = rec.current_stage
            if stage != old_stage:
                rec.stage_history.append({
                    "from": old_stage, "to": stage,
                    "date": ann.get("date", today),
                    "trigger": ann.get("title", ""),
                })
                rec.current_stage = stage
                rec.stage_label = STAGES.get(stage, stage)
                changed.append(code)
            rec.last_updated = ann.get("date", today)
            rec.announcements.append({
                "title": ann.get("title", ""),
                "date": ann.get("date", today),
                "stage": stage,
            })
            if rec.first_seen:
                try:
                    d0 = datetime.strptime(rec.first_seen, "%Y-%m-%d")
                    d1 = datetime.strptime(rec.last_updated, "%Y-%m-%d")
                    rec.days_in_process = (d1 - d0).days
                except ValueError:
                    pass
            rec.risk_level = self._assess_risk(rec)
        if changed and self._state_file:
            self._save_state()
        return changed

    def get_score(self, code: str) -> float:
        """Get restructuring progress score for a stock."""
        rec = self._records.get(code)
        if not rec:
            return 0.0
        base = _STAGE_SCORES.get(rec.current_stage, 0)
        # deadline risk — long processes have higher risk
        if rec.days_in_process > 365:
            base -= 10
        elif rec.days_in_process > 180:
            base -= 5
        return max(-50, min(100, base))

    def get_actionable(self, min_stage: str = "PLAN-2") -> List[RestructuringRecord]:
        """Get records at or beyond a given stage."""
        stage_order = list(STAGES.keys())
        try:
            min_idx = stage_order.index(min_stage)
        except ValueError:
            min_idx = 0
        results = []
        for rec in self._records.values():
            try:
                idx = stage_order.index(rec.current_stage)
            except ValueError:
                continue
            if idx >= min_idx and rec.current_stage != "FAIL-8":
                results.append(rec)
        results.sort(key=lambda r: self.get_score(r.code), reverse=True)
        return results

    @staticmethod
    def _assess_risk(rec: RestructuringRecord) -> str:
        if rec.current_stage == "FAIL-8":
            return "极高"
        if rec.current_stage in ("MAIN-7", "REMOVED"):
            return "低"
        if rec.days_in_process > 365:
            return "高"
        if rec.days_in_process > 180:
            return "中"
        return "低"

    def _load_state(self) -> None:
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:  # type: ignore[arg-type]
                data = json.load(f)
            for code, d in data.items():
                self._records[code] = RestructuringRecord(**d)
        except Exception as e:
            logger.warning("Failed to load state: %s", e)

    def _save_state(self) -> None:
        try:
            from dataclasses import asdict
            data = {k: asdict(v) for k, v in self._records.items()}
            with open(self._state_file, "w", encoding="utf-8") as f:  # type: ignore[arg-type]
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("Failed to save state: %s", e)
