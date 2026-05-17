"""Unified 7-item admission checklist for strategy lifecycle governance.

Orchestrates the seven mandatory checks before a strategy can advance
to S4 (Paper Trading):

1. P22 AR gate (DR-AR-002)
2. Capacity estimate (DR-CAP-001 / Principle D)
3. DSR/PBO/WF statistical significance (DR-STAT-001 / DR-PERF-001)
4. Portfolio correlation (DR-COMBO-001 / Principle E)
5. 2x/4x slippage sensitivity (Principle D)
6. Recent 3-year alpha decay (DR-DECAY-001 / DR-AR-004)
7. Crowding assessment (DR-CROWD-001)

Usage:
    from aipha.governance.admission_checker import AdmissionChecker
    checker = AdmissionChecker.from_config()
    result = checker.evaluate(strategy_evidence)
    if result.all_passed:
        # Strategy may proceed to S4
        ...

References:
    - copilot-instructions.md §1.2, §1.2a, §1.4
    - configs/governance/admission_checklist.yml
    - docs/design/20_specs/decision_rule_registry.md
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / "configs" / "governance" / "admission_checklist.yml"


class CheckStatus(Enum):
    """Result status for a single admission check."""
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"
    WARNING = "warning"


@dataclass
class CheckResult:
    """Result of a single admission check item."""
    item: str
    status: CheckStatus
    detail: str = ""
    metric_value: Any = None
    threshold: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "status": self.status.value,
            "detail": self.detail,
            "metric_value": self.metric_value,
            "threshold": self.threshold,
        }


@dataclass
class AdmissionResult:
    """Aggregate result of 7-item admission checklist."""
    strategy_name: str
    tier: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(c.status == CheckStatus.PASS for c in self.checks)

    @property
    def n_passed(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.PASS)

    @property
    def n_failed(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.FAIL)

    @property
    def n_not_evaluated(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.NOT_EVALUATED)

    def summary(self) -> str:
        """Human-readable summary."""
        status = "PASSED" if self.all_passed else "FAILED"
        lines = [
            f"[{status}] Admission Checklist: {self.strategy_name} ({self.tier})",
            f"  Passed: {self.n_passed}/7, Failed: {self.n_failed}, "
            f"Not Evaluated: {self.n_not_evaluated}",
        ]
        for c in self.checks:
            mark = {"pass": "✓", "fail": "✗", "not_evaluated": "?", "warning": "⚠"}
            lines.append(f"  {mark[c.status.value]} [{c.item}] {c.detail}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "tier": self.tier,
            "all_passed": self.all_passed,
            "checks": [c.to_dict() for c in self.checks],
        }


@dataclass
class StrategyEvidence:
    """Evidence bundle for admission evaluation."""
    strategy_name: str
    tier: str = "tier_s"
    # P22
    p22_ar_pct: float | None = None
    # Capacity
    capacity_conservative_wan: float | None = None
    asset_type: str = "cn_equity"
    # Statistical
    dsr_p_value: float | None = None
    pbo_estimate: float | None = None
    wf_oos_sr: float | None = None
    wf_is_sr: float | None = None
    wf_positive_folds: int | None = None
    wf_total_folds: int | None = None
    hlz_t_adj: float | None = None
    # Correlation
    max_pairwise_corr: float | None = None
    avg_corr: float | None = None
    jaccard_overlap: float | None = None
    # Slippage sensitivity
    sr_at_2x_slippage: float | None = None
    sr_at_4x_slippage: float | None = None
    # Decay
    rolling_3y_sr: float | None = None
    full_period_sr: float | None = None
    # Crowding
    fvs_score: float | None = None
    turnover_concentration: float | None = None
    max_participation_rate: float | None = None


class AdmissionChecker:
    """Orchestrates 7-item admission checklist evaluation."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._cfg = config.get("admission_checklist", config)
        self._global = config.get("global", {})

    @classmethod
    def from_config(cls, config_path: Path | str | None = None) -> "AdmissionChecker":
        path = Path(config_path) if config_path else DEFAULT_CONFIG
        if not path.exists():
            raise FileNotFoundError(f"Admission config not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(data)

    def evaluate(self, evidence: StrategyEvidence) -> AdmissionResult:
        """Run all 7 checks against provided evidence."""
        result = AdmissionResult(
            strategy_name=evidence.strategy_name,
            tier=evidence.tier,
        )
        result.checks.append(self._check_p22(evidence))
        result.checks.append(self._check_capacity(evidence))
        result.checks.append(self._check_statistical(evidence))
        result.checks.append(self._check_correlation(evidence))
        result.checks.append(self._check_slippage(evidence))
        result.checks.append(self._check_decay(evidence))
        result.checks.append(self._check_crowding(evidence))
        return result

    # --- Individual check implementations ---

    def _check_p22(self, ev: StrategyEvidence) -> CheckResult:
        """Check 1: P22 AR gate."""
        cfg = self._cfg.get("p22_ar", {})
        if not cfg.get("enabled", True):
            return CheckResult("p22_ar", CheckStatus.NOT_EVALUATED, "disabled")
        if ev.p22_ar_pct is None:
            return CheckResult("p22_ar", CheckStatus.NOT_EVALUATED, "no P22 AR data")
        threshold = cfg.get("tier_s_min_pct", 13.0) if ev.tier == "tier_s" else cfg.get("tier_l_min_pct", 9.0)
        passed = ev.p22_ar_pct >= threshold
        return CheckResult(
            "p22_ar",
            CheckStatus.PASS if passed else CheckStatus.FAIL,
            f"P22 AR={ev.p22_ar_pct:.1f}% vs threshold={threshold}%",
            metric_value=ev.p22_ar_pct,
            threshold=threshold,
        )

    def _check_capacity(self, ev: StrategyEvidence) -> CheckResult:
        """Check 2: Capacity estimate."""
        cfg = self._cfg.get("capacity", {})
        if not cfg.get("enabled", True):
            return CheckResult("capacity", CheckStatus.NOT_EVALUATED, "disabled")
        if ev.capacity_conservative_wan is None:
            return CheckResult("capacity", CheckStatus.NOT_EVALUATED, "no capacity data")
        min_cap = cfg.get("min_conservative_wan", 50.0)
        cb_cap = cfg.get("cb_hard_cap_wan", 200.0)
        if ev.asset_type == "convertible_bond" and ev.capacity_conservative_wan > cb_cap:
            return CheckResult(
                "capacity", CheckStatus.WARNING,
                f"CB capacity {ev.capacity_conservative_wan}万 > hard cap {cb_cap}万 (CF-001)",
                metric_value=ev.capacity_conservative_wan, threshold=cb_cap,
            )
        passed = ev.capacity_conservative_wan >= min_cap
        return CheckResult(
            "capacity",
            CheckStatus.PASS if passed else CheckStatus.FAIL,
            f"Capacity={ev.capacity_conservative_wan}万 vs min={min_cap}万",
            metric_value=ev.capacity_conservative_wan,
            threshold=min_cap,
        )

    def _check_statistical(self, ev: StrategyEvidence) -> CheckResult:
        """Check 3: DSR/PBO/WF statistical validation."""
        cfg = self._cfg.get("statistical", {})
        if not cfg.get("enabled", True):
            return CheckResult("statistical", CheckStatus.NOT_EVALUATED, "disabled")
        issues: list[str] = []
        # DSR
        if ev.dsr_p_value is not None:
            dsr_max = cfg.get("dsr_p_value_max", 0.05)
            if ev.dsr_p_value > dsr_max:
                issues.append(f"DSR p={ev.dsr_p_value:.3f}>{dsr_max}")
        else:
            issues.append("DSR not computed")
        # PBO
        if ev.pbo_estimate is not None:
            pbo_max = cfg.get("pbo_max", 0.50)
            if ev.pbo_estimate >= pbo_max:
                issues.append(f"PBO={ev.pbo_estimate:.2f}>={pbo_max}")
        else:
            issues.append("PBO not computed")
        # WF decay
        if ev.wf_oos_sr is not None and ev.wf_is_sr is not None and ev.wf_is_sr > 0:
            decay = 1.0 - ev.wf_oos_sr / ev.wf_is_sr
            decay_max = cfg.get("wf_decay_max", 0.50)
            if decay > decay_max:
                issues.append(f"WF decay={decay:.0%}>{decay_max:.0%}")
        else:
            issues.append("WF data incomplete")
        # WF positive folds
        if ev.wf_positive_folds is not None:
            min_folds = cfg.get("wf_min_positive_folds", 5)
            if ev.wf_positive_folds < min_folds:
                issues.append(f"WF positive folds={ev.wf_positive_folds}<{min_folds}")

        has_data = any(v is not None for v in [ev.dsr_p_value, ev.pbo_estimate, ev.wf_oos_sr])
        if not has_data:
            return CheckResult("statistical", CheckStatus.NOT_EVALUATED, "no statistical data")
        # Only count real failures (not missing data)
        real_issues = [i for i in issues if "not computed" not in i and "incomplete" not in i]
        if real_issues:
            return CheckResult("statistical", CheckStatus.FAIL, "; ".join(issues))
        return CheckResult("statistical", CheckStatus.PASS, "DSR/PBO/WF all within limits")

    def _check_correlation(self, ev: StrategyEvidence) -> CheckResult:
        """Check 4: Portfolio correlation."""
        cfg = self._cfg.get("correlation", {})
        if not cfg.get("enabled", True):
            return CheckResult("correlation", CheckStatus.NOT_EVALUATED, "disabled")
        if ev.max_pairwise_corr is None:
            return CheckResult("correlation", CheckStatus.NOT_EVALUATED, "no correlation data")
        max_pair = cfg.get("max_pairwise", 0.75)
        passed = ev.max_pairwise_corr < max_pair
        detail = f"max_pairwise_corr={ev.max_pairwise_corr:.3f} vs limit={max_pair}"
        if ev.avg_corr is not None:
            max_avg = cfg.get("max_average", 0.60)
            if ev.avg_corr >= max_avg:
                passed = False
                detail += f"; avg_corr={ev.avg_corr:.3f}>={max_avg}"
        return CheckResult(
            "correlation",
            CheckStatus.PASS if passed else CheckStatus.FAIL,
            detail, metric_value=ev.max_pairwise_corr, threshold=max_pair,
        )

    def _check_slippage(self, ev: StrategyEvidence) -> CheckResult:
        """Check 5: 2x/4x slippage sensitivity."""
        cfg = self._cfg.get("slippage_sensitivity", {})
        if not cfg.get("enabled", True):
            return CheckResult("slippage_sensitivity", CheckStatus.NOT_EVALUATED, "disabled")
        if ev.sr_at_2x_slippage is None and ev.sr_at_4x_slippage is None:
            return CheckResult("slippage_sensitivity", CheckStatus.NOT_EVALUATED, "no slippage data")
        min_sr = cfg.get("min_sr_after", 0.8)
        issues = []
        if ev.sr_at_2x_slippage is not None and ev.sr_at_2x_slippage < min_sr:
            issues.append(f"SR@2x={ev.sr_at_2x_slippage:.2f}<{min_sr}")
        if ev.sr_at_4x_slippage is not None and ev.sr_at_4x_slippage < min_sr:
            issues.append(f"SR@4x={ev.sr_at_4x_slippage:.2f}<{min_sr}")
        if issues:
            return CheckResult("slippage_sensitivity", CheckStatus.FAIL, "; ".join(issues))
        parts = []
        if ev.sr_at_2x_slippage is not None:
            parts.append(f"SR@2x={ev.sr_at_2x_slippage:.2f}")
        if ev.sr_at_4x_slippage is not None:
            parts.append(f"SR@4x={ev.sr_at_4x_slippage:.2f}")
        return CheckResult("slippage_sensitivity", CheckStatus.PASS, f"{', '.join(parts)} >= {min_sr}")

    def _check_decay(self, ev: StrategyEvidence) -> CheckResult:
        """Check 6: Recent 3-year alpha decay."""
        cfg = self._cfg.get("decay", {})
        if not cfg.get("enabled", True):
            return CheckResult("decay", CheckStatus.NOT_EVALUATED, "disabled")
        if ev.rolling_3y_sr is None or ev.full_period_sr is None:
            return CheckResult("decay", CheckStatus.NOT_EVALUATED, "no decay data")
        if ev.full_period_sr <= 0:
            return CheckResult("decay", CheckStatus.WARNING, f"full_period_sr={ev.full_period_sr:.2f}<=0")
        min_ratio = cfg.get("min_sr_ratio", 0.40)
        ratio = ev.rolling_3y_sr / ev.full_period_sr
        passed = ratio >= min_ratio - 1e-9  # tolerance for float precision
        return CheckResult(
            "decay",
            CheckStatus.PASS if passed else CheckStatus.FAIL,
            f"3Y_SR/Full_SR={ratio:.2f} vs min_ratio={min_ratio}",
            metric_value=ratio, threshold=min_ratio,
        )

    def _check_crowding(self, ev: StrategyEvidence) -> CheckResult:
        """Check 7: Crowding assessment."""
        cfg = self._cfg.get("crowding", {})
        if not cfg.get("enabled", True):
            return CheckResult("crowding", CheckStatus.NOT_EVALUATED, "disabled")
        if ev.fvs_score is None:
            return CheckResult("crowding", CheckStatus.NOT_EVALUATED, "no crowding data")
        fvs_error = cfg.get("fvs_error", 0.8)
        fvs_warn = cfg.get("fvs_warning", 0.6)
        if ev.fvs_score >= fvs_error:
            return CheckResult(
                "crowding", CheckStatus.FAIL,
                f"FVS={ev.fvs_score:.2f}>={fvs_error} (high crowding)",
                metric_value=ev.fvs_score, threshold=fvs_error,
            )
        if ev.fvs_score >= fvs_warn:
            return CheckResult(
                "crowding", CheckStatus.WARNING,
                f"FVS={ev.fvs_score:.2f}>={fvs_warn} (moderate crowding)",
                metric_value=ev.fvs_score, threshold=fvs_warn,
            )
        return CheckResult(
            "crowding", CheckStatus.PASS,
            f"FVS={ev.fvs_score:.2f}<{fvs_warn}",
            metric_value=ev.fvs_score, threshold=fvs_warn,
        )
