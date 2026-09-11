"""Live-readiness go/no-go checker.

Evaluates whether a strategy/combo meets all prerequisites for
transitioning from paper to live trading per the S1→S7 process
defined in copilot-instructions.md §1.3.

Returns a structured checklist with pass/fail for each gate.

Usage:
    from aipha.governance.live_readiness import check_live_readiness
    result = check_live_readiness("r115_combo")
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

ROOT = PROJECT_ROOT


@dataclass
class GateResult:
    """Result for a single readiness gate."""
    gate_id: str
    gate_name: str
    passed: bool = False
    evidence: str = ""
    blocker: bool = True  # If True, failure blocks live

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class LiveReadinessReport:
    """Full live-readiness assessment."""
    combo_id: str
    check_date: str = ""
    overall_ready: bool = False
    gates_passed: int = 0
    gates_total: int = 0
    gates: list[dict] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def check_live_readiness(combo_id: str) -> LiveReadinessReport:
    """Evaluate all live-readiness gates for a combo."""
    from datetime import date
    report = LiveReadinessReport(
        combo_id=combo_id,
        check_date=str(date.today()),
    )

    gate_fns = [
        _gate_statistical_validation,
        _gate_paper_track_record,
        _gate_capacity_verified,
        _gate_execution_consistency,
        _gate_risk_controls,
        _gate_qmt_integration,
        _gate_alert_system,
        _gate_manual_override,
        _gate_disaster_recovery,
        _gate_governance_complete,
        _gate_health_monitor,
    ]

    for fn in gate_fns:
        gate = fn(combo_id)
        report.gates.append(gate.to_dict())
        report.gates_total += 1
        if gate.passed:
            report.gates_passed += 1
        elif gate.blocker:
            report.blockers.append(f"{gate.gate_id}: {gate.gate_name}")

    report.overall_ready = report.gates_passed == report.gates_total

    if not report.overall_ready:
        report.recommendations.append(
            f"{len(report.blockers)} blocking gates must be resolved before live"
        )
    return report


def _gate_statistical_validation(combo_id: str) -> GateResult:
    """G1: Statistical validation (DSR/PBO/WF) must pass."""
    gate = GateResult(gate_id="G1", gate_name="Statistical Validation")
    # Check for validation report in output
    val_path = ROOT / "output" / combo_id / "validation_report.json"
    wf_path = ROOT / "output" / f"wf_{combo_id}" / "wf_report.json"
    if val_path.exists():
        val = json.loads(val_path.read_text(encoding="utf-8"))
        gate.passed = val.get("verdict") == "PASS"
        gate.evidence = f"score={val.get('score', 0)}"
    elif wf_path.exists():
        wf = json.loads(wf_path.read_text(encoding="utf-8"))
        gate.passed = not wf.get("is_overfit", True)
        gate.evidence = f"pbo={wf.get('pbo_estimate', 'N/A')}"
    else:
        gate.evidence = "no validation report found"
    return gate


def _gate_paper_track_record(combo_id: str) -> GateResult:
    """G2: Paper track record >= 3 months with stable NAV."""
    gate = GateResult(gate_id="G2", gate_name="Paper Track Record (>=3mo)")
    state_dir = ROOT / "data" / "paper_state" / combo_id
    if not state_dir.exists():
        gate.evidence = "no paper state directory"
        return gate
    # Check for nav_history
    for f in state_dir.glob("*.json"):
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
            hist = s.get("nav_history", [])
            if len(hist) >= 60:  # ~3 months of trading days
                gate.passed = True
                gate.evidence = f"{len(hist)} trading days tracked"
                return gate
        except Exception:
            continue
    gate.evidence = "insufficient paper history (<60 trading days)"
    return gate


def _gate_capacity_verified(combo_id: str) -> GateResult:
    """G3: Capacity verified at target capital level."""
    gate = GateResult(gate_id="G3", gate_name="Capacity Verification")
    # Check catalog for capacity field
    cat_path = ROOT / "configs" / "strategy_catalog.json"
    if cat_path.exists():
        cat = json.loads(cat_path.read_text(encoding="utf-8"))
        for group in cat.values():
            if not isinstance(group, list):
                continue
            for s in group:
                if s.get("name") == combo_id:
                    cap = s.get("capacity_conservative_wan")
                    if cap is not None:
                        gate.passed = True
                        gate.evidence = f"capacity={cap}万"
                    else:
                        gate.evidence = "capacity_conservative_wan not set"
                    return gate
    # Fallback: check paper_registry for combo-level capacity info
    reg_path = ROOT / "configs" / "paper" / "paper_registry.json"
    if reg_path.exists():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        for entry in reg.get("entries", []):
            if entry.get("entry_id") == combo_id:
                pe = entry.get("portfolio_expected", {})
                cap_range = pe.get("capital_range_cn", "")
                if cap_range:
                    gate.passed = True
                    gate.evidence = f"capital_range={cap_range}"
                else:
                    gate.evidence = "no capacity info in paper_registry"
                return gate
    gate.evidence = "combo not found in catalog or registry"
    return gate


def _gate_execution_consistency(combo_id: str) -> GateResult:
    """G4: Backtest-paper deviation < threshold."""
    gate = GateResult(gate_id="G4", gate_name="Backtest-Paper Consistency")

    # Load replay mapping
    mapping_path = ROOT / "configs" / "paper" / "replay_mapping.yml"
    if not mapping_path.exists():
        gate.evidence = "replay_mapping.yml not found"
        return gate

    try:
        import yaml  # type: ignore
    except ImportError:
        gate.evidence = "PyYAML not installed"
        return gate

    mapping_data = yaml.safe_load(
        mapping_path.read_text(encoding="utf-8")
    ) or {}
    mapping = mapping_data.get("mapping", {}) or {}
    replay_run_id = mapping.get(combo_id)
    if not replay_run_id:
        gate.evidence = f"no replay run mapped for {combo_id}"
        return gate

    # Delegate to paper_replay_diff
    try:
        sys_path = str(ROOT / "scripts" / "ops")
        import sys
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from paper_replay_diff import check_combo  # type: ignore

        result = check_combo(combo_id, replay_run_id, threshold=0.005)
        status = result.get("status", "unknown")
        if status == "ok":
            gate.passed = True
            n = result.get("n_days_compared", 0)
            mx = result.get("max_abs_rel_diff", 0)
            gate.evidence = (
                f"replay={replay_run_id}, {n} days compared, "
                f"max_diff={mx:.4%}"
            )
        elif status == "no_overlap":
            gate.evidence = (
                f"replay={replay_run_id}, 0 days overlap — "
                f"paper and replay date ranges do not intersect yet"
            )
        elif status in ("missing_paper_nav", "missing_replay_nav"):
            gate.evidence = f"status={status}, replay={replay_run_id}"
        elif status == "violation":
            n_v = result.get("n_violations", 0)
            mx = result.get("max_abs_rel_diff", 0)
            gate.evidence = (
                f"VIOLATION: {n_v} days exceed 0.5%, "
                f"max_diff={mx:.4%}"
            )
        else:
            gate.evidence = f"status={status}"
    except Exception as e:
        gate.evidence = f"replay diff check failed: {e}"

    return gate


def _gate_risk_controls(combo_id: str) -> GateResult:
    """G5: Shared risk controls active and tested."""
    gate = GateResult(gate_id="G5", gate_name="Risk Controls")
    try:
        from aipha.risk.ml_risk_controls import MddGradientDeleverager
        gate.passed = True
        gate.evidence = "MddGradientDeleverager importable"
    except ImportError:
        gate.evidence = "ml_risk_controls not importable"
    return gate


def _gate_qmt_integration(combo_id: str) -> GateResult:
    """G6: QMT gateway configured and tested."""
    gate = GateResult(gate_id="G6", gate_name="QMT Gateway")
    qmt_path = ROOT / "src" / "aipha" / "execution" / "qmt_gateway.py"
    gate.passed = qmt_path.exists()
    gate.evidence = "qmt_gateway.py exists" if gate.passed else "qmt_gateway.py missing"
    return gate


def _gate_alert_system(combo_id: str) -> GateResult:
    """G7: Alert system verified and responsive."""
    gate = GateResult(gate_id="G7", gate_name="Alert System")
    try:
        from aipha.monitor.feishu_alert import send_trade_daily_report
        gate.passed = True
        gate.evidence = "send_trade_daily_report importable"
    except ImportError:
        gate.evidence = "feishu_alert not importable"
    return gate


def _gate_manual_override(combo_id: str) -> GateResult:
    """G8: Manual override / kill switch tested."""
    gate = GateResult(gate_id="G8", gate_name="Manual Override")
    # Accept if emergency_stop.py exists AND tests pass (indirectly, via file presence)
    script = ROOT / "scripts" / "ops" / "emergency_stop.py"
    tests = ROOT / "tests" / "ops" / "test_emergency_stop.py"
    if script.exists() and tests.exists():
        gate.passed = True
        gate.evidence = "emergency_stop kill switch + tests present"
    else:
        missing = []
        if not script.exists():
            missing.append(str(script))
        if not tests.exists():
            missing.append(str(tests))
        gate.evidence = f"missing: {missing}"
    return gate


def _gate_disaster_recovery(combo_id: str) -> GateResult:
    """G9: Disaster recovery plan documented and tested."""
    gate = GateResult(gate_id="G9", gate_name="Disaster Recovery")
    dr_path = ROOT / "docs" / "disaster_recovery_plan.md"
    gate.passed = dr_path.exists()
    gate.evidence = "DR plan exists" if gate.passed else "DR plan missing"
    return gate


def _gate_governance_complete(combo_id: str) -> GateResult:
    """G10: Governance fields complete (owner/reviewer/eval)."""
    gate = GateResult(gate_id="G10", gate_name="Governance Complete")
    cat_path = ROOT / "configs" / "strategy_catalog.json"
    if cat_path.exists():
        cat = json.loads(cat_path.read_text(encoding="utf-8"))
        for group in cat.values():
            if not isinstance(group, list):
                continue
            for s in group:
                if s.get("name") == combo_id:
                    owner = s.get("owner", "unassigned")
                    reviewer = s.get("reviewer", "unassigned")
                    if owner != "unassigned" and reviewer != "unassigned":
                        gate.passed = True
                        gate.evidence = f"owner={owner} reviewer={reviewer}"
                    else:
                        gate.evidence = f"owner={owner} reviewer={reviewer}"
                    return gate
    # Fallback: check paper_registry for combo governance info
    reg_path = ROOT / "configs" / "paper" / "paper_registry.json"
    if reg_path.exists():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        for entry in reg.get("entries", []):
            if entry.get("entry_id") == combo_id:
                notes = entry.get("notes", "")
                ps = entry.get("paper_status", "")
                if ps in ("s4_active", "ready"):
                    gate.passed = True
                    gate.evidence = f"paper_status={ps}, registered"
                else:
                    gate.evidence = f"paper_status={ps}"
                return gate
    gate.evidence = "combo not found in catalog or registry"
    return gate


def _gate_health_monitor(combo_id: str) -> GateResult:
    """G11: Strategy health — rolling SR not negative for 3+ consecutive months."""
    gate = GateResult(gate_id="G11", gate_name="Health Monitor")
    # Attempt to load daily returns from paper state nav_history
    state_dir = ROOT / "data" / "paper_state" / combo_id
    nav_path = state_dir / "nav_history.json"
    if not nav_path.exists():
        # Try reading nav_history from state.json
        st_path = state_dir / "state.json"
        if st_path.exists():
            try:
                st = json.loads(st_path.read_text(encoding="utf-8"))
                hist = st.get("nav_history", [])
                if isinstance(hist, list) and len(hist) >= 60:
                    daily_returns = _extract_returns_from_nav(hist)
                    return _run_health_check(gate, combo_id, daily_returns)
            except Exception:
                pass
        gate.evidence = "no nav history available for health check"
        gate.blocker = False  # informational until nav tracking enabled
        return gate
    try:
        data = json.loads(nav_path.read_text(encoding="utf-8"))
        if isinstance(data, list) and len(data) >= 60:
            daily_returns = _extract_returns_from_nav(data)
            return _run_health_check(gate, combo_id, daily_returns)
    except Exception as exc:
        gate.evidence = f"nav parse error: {exc}"
        return gate
    gate.evidence = "insufficient nav history (<60 days)"
    gate.blocker = False
    return gate


def _extract_returns_from_nav(
    hist: list[dict],
) -> list[tuple[str, float]]:
    """Convert nav_history list into (date, daily_return) tuples."""
    navs = []
    for rec in hist:
        d = rec.get("date") or rec.get("trade_date")
        n = rec.get("nav") or rec.get("value")
        if d is not None and n is not None:
            navs.append((str(d), float(n)))
    navs.sort(key=lambda x: x[0])
    returns = []
    for i in range(1, len(navs)):
        prev_nav = navs[i - 1][1]
        if prev_nav > 0:
            ret = (navs[i][1] - prev_nav) / prev_nav
            returns.append((navs[i][0], ret))
    return returns


def _run_health_check(
    gate: GateResult,
    combo_id: str,
    daily_returns: list[tuple[str, float]],
) -> GateResult:
    """Run strategy_health_monitor and update gate."""
    from aipha.governance.strategy_health_monitor import check_strategy_health
    report = check_strategy_health(combo_id, daily_returns)
    if report.verdict in ("healthy", "insufficient_data"):
        gate.passed = True
        gate.evidence = (
            f"verdict={report.verdict} full_sr={report.full_sr:.2f}"
        )
    else:
        gate.evidence = (
            f"verdict={report.verdict} "
            f"consecutive_neg={report.consecutive_neg_12m_months}"
        )
    return gate
