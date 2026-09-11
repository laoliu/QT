"""Small-scale live launch review — programmatic go/no-go decision.

Combines live_readiness gates, capacity estimate, risk contribution,
and generates a structured launch review report with:
  - Gate summary (all 11 gates)
  - Capital limit recommendation
  - Risk warnings
  - Manual checklist items (require human sign-off)
  - Final verdict: APPROVED / BLOCKED / CONDITIONAL

Usage:
    from aipha.governance.launch_review import run_launch_review
    result = run_launch_review("r115_combo")
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)
ROOT = PROJECT_ROOT


@dataclass
class LaunchReview:
    """Structured launch review report."""

    combo_id: str
    review_date: str = ""
    verdict: str = "BLOCKED"  # APPROVED / BLOCKED / CONDITIONAL
    gates_passed: int = 0
    gates_total: int = 0
    blocking_gates: list[str] = field(default_factory=list)
    capital_limit_wan: float = 0.0
    capital_reasoning: str = ""
    risk_warnings: list[str] = field(default_factory=list)
    manual_checklist: list[dict[str, Any]] = field(default_factory=list)
    gate_details: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def run_launch_review(combo_id: str) -> LaunchReview:
    """Execute full launch review for a combo."""
    from aipha.governance.live_readiness import check_live_readiness

    review = LaunchReview(
        combo_id=combo_id,
        review_date=str(date.today()),
    )

    # 1. Run all 11 gates
    readiness = check_live_readiness(combo_id)
    review.gates_passed = readiness.gates_passed
    review.gates_total = readiness.gates_total
    review.gate_details = readiness.gates
    review.blocking_gates = readiness.blockers

    # 2. Assess capital limit
    _assess_capital_limit(combo_id, review)

    # 3. Risk warnings
    _collect_risk_warnings(combo_id, review, readiness)

    # 4. Manual checklist
    _build_manual_checklist(review)

    # 5. Final verdict
    _determine_verdict(review)

    return review


def _assess_capital_limit(combo_id: str, review: LaunchReview) -> None:
    """Determine recommended capital limit based on capacity evidence."""
    # Check paper_registry for expected capital range
    reg_path = ROOT / "configs" / "paper" / "paper_registry.json"
    if reg_path.exists():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        for entry in reg.get("entries", []):
            if entry.get("entry_id") == combo_id:
                expected = entry.get("portfolio_expected", {})
                cap_range = expected.get("capital_range_cn", "")
                if cap_range:
                    # Parse "100-500万" style range → use lower bound
                    parts = cap_range.replace("万", "").split("-")
                    try:
                        lower = float(parts[0])
                        review.capital_limit_wan = lower
                        review.capital_reasoning = (
                            f"Based on portfolio_expected: {cap_range}"
                        )
                    except (ValueError, IndexError):
                        pass
                break

    # Apply small-scale constraint: cap at 100万 for initial launch
    if review.capital_limit_wan > 100:
        review.capital_limit_wan = 100.0
        review.capital_reasoning += (
            "; capped at 100万 for initial small-scale launch (S5)"
        )
    elif review.capital_limit_wan == 0:
        review.capital_limit_wan = 50.0
        review.capital_reasoning = "default 50万 (no capacity evidence)"


def _collect_risk_warnings(
    combo_id: str, review: LaunchReview, readiness: Any
) -> None:
    """Collect risk warnings from gates and metrics."""
    warnings: list[str] = []

    # Check validation report for segment concerns
    val_path = ROOT / "output" / combo_id / "validation_report.json"
    if val_path.exists():
        val = json.loads(val_path.read_text(encoding="utf-8"))
        for seg in val.get("segments", []):
            if seg.get("label") == "post_2022_rule":
                ar = seg.get("annual_return", 0)
                sr = seg.get("sharpe_ratio", 0)
                if ar < 0.10:
                    warnings.append(
                        f"Post-2022 AR={ar:.1%} below 10% threshold"
                    )
                if sr < 0.8:
                    warnings.append(
                        f"Post-2022 SR={sr:.2f} below 0.8 threshold"
                    )

    # Check RC from paper_registry
    reg_path = ROOT / "configs" / "paper" / "paper_registry.json"
    if reg_path.exists():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        for entry in reg.get("entries", []):
            if entry.get("entry_id") == combo_id:
                weights = entry.get("weights", {})
                max_w = max(weights.values()) if weights else 0
                if max_w > 0.50:
                    warnings.append(
                        f"Single component weight {max_w:.0%} > 50% "
                        f"(high concentration risk)"
                    )

    # G2/G4 not passing → warn about limited paper evidence
    for g in review.gate_details:
        if g["gate_id"] == "G2" and not g["passed"]:
            warnings.append("Paper track record < 3 months")
        if g["gate_id"] == "G4" and not g["passed"]:
            warnings.append("No replay NAV comparison available")

    review.risk_warnings = warnings


def _build_manual_checklist(review: LaunchReview) -> None:
    """Build human-required manual checklist items."""
    review.manual_checklist = [
        {
            "item": "Broker account configured",
            "description": "QMT account credentials set in .env",
            "signed_off": False,
        },
        {
            "item": "Capital deposited",
            "description": (
                f"Verify >= {review.capital_limit_wan}万 available in account"
            ),
            "signed_off": False,
        },
        {
            "item": "Alert channel verified",
            "description": "Send test alert via Feishu and confirm receipt",
            "signed_off": False,
        },
        {
            "item": "Kill switch tested",
            "description": "Run emergency_stop.py --dry-run and verify response",
            "signed_off": False,
        },
        {
            "item": "Position limit confirmed",
            "description": "Max single-stock participation rate <= 10%",
            "signed_off": False,
        },
        {
            "item": "Operator designated",
            "description": "Named operator responsible for daily monitoring",
            "signed_off": False,
        },
    ]


def _determine_verdict(review: LaunchReview) -> None:
    """Determine final verdict based on all evidence."""
    # Count hard blockers (gates with blocker=True that failed)
    hard_blockers = [
        g for g in review.gate_details
        if not g["passed"] and g.get("blocker", True)
    ]

    if not hard_blockers:
        review.verdict = "APPROVED"
        review.recommendations.append(
            f"All gates passed. Recommended capital: {review.capital_limit_wan}万"
        )
    elif len(hard_blockers) <= 2 and all(
        g["gate_id"] in ("G2", "G11") for g in hard_blockers
    ):
        # G2 (paper time) and G11 (health data) are time-dependent
        # Allow CONDITIONAL if only these remain
        review.verdict = "CONDITIONAL"
        review.recommendations.append(
            "Time-dependent gates pending. Consider launching with "
            f"reduced capital ({review.capital_limit_wan * 0.5:.0f}万) "
            "while accumulating paper evidence."
        )
        review.recommendations.append(
            "Re-evaluate after 60 trading days of paper track record."
        )
    else:
        review.verdict = "BLOCKED"
        for g in hard_blockers:
            if g["gate_id"] not in ("G2", "G11"):
                review.recommendations.append(
                    f"Fix {g['gate_id']} ({g['gate_name']}): "
                    f"{g['evidence']}"
                )
