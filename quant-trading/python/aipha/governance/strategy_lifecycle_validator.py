"""Strategy lifecycle transition validator — blocking gates.

Enforces §1.3 S1-S7 prerequisites before allowing lifecycle transitions.
Unlike statistical_gate.py (read-only audit), this module is BLOCKING:
transitions that fail validation are REJECTED.

Key gates per transition:
  S1→S2: tests ≥12, py_compile clean
  S2→S3: AR≥threshold, SR≥0.8, MDD≤threshold, WF decay≤50%,
          DSR p<0.10, PBO<0.50
  S3→S4: P22 AR≥threshold (§1.2a DR-AR-002), slippage 2x SR>0.8
  S4→S5: Paper NAV deviation ≤5%, signal consistency ≥90%
  S5→S6: capacity verification, 30d live track record
  S6→S7: monitoring infrastructure verified

Usage:
    from aipha.governance.strategy_lifecycle_validator import validate_transition
    result = validate_transition("cb_stock_quality_v4b", "S2", "S3", evidence)
    if not result.passed:
        raise LifecycleGateError(result.summary())
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# §1.2a P22 门槛 (DR-AR-002)
P22_HARD_GATE_TIER_S = 13.0  # %
P22_HARD_GATE_TIER_L = 9.0   # %

# §1.2 Performance thresholds
PERF_GATES = {
    "tier_s": {"min_ar": 20.0, "max_mdd": 30.0, "min_sr": 0.8, "min_calmar": 0.6},
    "tier_l": {"min_ar": 15.0, "max_mdd": 25.0, "min_sr": 0.8, "min_calmar": 0.6},
}

# §原则A统计门禁
DSR_P_THRESHOLD = 0.10
PBO_MAX = 0.50
WF_DECAY_MAX = 0.50
WF_MIN_FOLDS = 5

# §原则D 滑点压测
SLIPPAGE_2X_MIN_SR = 0.8
SLIPPAGE_4X_MIN_SR = 0.6

# S4 Paper 验收标准
PAPER_NAV_DEVIATION_MAX = 0.05  # 5%
PAPER_SIGNAL_CONSISTENCY_MIN = 0.90  # 90%
PAPER_MIN_DAYS = 30


@dataclass
class GateCheck:
    """Single gate check result."""
    gate_id: str
    name: str
    passed: bool
    severity: str = "error"  # error = blocking, warning = advisory
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class TransitionResult:
    """Aggregate result of a lifecycle transition validation."""
    strategy: str
    from_stage: str
    to_stage: str
    tier: str = "tier_s"
    checks: list[GateCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == "error")

    @property
    def error_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed and c.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed and c.severity == "warning")

    def summary(self) -> str:
        status = "PASS" if self.passed else "BLOCKED"
        lines = [f"[{status}] {self.strategy}: {self.from_stage}→{self.to_stage}"]
        for c in self.checks:
            mark = "✓" if c.passed else "✗"
            lines.append(f"  {mark} [{c.gate_id}] {c.name}: {c.detail}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "from_stage": self.from_stage,
            "to_stage": self.to_stage,
            "tier": self.tier,
            "passed": self.passed,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "checks": [c.to_dict() for c in self.checks],
        }


def validate_transition(
    strategy_name: str,
    from_stage: str,
    to_stage: str,
    evidence: dict[str, Any],
    *,
    tier: str = "tier_s",
) -> TransitionResult:
    """Validate whether a strategy can transition between lifecycle stages.

    Args:
        strategy_name: Strategy identifier
        from_stage: Current stage (S1-S7)
        to_stage: Target stage (S1-S7)
        evidence: Dict with validation data (metrics, DSR, PBO, WF, etc.)
        tier: "tier_s" or "tier_l" for threshold selection

    Returns:
        TransitionResult with pass/fail and detailed checks
    """
    result = TransitionResult(
        strategy=strategy_name,
        from_stage=from_stage,
        to_stage=to_stage,
        tier=tier,
    )

    # Validate stage ordering
    stages = [f"S{i}" for i in range(1, 8)]
    if from_stage not in stages or to_stage not in stages:
        result.checks.append(GateCheck(
            gate_id="LIFECYCLE-001", name="stage_valid",
            passed=False, detail=f"Invalid stage: {from_stage}→{to_stage}",
        ))
        return result

    from_idx = stages.index(from_stage)
    to_idx = stages.index(to_stage)
    if to_idx != from_idx + 1:
        result.checks.append(GateCheck(
            gate_id="LIFECYCLE-002", name="sequential_transition",
            passed=False, detail=f"Must advance one stage at a time, got {from_stage}→{to_stage}",
        ))
        return result

    # Dispatch to stage-specific validation
    validators = {
        "S2": _validate_s1_to_s2,
        "S3": _validate_s2_to_s3,
        "S4": _validate_s3_to_s4,
        "S5": _validate_s4_to_s5,
        "S6": _validate_s5_to_s6,
        "S7": _validate_s6_to_s7,
    }
    validator = validators.get(to_stage)
    if validator:
        validator(result, evidence)
    else:
        result.checks.append(GateCheck(
            gate_id="LIFECYCLE-003", name="validator_exists",
            passed=False, detail=f"No validator for transition to {to_stage}",
        ))

    return result


def _validate_s1_to_s2(result: TransitionResult, evidence: dict) -> None:
    """S1→S2: Strategy has tests, code compiles, basic structure."""
    test_count = evidence.get("test_count", 0)
    result.checks.append(GateCheck(
        gate_id="S1-TESTS", name="minimum_tests",
        passed=test_count >= 12,
        detail=f"tests={test_count}, required≥12",
    ))
    compile_clean = evidence.get("compile_clean", False)
    result.checks.append(GateCheck(
        gate_id="S1-COMPILE", name="py_compile_clean",
        passed=bool(compile_clean),
        detail="compile OK" if compile_clean else "compile errors found",
    ))
    has_config = evidence.get("has_backtest_config", False)
    result.checks.append(GateCheck(
        gate_id="S1-CONFIG", name="backtest_config_exists",
        passed=bool(has_config),
        detail="config present" if has_config else "no backtest YAML",
    ))


def _validate_s2_to_s3(result: TransitionResult, evidence: dict) -> None:
    """S2→S3: Backtest meets performance + statistical gates."""
    tier = result.tier
    gates = PERF_GATES.get(tier, PERF_GATES["tier_s"])

    # Performance gates (§1.2)
    ar = evidence.get("annualized_return")
    if ar is not None:
        result.checks.append(GateCheck(
            gate_id="S2-AR", name="annualized_return",
            passed=ar >= gates["min_ar"],
            detail=f"AR={ar:.2f}%, required≥{gates['min_ar']}%",
        ))
    else:
        result.checks.append(GateCheck(
            gate_id="S2-AR", name="annualized_return",
            passed=False, detail="AR not provided",
        ))

    sr = evidence.get("sharpe_ratio")
    if sr is not None:
        result.checks.append(GateCheck(
            gate_id="S2-SR", name="sharpe_ratio",
            passed=sr >= gates["min_sr"],
            detail=f"SR={sr:.3f}, required≥{gates['min_sr']}",
        ))
    else:
        result.checks.append(GateCheck(
            gate_id="S2-SR", name="sharpe_ratio",
            passed=False, detail="SR not provided",
        ))

    mdd = evidence.get("max_drawdown")
    if mdd is not None:
        mdd_pct = mdd * 100 if mdd <= 1.0 else mdd
        result.checks.append(GateCheck(
            gate_id="S2-MDD", name="max_drawdown",
            passed=mdd_pct <= gates["max_mdd"],
            detail=f"MDD={mdd_pct:.2f}%, required≤{gates['max_mdd']}%",
        ))

    # Statistical gates (§原则A)
    dsr_p = evidence.get("dsr_p_value")
    if dsr_p is not None:
        result.checks.append(GateCheck(
            gate_id="S2-DSR", name="dsr_p_value",
            passed=dsr_p < DSR_P_THRESHOLD,
            detail=f"p={dsr_p:.4f}, required<{DSR_P_THRESHOLD}",
        ))
    else:
        result.checks.append(GateCheck(
            gate_id="S2-DSR", name="dsr_p_value",
            passed=False, detail="DSR p-value not provided",
        ))

    pbo = evidence.get("pbo")
    if pbo is not None:
        result.checks.append(GateCheck(
            gate_id="S2-PBO", name="pbo_estimate",
            passed=pbo < PBO_MAX,
            detail=f"PBO={pbo:.3f}, required<{PBO_MAX}",
        ))
    else:
        result.checks.append(GateCheck(
            gate_id="S2-PBO", name="pbo_estimate",
            passed=False, detail="PBO not provided",
        ))

    # Walk-Forward (§原则C)
    wf_decay = evidence.get("wf_decay")
    if wf_decay is not None:
        result.checks.append(GateCheck(
            gate_id="S2-WF", name="wf_decay",
            passed=wf_decay <= WF_DECAY_MAX,
            detail=f"decay={wf_decay:.2%}, required≤{WF_DECAY_MAX:.0%}",
        ))
    else:
        result.checks.append(GateCheck(
            gate_id="S2-WF", name="wf_decay",
            passed=False, detail="WF decay not provided",
        ))

    wf_folds = evidence.get("wf_positive_folds")
    if wf_folds is not None:
        result.checks.append(GateCheck(
            gate_id="S2-WF-FOLDS", name="wf_positive_folds",
            passed=wf_folds >= WF_MIN_FOLDS,
            severity="warning",
            detail=f"folds={wf_folds}, target≥{WF_MIN_FOLDS}",
        ))


def _validate_s3_to_s4(result: TransitionResult, evidence: dict) -> None:
    """S3→S4: P22 AR gate + slippage stress + formal backtest passed."""
    tier = result.tier
    p22_gate = P22_HARD_GATE_TIER_S if tier == "tier_s" else P22_HARD_GATE_TIER_L

    # §1.3 S3.5: formal backtest run_id must exist and be s4_promotion source
    formal_run_id = evidence.get("formal_backtest_run_id")
    if formal_run_id:
        # Validate source_mode — caller is responsible for verifying run exists
        # in ResultStore before submitting evidence.  If source_mode is provided
        # in evidence (from ResultStore lookup), validate it.
        source_mode = evidence.get("formal_backtest_source_mode")
        if source_mode and source_mode != "s4_promotion":
            result.checks.append(GateCheck(
                gate_id="S3-FORMAL-BT", name="formal_backtest_source",
                passed=False,
                detail=(
                    f"run_id {formal_run_id} source_mode='{source_mode}', "
                    f"required='s4_promotion'"
                ),
            ))
        else:
            result.checks.append(GateCheck(
                gate_id="S3-FORMAL-BT", name="formal_backtest_source",
                passed=True,
                detail=f"formal backtest run_id={formal_run_id}",
            ))
    else:
        result.checks.append(GateCheck(
            gate_id="S3-FORMAL-BT", name="formal_backtest_source",
            passed=False,
            detail="formal_backtest_run_id not provided (S3.5 requires promote_to_s4)",
        ))

    # §1.2a DR-AR-002: P22 AR hard gate
    p22_ar = evidence.get("p22_annualized_return")
    if p22_ar is not None:
        result.checks.append(GateCheck(
            gate_id="S3-P22", name="p22_ar_hard_gate",
            passed=p22_ar >= p22_gate,
            detail=f"P22 AR={p22_ar:.2f}%, required≥{p22_gate}% ({tier})",
        ))
    else:
        result.checks.append(GateCheck(
            gate_id="S3-P22", name="p22_ar_hard_gate",
            passed=False, detail="P22 AR not provided (DR-AR-002 requires it)",
        ))

    # §原则D: slippage 2x SR>0.8
    slip_2x_sr = evidence.get("slippage_2x_sr")
    if slip_2x_sr is not None:
        result.checks.append(GateCheck(
            gate_id="S3-SLIP2X", name="slippage_2x_sharpe",
            passed=slip_2x_sr >= SLIPPAGE_2X_MIN_SR,
            detail=f"2x SR={slip_2x_sr:.3f}, required≥{SLIPPAGE_2X_MIN_SR}",
        ))
    else:
        result.checks.append(GateCheck(
            gate_id="S3-SLIP2X", name="slippage_2x_sharpe",
            passed=False, detail="slippage 2x SR not provided (Principle D)",
        ))

    # §原则D: slippage 4x SR>0.6
    slip_4x_sr = evidence.get("slippage_4x_sr")
    if slip_4x_sr is not None:
        result.checks.append(GateCheck(
            gate_id="S3-SLIP4X", name="slippage_4x_sharpe",
            passed=slip_4x_sr >= SLIPPAGE_4X_MIN_SR,
            severity="warning",  # 4x is advisory, not hard gate
            detail=f"4x SR={slip_4x_sr:.3f}, target≥{SLIPPAGE_4X_MIN_SR}",
        ))

    # Attribution report exists
    has_attribution = evidence.get("has_attribution_report", False)
    result.checks.append(GateCheck(
        gate_id="S3-ATTR", name="attribution_report",
        passed=bool(has_attribution),
        detail="attribution present" if has_attribution else "attribution report missing",
    ))


def _validate_s4_to_s5(result: TransitionResult, evidence: dict) -> None:
    """S4→S5: Paper trading validation period passed."""
    # Paper duration
    paper_days = evidence.get("paper_days", 0)
    result.checks.append(GateCheck(
        gate_id="S4-DURATION", name="paper_min_days",
        passed=paper_days >= PAPER_MIN_DAYS,
        detail=f"days={paper_days}, required≥{PAPER_MIN_DAYS}",
    ))

    # NAV deviation vs backtest baseline
    nav_deviation = evidence.get("nav_deviation")
    if nav_deviation is not None:
        result.checks.append(GateCheck(
            gate_id="S4-NAV", name="nav_deviation",
            passed=nav_deviation <= PAPER_NAV_DEVIATION_MAX,
            detail=f"deviation={nav_deviation:.2%}, required≤{PAPER_NAV_DEVIATION_MAX:.0%}",
        ))
    else:
        result.checks.append(GateCheck(
            gate_id="S4-NAV", name="nav_deviation",
            passed=False, detail="NAV deviation not measured",
        ))

    # Signal consistency
    signal_consistency = evidence.get("signal_consistency")
    if signal_consistency is not None:
        result.checks.append(GateCheck(
            gate_id="S4-SIGNAL", name="signal_consistency",
            passed=signal_consistency >= PAPER_SIGNAL_CONSISTENCY_MIN,
            detail=f"consistency={signal_consistency:.1%}, required≥{PAPER_SIGNAL_CONSISTENCY_MIN:.0%}",
        ))
    else:
        result.checks.append(GateCheck(
            gate_id="S4-SIGNAL", name="signal_consistency",
            passed=False, severity="warning",
            detail="signal consistency not measured",
        ))

    # No kill switch triggered
    kill_switch_triggered = evidence.get("kill_switch_triggered", False)
    result.checks.append(GateCheck(
        gate_id="S4-KILLSW", name="no_kill_switch",
        passed=not kill_switch_triggered,
        detail="no kill switch" if not kill_switch_triggered else "kill switch was triggered during paper",
    ))


def _validate_s5_to_s6(result: TransitionResult, evidence: dict) -> None:
    """S5→S6: Small-scale live verified."""
    # Capacity verification
    capacity_verified = evidence.get("capacity_verified", False)
    result.checks.append(GateCheck(
        gate_id="S5-CAP", name="capacity_verified",
        passed=bool(capacity_verified),
        detail="capacity OK" if capacity_verified else "capacity not verified",
    ))

    # Live track record duration
    live_days = evidence.get("live_days", 0)
    result.checks.append(GateCheck(
        gate_id="S5-TRACK", name="live_track_record",
        passed=live_days >= 30,
        detail=f"days={live_days}, required≥30",
    ))

    # Performance in-line with paper
    live_vs_paper_drift = evidence.get("live_vs_paper_drift")
    if live_vs_paper_drift is not None:
        result.checks.append(GateCheck(
            gate_id="S5-DRIFT", name="live_vs_paper_drift",
            passed=live_vs_paper_drift <= 0.10,
            detail=f"drift={live_vs_paper_drift:.2%}, required≤10%",
        ))


def _validate_s6_to_s7(result: TransitionResult, evidence: dict) -> None:
    """S6→S7: Full production monitoring set up."""
    # Monitoring infrastructure
    has_mdd_monitor = evidence.get("has_mdd_monitor", False)
    result.checks.append(GateCheck(
        gate_id="S6-MDD-MON", name="mdd_monitoring",
        passed=bool(has_mdd_monitor),
        detail="MDD monitor active" if has_mdd_monitor else "MDD monitoring not configured",
    ))

    has_alpha_decay_monitor = evidence.get("has_alpha_decay_monitor", False)
    result.checks.append(GateCheck(
        gate_id="S6-DECAY-MON", name="alpha_decay_monitor",
        passed=bool(has_alpha_decay_monitor),
        severity="warning",
        detail="decay monitor active" if has_alpha_decay_monitor else "alpha decay monitor not set up",
    ))

    has_reconciliation = evidence.get("has_reconciliation", False)
    result.checks.append(GateCheck(
        gate_id="S6-RECON", name="reconciliation",
        passed=bool(has_reconciliation),
        detail="reconciliation active" if has_reconciliation else "position/cash reconciliation not configured",
    ))
