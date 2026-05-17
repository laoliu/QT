"""Statistical validation gate for strategy lifecycle governance.

Enforces the Principle A/C statistical requirements from
copilot-instructions.md §1.4:

- Every strategy advancing to paper_candidate / paper_ready /
  live_candidate must declare statistical validation results.
- DSR p-value < 0.10 (soft) / < 0.05 (strict)
- PBO (probability of backtest overfitting) < 0.50
- Walk-Forward OOS/IS Sharpe decay <= 50%

Field convention in strategy_catalog.json per strategy:

    "validation": {
        "dsr": {"t_stat": 2.1, "p_value": 0.036},
        "pbo": {"estimate": 0.34, "n_trials": 16},
        "walk_forward": {"oos_sr": 1.2, "is_sr": 1.5, "decay": 0.20,
                         "n_splits": 6, "positive_folds": 5}
    }

This module is READ-ONLY: reports missing/failed validation
without modifying catalog.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]

# Thresholds per copilot-instructions.md
DSR_P_SOFT = 0.10
DSR_P_STRICT = 0.05
PBO_MAX = 0.50
WF_DECAY_MAX = 0.50
WF_MIN_POSITIVE_FOLDS = 5
LIFECYCLE_REQUIRES_STATS = {"paper_candidate", "paper_ready", "live_candidate"}

# TS-2 v2: performance / capacity gates (R156)
GRANDFATHER_DEADLINE = date(2026, 5, 19)

# Performance thresholds (copilot-instructions.md §1.2)
PERF_MIN_AR_TIER_S = 20.0   # Tier S: ≤1000万, AR≥20%
PERF_MIN_AR_TIER_L = 15.0   # Tier L: ≥1亿, AR≥15%
PERF_MIN_AR = 15.0           # Universal minimum (backward compat)
PERF_MIN_SR = 0.8            # Minimum Sharpe ratio
PERF_MAX_MDD_TIER_S = 0.30   # Tier S: MDD≤30%
PERF_MAX_MDD_TIER_L = 0.25   # Tier L: MDD≤25%
PERF_MIN_CALMAR = 0.60       # Calmar≥0.6 universal

# Capacity thresholds (R156 §4.5)
CAPACITY_MIN_TIER_S_CNY = 10_000_000   # 1000万
CAPACITY_MIN_TIER_L_CNY = 200_000_000  # 2亿


@dataclass
class StatisticalIssue:
    """Single statistical gate finding."""
    strategy: str
    severity: str  # error | warning | info
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class StatisticalGateReport:
    """Aggregate statistical gate report."""
    total_checked: int = 0
    total_requiring_stats: int = 0
    passed_count: int = 0
    errors: int = 0
    warnings: int = 0
    issues: list[dict] = field(default_factory=list)

    def passed(self, strict: bool = False) -> bool:
        if self.errors > 0:
            return False
        if strict and self.warnings > 0:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _check_dsr(name: str, dsr: dict | None, strict: bool) -> list[StatisticalIssue]:
    issues: list[StatisticalIssue] = []
    if not dsr or not isinstance(dsr, dict):
        issues.append(StatisticalIssue(
            strategy=name, severity="error", code="STAT-DSR-001",
            message="DSR missing; Principle A requires DSR for paper+",
        ))
        return issues
    p = dsr.get("p_value")
    if p is None:
        issues.append(StatisticalIssue(
            strategy=name, severity="error", code="STAT-DSR-002",
            message="DSR.p_value is null",
        ))
        return issues
    threshold = DSR_P_STRICT if strict else DSR_P_SOFT
    if p >= threshold:
        sev = "error" if strict else "warning"
        issues.append(StatisticalIssue(
            strategy=name, severity=sev, code="STAT-DSR-003",
            message=f"DSR p_value={p:.4f} >= threshold {threshold}",
        ))
    return issues


def _check_pbo(name: str, pbo: dict | None) -> list[StatisticalIssue]:
    issues: list[StatisticalIssue] = []
    if not pbo or not isinstance(pbo, dict):
        issues.append(StatisticalIssue(
            strategy=name, severity="error", code="STAT-PBO-001",
            message="PBO missing; Principle A requires PBO<0.50",
        ))
        return issues
    est = pbo.get("estimate")
    if est is None:
        issues.append(StatisticalIssue(
            strategy=name, severity="error", code="STAT-PBO-002",
            message="PBO.estimate is null",
        ))
        return issues
    if est >= PBO_MAX:
        issues.append(StatisticalIssue(
            strategy=name, severity="error", code="STAT-PBO-003",
            message=f"PBO estimate={est:.3f} >= {PBO_MAX} (overfit risk)",
        ))
    return issues


def _check_walk_forward(name: str, wf: dict | None) -> list[StatisticalIssue]:
    issues: list[StatisticalIssue] = []
    if not wf or not isinstance(wf, dict):
        issues.append(StatisticalIssue(
            strategy=name, severity="error", code="STAT-WF-001",
            message="walk_forward missing; Principle C requires WF",
        ))
        return issues
    oos = wf.get("oos_sr")
    is_sr = wf.get("is_sr")
    decay = wf.get("decay")
    if decay is None and oos is not None and is_sr and is_sr > 0:
        decay = 1.0 - oos / is_sr
    if decay is None:
        issues.append(StatisticalIssue(
            strategy=name, severity="error", code="STAT-WF-002",
            message="walk_forward.decay not computable (need oos_sr+is_sr or decay)",
        ))
    elif decay > WF_DECAY_MAX:
        issues.append(StatisticalIssue(
            strategy=name, severity="error", code="STAT-WF-003",
            message=f"WF decay={decay:.2%} > {WF_DECAY_MAX:.0%} "
                    "(Principle C blocks paper)",
        ))
    pos = wf.get("positive_folds")
    if pos is not None and pos < WF_MIN_POSITIVE_FOLDS:
        issues.append(StatisticalIssue(
            strategy=name, severity="warning", code="STAT-WF-004",
            message=f"only {pos} folds positive, Principle C target >={WF_MIN_POSITIVE_FOLDS}",
        ))
    return issues


def _check_performance(name: str, perf: dict | None) -> list[StatisticalIssue]:
    """TS-2 v2: Check validation.performance exists and meets minimums.

    Checks AR, SR, MDD, Calmar against copilot-instructions §1.2 thresholds.
    Uses the universal (Tier L) minimum as the baseline hard gate.
    """
    issues: list[StatisticalIssue] = []
    if not perf or not isinstance(perf, dict):
        issues.append(StatisticalIssue(
            strategy=name, severity="error", code="STAT-PERF-001",
            message="validation.performance missing (R156 TS-2 requires it)",
        ))
        return issues
    ar = perf.get("annualized_return")
    if ar is not None and ar < PERF_MIN_AR:
        issues.append(StatisticalIssue(
            strategy=name, severity="warning", code="STAT-PERF-002",
            message=f"AR={ar:.2f}% < {PERF_MIN_AR}% minimum",
        ))
    sr = perf.get("sharpe_ratio")
    if sr is not None and sr < PERF_MIN_SR:
        issues.append(StatisticalIssue(
            strategy=name, severity="warning", code="STAT-PERF-003",
            message=f"SR={sr:.3f} < {PERF_MIN_SR} minimum",
        ))
    mdd = perf.get("max_drawdown")
    if mdd is not None and mdd > PERF_MAX_MDD_TIER_L * 100:
        issues.append(StatisticalIssue(
            strategy=name, severity="warning", code="STAT-PERF-004",
            message=f"MDD={mdd:.2f}% > {PERF_MAX_MDD_TIER_L:.0%} Tier L max",
        ))
    calmar = perf.get("calmar_ratio")
    if calmar is not None and calmar < PERF_MIN_CALMAR:
        issues.append(StatisticalIssue(
            strategy=name, severity="warning", code="STAT-PERF-005",
            message=f"Calmar={calmar:.3f} < {PERF_MIN_CALMAR} minimum",
        ))
    return issues


def _check_validation_capacity(name: str, cap: dict | None) -> list[StatisticalIssue]:
    """TS-2 v2: Check validation.capacity exists and meets thresholds.

    Checks:
    - capacity block exists
    - method is not default placeholder
    - estimated capacity meets Tier S minimum (10M CNY)
    - tier_suitable is not 'none'
    """
    issues: list[StatisticalIssue] = []
    if not cap or not isinstance(cap, dict):
        issues.append(StatisticalIssue(
            strategy=name, severity="error", code="STAT-CAP-001",
            message="validation.capacity missing (R156 TS-2 requires it)",
        ))
        return issues
    method = cap.get("method", "")
    if method == "default_by_pattern":
        issues.append(StatisticalIssue(
            strategy=name, severity="warning", code="STAT-CAP-002",
            message="capacity.method='default_by_pattern' — needs actual estimation",
        ))
    # Check capacity amount (conservative_wan is in 万 CNY)
    cap_wan = cap.get("conservative_wan")
    if cap_wan is not None:
        cap_cny = cap_wan * 10_000
        if cap_cny < CAPACITY_MIN_TIER_S_CNY:
            issues.append(StatisticalIssue(
                strategy=name, severity="warning", code="STAT-CAP-003",
                message=(
                    f"capacity={cap_wan:.1f}万 < "
                    f"{CAPACITY_MIN_TIER_S_CNY/10_000:.0f}万 Tier S minimum"
                ),
            ))
    tier = cap.get("tier_suitable", "unknown")
    if tier == "none":
        issues.append(StatisticalIssue(
            strategy=name, severity="warning", code="STAT-CAP-004",
            message="capacity tier_suitable='none' — insufficient for any tier",
        ))
    return issues


def _is_grandfathered(strat: dict) -> bool:
    """Check if strategy is in grandfather period (before deadline)."""
    if date.today() >= GRANDFATHER_DEADLINE:
        return False
    fill_date = strat.get("capacity_fill_date")
    if fill_date and fill_date < "2026-04-19":
        return True
    return True  # All existing paper_candidates get grandfather until deadline


def _fingerprint_validation(val: dict) -> str:
    """SHA-256 hash of validation block for dedup detection."""
    canonical = json.dumps(val, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _check_single(strat: dict, strict: bool) -> tuple[list[StatisticalIssue], bool]:
    """Check statistical validation for one strategy.

    Returns (issues, requires_stats_bool).
    Alias entries (alias_of != None) are silently skipped."""
    name = strat.get("name", "<unknown>")

    # R156 A-001: skip alias entries
    if strat.get("alias_of"):
        return [], False

    lifecycle = strat.get("lifecycle_stage", "research")
    requires = lifecycle in LIFECYCLE_REQUIRES_STATS
    if not requires:
        return [], False

    val = strat.get("validation") or {}
    if not isinstance(val, dict):
        return [StatisticalIssue(
            strategy=name, severity="error", code="STAT-001",
            message=f"validation field must be dict, got {type(val).__name__}",
        )], True

    issues: list[StatisticalIssue] = []
    issues.extend(_check_dsr(name, val.get("dsr"), strict))
    issues.extend(_check_pbo(name, val.get("pbo")))
    issues.extend(_check_walk_forward(name, val.get("walk_forward")))

    # TS-2 v2 checks: performance + capacity (with grandfather)
    grandfathered = _is_grandfathered(strat)
    perf_issues = _check_performance(name, val.get("performance"))
    cap_issues = _check_validation_capacity(name, val.get("capacity"))
    if grandfathered:
        # During grandfather period, downgrade errors to warnings
        for issue in perf_issues + cap_issues:
            if issue.severity == "error":
                issue.severity = "warning"
                issue.message += " [grandfathered until 2026-05-19]"
    issues.extend(perf_issues)
    issues.extend(cap_issues)

    return issues, True


def check_statistical_gate(
    catalog_path: Path | str | None = None,
    strict: bool = False,
) -> StatisticalGateReport:
    """Run statistical gate over all paper-lifecycle strategies.

    Args:
        catalog_path: optional override for strategy_catalog.json
        strict: use DSR p<0.05 threshold (else 0.10)
    """
    cat_path = Path(catalog_path) if catalog_path else ROOT / "configs" / "strategy_catalog.json"
    catalog = json.loads(cat_path.read_text(encoding="utf-8"))

    report = StatisticalGateReport()
    fingerprints: dict[str, str] = {}  # hash -> first strategy name

    for group, items in catalog.items():
        if not isinstance(items, list):
            continue
        for strat in items:
            if not isinstance(strat, dict):
                continue
            report.total_checked += 1

            # Fingerprint dedup check (TS-2 v2)
            name = strat.get("name", "<unknown>")
            val = strat.get("validation")
            if val and isinstance(val, dict) and not strat.get("alias_of"):
                fp = _fingerprint_validation(val)
                if fp in fingerprints:
                    dup_issue = StatisticalIssue(
                        strategy=name, severity="warning",
                        code="STAT-DEDUP-001",
                        message=(
                            f"validation fingerprint matches "
                            f"'{fingerprints[fp]}' — consider alias_of"
                        ),
                    )
                    report.issues.append(dup_issue.to_dict())
                    report.warnings += 1
                else:
                    fingerprints[fp] = name

            issues, requires = _check_single(strat, strict)
            if requires:
                report.total_requiring_stats += 1
                if not issues:
                    report.passed_count += 1
            for issue in issues:
                report.issues.append(issue.to_dict())
                if issue.severity == "error":
                    report.errors += 1
                elif issue.severity == "warning":
                    report.warnings += 1

    return report
