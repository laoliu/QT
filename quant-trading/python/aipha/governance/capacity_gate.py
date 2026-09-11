"""Capacity gate for strategy lifecycle governance.

Enforces the Principle D capacity constraint from
copilot-instructions.md §1.4 and CF-001 binding conclusion:

- Every strategy advancing to paper_ready/live_candidate must
  have a non-null `capacity_conservative_wan` field.
- CB-scoped strategies are hard-capped at 200万 per CF-001.
- Capacity < 50万 is flagged as capacity-constrained (combo
  allocation ≤15% per Principle D).

Used by `ci_runner` (called via --strict) and standalone
governance audits. Read-only: reports issues, does not modify
catalog.

Usage:
    from aipha.governance.capacity_gate import check_capacity_gate
    report = check_capacity_gate(catalog_path)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)
ROOT = PROJECT_ROOT

# Binding constraints from confirmed_research_conclusions.md
CF001_CB_HARD_CAP_WAN = 200.0  # CB per-strategy capacity ceiling
CAPACITY_CONSTRAINED_THRESHOLD_WAN = 50.0  # below -> mark constrained
LIFECYCLE_REQUIRES_CAPACITY = {"paper_candidate", "paper_ready", "live_candidate"}


@dataclass
class CapacityIssue:
    """Single capacity finding for a strategy."""
    strategy: str
    severity: str  # error | warning | info
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class CapacityGateReport:
    """Aggregate capacity gate report."""
    total_checked: int = 0
    errors: int = 0
    warnings: int = 0
    info: int = 0
    issues: list[dict] = field(default_factory=list)

    def passed(self, strict: bool = False) -> bool:
        if self.errors > 0:
            return False
        if strict and self.warnings > 0:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _scoped_for_cb(strat: dict) -> bool:
    """True if strategy operates in CB asset scope."""
    cat = (strat.get("category") or "").lower()
    if cat == "convertible_bond":
        return True
    # Heuristic: strategy name prefix cb_ is CB-scoped
    name = (strat.get("name") or "").lower()
    return name.startswith("cb_")


def _check_single(strat: dict) -> list[CapacityIssue]:
    """Check capacity constraints for a single strategy."""
    issues: list[CapacityIssue] = []
    name = strat.get("name", "<unknown>")

    # R156 A-001: skip alias entries
    if strat.get("alias_of"):
        return issues

    lifecycle = strat.get("lifecycle_stage", "research")
    cap_wan = strat.get("capacity_conservative_wan")

    # Gate 1: strategies past research must declare capacity
    if lifecycle in LIFECYCLE_REQUIRES_CAPACITY:
        if cap_wan is None:
            issues.append(CapacityIssue(
                strategy=name, severity="error", code="CAP-001",
                message=f"lifecycle={lifecycle} but capacity_conservative_wan "
                        "is missing (Principle D requires explicit capacity)",
            ))
            return issues
        if not isinstance(cap_wan, (int, float)):
            issues.append(CapacityIssue(
                strategy=name, severity="error", code="CAP-002",
                message=f"capacity_conservative_wan must be numeric, got "
                        f"{type(cap_wan).__name__}",
            ))
            return issues

    if cap_wan is None:
        return issues

    # Gate 2: CF-001 CB hard cap
    if _scoped_for_cb(strat) and cap_wan > CF001_CB_HARD_CAP_WAN:
        issues.append(CapacityIssue(
            strategy=name, severity="error", code="CAP-003",
            message=f"CB strategy capacity={cap_wan}\u4e07 exceeds CF-001 "
                    f"hard cap {CF001_CB_HARD_CAP_WAN}\u4e07",
        ))

    # Gate 3: capacity-constrained warning
    if cap_wan < CAPACITY_CONSTRAINED_THRESHOLD_WAN:
        issues.append(CapacityIssue(
            strategy=name, severity="warning", code="CAP-004",
            message=f"capacity={cap_wan}\u4e07 < {CAPACITY_CONSTRAINED_THRESHOLD_WAN}\u4e07 "
                    "-> capacity-constrained (combo allocation \u226415% per Principle D)",
        ))

    return issues


def check_capacity_gate(
    catalog_path: Path | str | None = None,
) -> CapacityGateReport:
    """Run capacity gate over all active strategies in catalog."""
    cat_path = Path(catalog_path) if catalog_path else ROOT / "configs" / "strategy_catalog.json"
    catalog = json.loads(cat_path.read_text(encoding="utf-8"))

    report = CapacityGateReport()

    for group, items in catalog.items():
        if not isinstance(items, list):
            continue
        for strat in items:
            if not isinstance(strat, dict):
                continue
            report.total_checked += 1
            for issue in _check_single(strat):
                report.issues.append(issue.to_dict())
                if issue.severity == "error":
                    report.errors += 1
                elif issue.severity == "warning":
                    report.warnings += 1
                else:
                    report.info += 1

    return report
