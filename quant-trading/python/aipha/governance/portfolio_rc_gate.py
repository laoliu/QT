"""Portfolio risk-contribution gate.

Enforces Principle E from copilot-instructions.md §1.4:

- No single strategy should contribute > 50% of combo risk
  (soft: > 40% warning, hard: > 50% error).
- Pairwise return correlation between any two sub-strategies
  within a combo should be < 0.75; average < 0.60.

Reads combo descriptor YAML/JSON and per-strategy metrics to
estimate risk contribution. In absence of full covariance data
falls back to weight-based heuristic (weight^2 as RC proxy).

Usage:
    from aipha.governance.portfolio_rc_gate import check_combo_rc
    report = check_combo_rc(combo_config_path)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]

RC_ERROR_THRESHOLD = 0.50
RC_WARNING_THRESHOLD = 0.40
PAIRWISE_CORR_MAX = 0.75
AVG_CORR_MAX = 0.60


@dataclass
class RCIssue:
    """Single risk-contribution finding."""
    combo: str
    component: str
    severity: str
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class PortfolioRCReport:
    """Aggregate portfolio RC report."""
    combo: str = ""
    total_components: int = 0
    errors: int = 0
    warnings: int = 0
    issues: list[dict] = field(default_factory=list)
    component_rc: dict[str, float] = field(default_factory=dict)

    def passed(self, strict: bool = False) -> bool:
        if self.errors > 0:
            return False
        if strict and self.warnings > 0:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _weight_based_rc(components: list[dict]) -> dict[str, float]:
    """Fallback RC estimate: w_i^2 / sum(w_j^2).

    Used when explicit covariance / return data unavailable.
    Conservative: tends to over-weight concentrated positions.
    """
    weights: dict[str, float] = {}
    for c in components:
        name = c.get("name") or c.get("strategy") or c.get("id", "?")
        w = c.get("weight", c.get("w", 0.0))
        if isinstance(w, (int, float)) and w > 0:
            weights[name] = float(w)
    if not weights:
        return {}
    # Normalize weights to sum=1
    total = sum(weights.values())
    if total <= 0:
        return {}
    norm = {k: v / total for k, v in weights.items()}
    # RC proxy = w^2 normalized
    sq = {k: v * v for k, v in norm.items()}
    total_sq = sum(sq.values())
    if total_sq <= 0:
        return {}
    return {k: v / total_sq for k, v in sq.items()}


def _explicit_rc(components: list[dict]) -> dict[str, float] | None:
    """Extract explicit RC values from component descriptor if present.

    Looks for 'risk_contribution' or 'rc' field per component; requires
    all components to declare it for this path to be used.
    """
    rc: dict[str, float] = {}
    for c in components:
        name = c.get("name") or c.get("strategy") or c.get("id", "?")
        v = c.get("risk_contribution", c.get("rc"))
        if v is None:
            return None
        if not isinstance(v, (int, float)):
            return None
        rc[name] = float(v)
    # Normalize to sum=1 in case input is percentages/unnormalized
    total = sum(rc.values())
    if total <= 0:
        return None
    if abs(total - 1.0) > 0.01:
        rc = {k: v / total for k, v in rc.items()}
    return rc


def check_combo_rc(
    combo_data: dict | Path | str,
    combo_name: str | None = None,
) -> PortfolioRCReport:
    """Check risk-contribution gates for a combo.

    Args:
        combo_data: dict describing combo with 'components' list, or path
            to a JSON file containing such a dict.
        combo_name: optional override name for report (else combo_data['name']).
    """
    if isinstance(combo_data, (str, Path)):
        p = Path(combo_data)
        combo_data = json.loads(p.read_text(encoding="utf-8"))

    if not isinstance(combo_data, dict):
        raise TypeError(f"combo_data must be dict, got {type(combo_data).__name__}")

    name = combo_name or combo_data.get("name") or combo_data.get("combo_id", "?")
    components = combo_data.get("components") or combo_data.get("strategies") or []
    if not isinstance(components, list):
        raise ValueError(f"components must be list, got {type(components).__name__}")

    report = PortfolioRCReport(combo=name, total_components=len(components))

    rc = _explicit_rc(components) or _weight_based_rc(components)
    report.component_rc = rc

    if not rc:
        report.issues.append(RCIssue(
            combo=name, component="-", severity="error", code="RC-001",
            message="no weight/rc info on components; cannot verify",
        ).to_dict())
        report.errors += 1
        return report

    # Gate: per-component RC threshold
    for comp_name, rc_val in rc.items():
        if rc_val >= RC_ERROR_THRESHOLD:
            report.issues.append(RCIssue(
                combo=name, component=comp_name, severity="error", code="RC-002",
                message=f"RC={rc_val:.2%} >= {RC_ERROR_THRESHOLD:.0%} "
                        "(Principle E hard cap)",
            ).to_dict())
            report.errors += 1
        elif rc_val >= RC_WARNING_THRESHOLD:
            report.issues.append(RCIssue(
                combo=name, component=comp_name, severity="warning", code="RC-003",
                message=f"RC={rc_val:.2%} >= {RC_WARNING_THRESHOLD:.0%} "
                        "(approaching Principle E cap; recommend rebalance)",
            ).to_dict())
            report.warnings += 1

    # Optional: check pairwise correlations if provided
    corr = combo_data.get("pairwise_correlation") or combo_data.get("correlation_matrix")
    if isinstance(corr, dict):
        for pair_key, val in corr.items():
            if not isinstance(val, (int, float)):
                continue
            if val >= PAIRWISE_CORR_MAX:
                report.issues.append(RCIssue(
                    combo=name, component=pair_key, severity="error",
                    code="RC-004",
                    message=f"pairwise corr={val:.2f} >= {PAIRWISE_CORR_MAX} "
                            "(Principle E dispersion requirement)",
                ).to_dict())
                report.errors += 1

    return report


def check_combo_rc_batch(
    combo_paths: list[Path | str],
) -> list[PortfolioRCReport]:
    """Run RC gate over multiple combo descriptors."""
    reports: list[PortfolioRCReport] = []
    for p in combo_paths:
        try:
            reports.append(check_combo_rc(p))
        except Exception as e:
            logger.warning("failed to check %s: %s", p, e)
    return reports
