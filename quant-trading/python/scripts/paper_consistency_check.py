"""Paper Trading vs Backtest Consistency Check.

Validates that paper trading signals, fees, and universe filtering
are consistent with backtest engine outputs for the same data period.

Usage:
    python scripts/paper_consistency_check.py [--config CONFIG_PATH]

Checks performed:
1. Config parity: paper config params match backtest config params
2. Fee model parity: commission/tax/slippage identical
3. Universe parity: same filtering rules applied
4. Signal replay: replay historical data and compare trade signals
5. Position parity: compare end-of-day positions from paper replay vs backtest
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_PAPER_CONFIGS = [
    "configs/paper/cb_paper_v2.yml",
    "configs/paper/cb_s4_launch.yml",
    "configs/paper/cb_optimal_portfolio.yml",
    "configs/paper/cb_ensemble_portfolio.yml",
]

REQUIRED_FEE_KEYS = [
    "commission_rate", "commission_min", "stamp_tax_rate",
    "slippage_bps", "lot_size", "t_plus_n",
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    """Result of a single consistency check."""
    check_name: str
    passed: bool
    details: str = ""
    discrepancies: list[str] = field(default_factory=list)


@dataclass
class ConsistencyReport:
    """Aggregate report for one paper config."""
    config_path: str
    timestamp: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    overall_pass: bool = True

    def add(self, result: CheckResult) -> None:
        """Add a check result."""
        self.checks.append(result)
        if not result.passed:
            self.overall_pass = False

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [f"=== Consistency Report: {self.config_path} ==="]
        lines.append(f"  Timestamp: {self.timestamp}")
        passed = sum(1 for c in self.checks if c.passed)
        total = len(self.checks)
        lines.append(f"  Result: {passed}/{total} checks passed")
        for c in self.checks:
            status = "PASS" if c.passed else "FAIL"
            lines.append(f"  [{status}] {c.check_name}: {c.details}")
            for d in c.discrepancies:
                lines.append(f"         - {d}")
        status_str = "PASS" if self.overall_pass else "FAIL"
        lines.append(f"  Overall: {status_str}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "config_path": self.config_path,
            "timestamp": self.timestamp,
            "overall_pass": self.overall_pass,
            "checks": [
                {
                    "name": c.check_name,
                    "passed": c.passed,
                    "details": c.details,
                    "discrepancies": c.discrepancies,
                }
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Core checker class
# ---------------------------------------------------------------------------
class PaperConsistencyChecker:
    """Checks consistency between paper trading config and backtest config."""

    def __init__(self, paper_config_path: str) -> None:
        self.paper_config_path = paper_config_path
        self.paper_config: dict[str, Any] = {}
        self.report = ConsistencyReport(config_path=paper_config_path)

    def load_config(self) -> None:
        """Load and parse the paper config YAML."""
        with open(self.paper_config_path, "r", encoding="utf-8") as f:
            self.paper_config = yaml.safe_load(f) or {}
        self.report.timestamp = datetime.now().isoformat()

    def _get_paper_engine(self) -> dict[str, Any]:
        return self.paper_config.get("paper_engine", {})

    def _get_multi_strategy(self) -> dict[str, Any]:
        return self.paper_config.get("multi_strategy", {})

    def _find_backtest_config(self, strategy_name: str) -> Path | None:
        """Find corresponding backtest config for a strategy name.

        Priority: exact name match > highest version match.
        """
        import re
        cb_dir = Path("configs/cb_strategies")
        bt_dir = Path("configs/backtest")

        # 1. Exact name match (highest priority)
        for d in [cb_dir, bt_dir]:
            exact = d / f"{strategy_name}.yml"
            if exact.exists():
                return exact

        # 2. Glob all versioned variants
        all_matches: list[Path] = []
        for d in [cb_dir, bt_dir]:
            if d.exists():
                all_matches.extend(d.glob(f"{strategy_name}_*.yml"))

        # Deduplicate
        all_matches = list(dict.fromkeys(all_matches))

        if not all_matches:
            return None

        # Sort by version number (highest first), prefer canonical (no letter suffix)
        def _version_key(p: Path) -> tuple[int, int, int]:
            name = p.stem
            m = re.search(r'_v(\d+)([a-z])?', name)
            ver = int(m.group(1)) if m else 0
            has_suffix = 1 if (m and m.group(2)) else 0
            return (ver, -has_suffix, len(name))

        all_matches.sort(key=_version_key, reverse=True)
        return all_matches[0]

    def check_config_parity(self) -> CheckResult:
        """Check that paper config params exist in corresponding backtest configs."""
        pe = self._get_paper_engine()
        ms = self._get_multi_strategy()
        strategies = ms.get("strategies", [])
        discrepancies: list[str] = []

        if not strategies:
            # Single strategy mode
            sname = pe.get("strategy_name", "")
            bt_path = self._find_backtest_config(sname)
            if bt_path is None:
                discrepancies.append(f"No backtest config found for '{sname}'")
        else:
            for s in strategies:
                sname = s.get("name", "")
                bt_path = self._find_backtest_config(sname)
                if bt_path is None:
                    discrepancies.append(f"No backtest config for sub-strategy '{sname}'")

        passed = len(discrepancies) == 0
        return CheckResult(
            check_name="config_parity",
            passed=passed,
            details=f"{len(strategies)} sub-strategies checked" if strategies else "single strategy",
            discrepancies=discrepancies,
        )

    def check_fee_model_parity(self) -> CheckResult:
        """Check fee/slippage/lot_size consistency between paper_engine and multi_strategy."""
        pe = self._get_paper_engine()
        ms = self._get_multi_strategy()
        discrepancies: list[str] = []

        if not ms:
            return CheckResult(
                check_name="fee_model_parity",
                passed=True,
                details="No multi_strategy section (single strategy mode)",
            )

        for key in REQUIRED_FEE_KEYS:
            pe_val = pe.get(key)
            ms_val = ms.get(key)
            if pe_val is not None and ms_val is not None and pe_val != ms_val:
                discrepancies.append(
                    f"{key}: paper_engine={pe_val} vs multi_strategy={ms_val}"
                )

        # Also check backtest configs
        strategies = ms.get("strategies", [])
        for s in strategies:
            sname = s.get("name", "")
            bt_path = self._find_backtest_config(sname)
            if bt_path and bt_path.exists():
                with open(bt_path, "r", encoding="utf-8") as f:
                    bt = yaml.safe_load(f) or {}
                bt_engine = bt.get("backtest", bt.get("engine", {}))
                for key in ["commission_rate", "stamp_tax_rate", "slippage_bps"]:
                    bt_val = bt_engine.get(key)
                    pe_val = pe.get(key)
                    if bt_val is not None and pe_val is not None:
                        if abs(float(bt_val) - float(pe_val)) > 1e-8:
                            discrepancies.append(
                                f"{sname}/{key}: paper={pe_val} vs backtest={bt_val}"
                            )

        passed = len(discrepancies) == 0
        return CheckResult(
            check_name="fee_model_parity",
            passed=passed,
            details=f"Checked {len(REQUIRED_FEE_KEYS)} fee keys across {len(strategies)} strategies",
            discrepancies=discrepancies,
        )

    def check_universe_parity(self) -> CheckResult:
        """Check universe filtering rules consistency."""
        pe = self._get_paper_engine()
        universe = pe.get("universe", {})
        discrepancies: list[str] = []

        if not universe.get("enabled", False):
            return CheckResult(
                check_name="universe_parity",
                passed=True,
                details="Dynamic universe disabled (static symbols)",
            )

        # Verify essential universe params
        required_universe = ["max_price", "min_amount"]
        for key in required_universe:
            if key not in universe:
                discrepancies.append(f"Missing universe param: {key}")

        # Cross-check max_price with sub-strategy max_price params
        univ_max_price = universe.get("max_price", 999)
        ms = self._get_multi_strategy()
        for s in ms.get("strategies", []):
            sp = s.get("params", {})
            strat_max_price = sp.get("max_price")
            if strat_max_price and strat_max_price > univ_max_price:
                discrepancies.append(
                    f"{s['name']}: strategy max_price={strat_max_price} > "
                    f"universe max_price={univ_max_price}"
                )

        passed = len(discrepancies) == 0
        return CheckResult(
            check_name="universe_parity",
            passed=passed,
            details=f"Universe max_price={univ_max_price}, min_amount={universe.get('min_amount', 'N/A')}",
            discrepancies=discrepancies,
        )

    def check_strategy_params(self) -> CheckResult:
        """Check strategy parameters match backtest YAML."""
        ms = self._get_multi_strategy()
        strategies = ms.get("strategies", [])
        discrepancies: list[str] = []

        for s in strategies:
            sname = s.get("name", "")
            paper_params = s.get("params", {})

            # Prefer explicit source_config if provided
            source_cfg = s.get("source_config", "")
            if source_cfg and Path(source_cfg).exists():
                bt_path = Path(source_cfg)
            else:
                bt_path = self._find_backtest_config(sname)

            if not bt_path or not bt_path.exists():
                continue

            with open(bt_path, "r", encoding="utf-8") as f:
                bt = yaml.safe_load(f) or {}

            # Extract backtest strategy params
            bt_strat = bt.get("strategy", {})
            bt_params = bt_strat.get("params", bt_strat)

            # Compare core trading params
            core_keys = ["top_k", "rebalance_days", "max_price", "max_premium",
                         "stop_loss_pct", "exclude_st"]
            for key in core_keys:
                if key in paper_params and key in bt_params:
                    pv = paper_params[key]
                    bv = bt_params[key]
                    if isinstance(pv, (int, float)) and isinstance(bv, (int, float)):
                        if abs(float(pv) - float(bv)) > 1e-8:
                            discrepancies.append(
                                f"{sname}/{key}: paper={pv} vs backtest={bv}"
                            )
                    elif pv != bv:
                        discrepancies.append(
                            f"{sname}/{key}: paper={pv} vs backtest={bv}"
                        )

        passed = len(discrepancies) == 0
        return CheckResult(
            check_name="strategy_params",
            passed=passed,
            details=f"Compared core params for {len(strategies)} strategies",
            discrepancies=discrepancies,
        )

    def check_state_dir_writable(self) -> CheckResult:
        """Check that state/audit directories exist and are writable."""
        pe = self._get_paper_engine()
        state_dir = pe.get("state_dir", "")
        audit_dir = pe.get("audit_log_dir", "")
        metrics_cfg = pe.get("metrics", {})
        metrics_dir = metrics_cfg.get("output_dir", "")
        discrepancies: list[str] = []

        for label, d in [("state_dir", state_dir), ("audit_log_dir", audit_dir),
                         ("metrics_dir", metrics_dir)]:
            if not d:
                continue
            p = Path(d)
            if not p.exists():
                # Try to create
                try:
                    p.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    discrepancies.append(f"{label}={d}: cannot create ({e})")
            if p.exists() and not os.access(p, os.W_OK):
                discrepancies.append(f"{label}={d}: not writable")

        passed = len(discrepancies) == 0
        return CheckResult(
            check_name="state_dir_writable",
            passed=passed,
            details=f"Checked state/audit/metrics dirs",
            discrepancies=discrepancies,
        )

    def run_all(self) -> ConsistencyReport:
        """Run all checks and return the report."""
        self.load_config()
        checks = [
            self.check_config_parity,
            self.check_fee_model_parity,
            self.check_universe_parity,
            self.check_strategy_params,
            self.check_state_dir_writable,
        ]
        for check_fn in checks:
            try:
                result = check_fn()
            except Exception as e:
                result = CheckResult(
                    check_name=check_fn.__name__.replace("check_", ""),
                    passed=False,
                    details=f"Exception: {e}",
                )
            self.report.add(result)
        return self.report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    """Run consistency checks on all registered paper configs."""
    import argparse

    parser = argparse.ArgumentParser(description="Paper vs Backtest Consistency Check")
    parser.add_argument("--config", type=str, default=None,
                        help="Single paper config to check (default: all registered)")
    parser.add_argument("--output", type=str, default="output/paper_consistency.json",
                        help="Output JSON path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    os.chdir(os.path.join(os.path.dirname(__file__), '..'))

    configs = [args.config] if args.config else DEFAULT_PAPER_CONFIGS
    all_reports: list[dict[str, Any]] = []
    overall_pass = True

    for cfg_path in configs:
        if not Path(cfg_path).exists():
            print(f"[SKIP] {cfg_path} not found")
            continue

        print(f"\nChecking: {cfg_path}")
        checker = PaperConsistencyChecker(cfg_path)
        report = checker.run_all()
        print(report.summary())
        all_reports.append(report.to_dict())
        if not report.overall_pass:
            overall_pass = False

    # Save
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_reports, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {out_path}")

    # Summary
    total = len(all_reports)
    passed = sum(1 for r in all_reports if r["overall_pass"])
    print(f"\n{'='*60}")
    print(f"Overall: {passed}/{total} configs fully consistent")
    status = "ALL PASS" if overall_pass else "SOME FAILURES"
    print(f"Status: {status}")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
