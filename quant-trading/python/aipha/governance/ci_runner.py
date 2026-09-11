"""Unified governance CI runner.

Aggregates: strategy audit, pool layering, paper health,
live-readiness checks into one exit-code.

Usage:
    python -m aipha.governance.ci_runner          # run all
    python -m aipha.governance.ci_runner --strict  # fail on warnings
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)
ROOT = PROJECT_ROOT


def run_governance_ci(strict: bool = False) -> dict:
    """Run all governance checks, return summary dict."""
    from aipha.governance.strategy_governance import audit_strategy_governance
    from aipha.governance.strategy_pool_layering import classify_pool
    from aipha.governance.paper_ops_health import check_paper_health
    from aipha.governance.capacity_gate import check_capacity_gate
    from aipha.governance.statistical_gate import check_statistical_gate

    results: dict = {"checks": {}, "passed": True}

    # 1. Strategy governance audit
    cat_p = ROOT / "configs" / "strategy_catalog.json"
    sts_p = ROOT / "configs" / "strategy_status.json"
    reg_p = ROOT / "configs" / "paper" / "paper_registry.json"
    audit = audit_strategy_governance(str(cat_p), str(sts_p), str(reg_p))
    n_err = len(audit.get("errors", []))
    n_warn = len(audit.get("warnings", []))
    results["checks"]["governance_audit"] = {
        "errors": n_err, "warnings": n_warn,
    }
    if n_err > 0 or (strict and n_warn > 0):
        results["passed"] = False

    # 2. Pool layering
    pool = classify_pool(cat_p, sts_p)
    results["checks"]["pool_layering"] = pool.to_dict()

    # 3. Paper health
    state_dir = ROOT / "data" / "paper_state"
    if state_dir.exists():
        health = check_paper_health(state_dir, reg_p)
        n_herr = health.error
        results["checks"]["paper_health"] = {
            "errors": n_herr, "warnings": health.warning,
        }
        # Paper health errors are informational (missing state for
        # undeployed combos etc.) — only block in strict mode.
        if strict and n_herr > 0:
            results["passed"] = False
    else:
        results["checks"]["paper_health"] = {"skipped": True}

    # 4. Capacity gate (H2): CF-001 CB cap + Principle D capacity declaration
    cap = check_capacity_gate(cat_p)
    results["checks"]["capacity_gate"] = {
        "total_checked": cap.total_checked,
        "errors": cap.errors,
        "warnings": cap.warnings,
        "issues": cap.issues if cap.errors + cap.warnings > 0 else [],
    }
    if cap.errors > 0 or (strict and cap.warnings > 0):
        results["passed"] = False

    # 5. Statistical gate (H1): DSR/PBO/WF validation for paper+ strategies
    stat = check_statistical_gate(cat_p, strict=strict)
    results["checks"]["statistical_gate"] = {
        "total_checked": stat.total_checked,
        "total_requiring_stats": stat.total_requiring_stats,
        "passed_count": stat.passed_count,
        "errors": stat.errors,
        "warnings": stat.warnings,
        "issues": stat.issues if stat.errors + stat.warnings > 0 else [],
    }
    if stat.errors > 0:
        results["passed"] = False

    # 6. Portfolio RC gate (H10): single strategy RC < 50% for active combos
    from aipha.governance.portfolio_rc_gate import check_combo_rc
    rc_issues: list[dict] = []
    rc_errors = 0
    rc_warnings = 0
    if reg_p.exists():
        reg_data = json.loads(reg_p.read_text(encoding="utf-8"))
        for entry in reg_data.get("entries", []):
            if entry.get("paper_status") in ("archived", "suspended"):
                continue
            combo_desc = {
                "name": entry.get("entry_id", "?"),
                "components": [
                    {"name": s, "weight": w}
                    for s, w in zip(
                        entry.get("strategies", []),
                        entry.get("weights", {}).values()
                        if isinstance(entry.get("weights"), dict)
                        else entry.get("weights", []),
                    )
                ],
            }
            rc_report = check_combo_rc(combo_desc)
            rc_errors += rc_report.errors
            rc_warnings += rc_report.warnings
            rc_issues.extend(rc_report.issues)
    results["checks"]["portfolio_rc_gate"] = {
        "errors": rc_errors,
        "warnings": rc_warnings,
        "issues": rc_issues if rc_errors + rc_warnings > 0 else [],
    }
    # RC gate errors block only in strict mode (H10 is soft_gate per
    # DR-COMBO-002); in normal mode they surface as informational.
    if strict and rc_errors > 0:
        results["passed"] = False

    # 7. Strategy health monitor (P3): rolling SR, alpha decay detection
    try:
        from aipha.governance.strategy_health_monitor import (
            check_strategy_health,
        )
        health_issues: list[dict] = []
        health_dir = ROOT / "data" / "paper_state"
        if health_dir.exists() and reg_p.exists():
            reg_data2 = json.loads(reg_p.read_text(encoding="utf-8"))
            for entry in reg_data2.get("entries", []):
                if entry.get("paper_status") in ("archived", "suspended"):
                    continue
                cid = entry.get("entry_id", "?")
                sd = entry.get("state_dir", f"data/paper_state/{cid}")
                sp = ROOT / sd / "state.json"
                if not sp.exists():
                    continue
                try:
                    st = json.loads(sp.read_text(encoding="utf-8"))
                except Exception:
                    continue
                nh = st.get("nav_history", [])
                if len(nh) < 2:
                    continue
                # Build (date, daily_return) pairs
                dr_pairs = []
                for i in range(1, len(nh)):
                    prev = nh[i - 1].get("nav", 0)
                    curr = nh[i].get("nav", 0)
                    d = nh[i].get("date", "")
                    if prev > 0 and d:
                        dr_pairs.append((d, (curr - prev) / prev))
                h = check_strategy_health(cid, dr_pairs)
                v = h.verdict
                if v in ("dead", "decay", "warning"):
                    health_issues.append({
                        "combo": cid,
                        "verdict": v,
                        "message": "; ".join(
                            i.get("message", "") for i in h.issues
                        ),
                    })
        results["checks"]["health_monitor"] = {
            "issues": health_issues,
            "n_issues": len(health_issues),
        }
        # Health issues are informational only (monitoring, not gating)
    except ImportError:
        results["checks"]["health_monitor"] = {"skipped": True}

    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Governance CI")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    res = run_governance_ci(strict=args.strict)
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))

    if not res["passed"]:
        logger.error("Governance CI FAILED")
        sys.exit(1)
    logger.info("Governance CI PASSED")


if __name__ == "__main__":
    main()
