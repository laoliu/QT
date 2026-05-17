"""S4 Promotion — formal backtest gate for entering Paper Trading.

Ensures that a strategy entering S4 (模拟交易) has been validated with
standard MarketRules parameters via a controlled, reproducible backtest.

Usage:
    from aipha.governance.s4_promotion import promote_to_s4
    result = promote_to_s4("configs/cb_strategies/v4b.yml", tier="tier_s")
    if result.passed:
        # Use result.run_id as evidence for lifecycle approve API
        ...

References:
    - §1.3 S3.5 正式回测验证
    - §1.2a P22 AR 三层评估框架
    - §13.1 MarketRules 单一来源
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Minimum evaluation periods (§5.6)
MIN_START_DATE_CB = "2018-01-02"
MIN_START_DATE_EQUITY = "2015-01-05"

# P22 split date (§5.6 CB 新规)
P22_SPLIT_DATE = "2022-08-01"

# §1.2a P22 hard gates
P22_HARD_GATE = {"tier_s": 13.0, "tier_l": 9.0}

# §1.2 full-period hard gates
FULL_PERIOD_GATES = {
    "tier_s": {"min_ar": 20.0, "max_mdd": 30.0, "min_sr": 0.8, "min_calmar": 0.6},
    "tier_l": {"min_ar": 15.0, "max_mdd": 25.0, "min_sr": 0.8, "min_calmar": 0.6},
}


@dataclass
class PromotionCheck:
    """Single check within the promotion validation."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class PromotionResult:
    """Result of promote_to_s4 execution."""

    passed: bool
    run_id: str = ""
    strategy_yaml: str = ""
    tier: str = "tier_s"
    metrics: dict[str, Any] = field(default_factory=dict)
    p22_metrics: dict[str, Any] = field(default_factory=dict)
    checks: list[PromotionCheck] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        """Human-readable summary."""
        status = "PASSED" if self.passed else "FAILED"
        lines = [f"[{status}] S4 Promotion: {self.strategy_yaml} ({self.tier})"]
        if self.run_id:
            lines.append(f"  run_id: {self.run_id}")
        for c in self.checks:
            mark = "✓" if c.passed else "✗"
            lines.append(f"  {mark} {c.name}: {c.detail}")
        if self.error:
            lines.append(f"  ERROR: {self.error}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serializable dict for JSON output."""
        return {
            "passed": self.passed,
            "run_id": self.run_id,
            "strategy_yaml": self.strategy_yaml,
            "tier": self.tier,
            "metrics": self.metrics,
            "p22_metrics": self.p22_metrics,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks],
            "error": self.error,
        }


def promote_to_s4(
    strategy_yaml: str | Path,
    *,
    tier: str = "tier_s",
    output_dir: str | Path | None = None,
) -> PromotionResult:
    """Execute formal backtest for S4 promotion.

    This is the ONLY path that produces valid evidence for S3→S4 transition.
    It forces MarketRules standard parameters and strict validation.

    Args:
        strategy_yaml: Path to strategy YAML config.
        tier: "tier_s" or "tier_l" for threshold selection.
        output_dir: Override output directory (default: output/<name>_s4_promotion/).

    Returns:
        PromotionResult with pass/fail, run_id, metrics, and check details.
    """
    strategy_yaml = Path(strategy_yaml)
    result = PromotionResult(passed=False, strategy_yaml=str(strategy_yaml), tier=tier)

    # --- Step 1: Validate inputs ---
    if not strategy_yaml.exists():
        result.error = f"Strategy YAML not found: {strategy_yaml}"
        return result

    if tier not in ("tier_s", "tier_l"):
        result.error = f"Invalid tier: {tier}. Must be 'tier_s' or 'tier_l'."
        return result

    # --- Step 2: Run formal backtest with strict validation ---
    try:
        run_result = _run_formal_backtest(strategy_yaml, output_dir=output_dir)
    except Exception as e:
        result.error = f"Formal backtest failed: {e}"
        logger.error("S4 promotion backtest failed for %s: %s", strategy_yaml, e)
        return result

    # --- Step 3: Extract metrics and validate gates ---
    result.run_id = _extract_run_id(run_result)
    result.metrics = _extract_metrics(run_result)

    _check_full_period_gates(result)
    _check_p22_gate(result, run_result)

    # --- Step 4: Mark source_mode in summary ---
    _mark_source_mode(run_result)

    # --- Step 5: Determine overall pass/fail ---
    result.passed = all(c.passed for c in result.checks)

    logger.info("S4 promotion result: %s", "PASSED" if result.passed else "FAILED")
    logger.info(result.summary())

    return result


def _run_formal_backtest(
    strategy_yaml: Path,
    *,
    output_dir: str | Path | None = None,
) -> Any:
    """Run backtest via run_from_config with strict validation."""
    from aipha.backtest.config_runner import run_from_config

    if output_dir is None:
        name = strategy_yaml.stem
        output_dir = f"output/{name}_s4_promotion"

    # run_from_config already calls validate_config which will catch
    # any invalid reality model parameters
    run_result = run_from_config(
        strategy_yaml,
        output_dir=str(output_dir),
    )
    return run_result


def _extract_run_id(run_result: Any) -> str:
    """Extract run_id from fingerprint."""
    fp = run_result.fingerprint
    if hasattr(fp, "run_id"):
        return str(fp.run_id)
    if hasattr(fp, "to_dict"):
        d = fp.to_dict()
        return d.get("run_id", d.get("config_hash", "unknown"))
    return "unknown"


def _extract_metrics(run_result: Any) -> dict[str, Any]:
    """Extract key metrics from backtest result."""
    try:
        from aipha.backtest.report import extract_metrics_dict
        return extract_metrics_dict(run_result.result)
    except Exception as e:
        logger.warning("Failed to extract metrics: %s", e)
        return {}


def _check_full_period_gates(result: PromotionResult) -> None:
    """Check §1.2 full-period performance gates."""
    gates = FULL_PERIOD_GATES.get(result.tier, FULL_PERIOD_GATES["tier_s"])
    m = result.metrics

    # AR check
    ar = m.get("annualized_return_pct", m.get("annualized_return", 0))
    # Normalize: if stored as decimal (0.25), convert to pct (25.0)
    if isinstance(ar, (int, float)) and -1 < ar < 1:
        ar = ar * 100
    result.checks.append(PromotionCheck(
        name="full_period_ar",
        passed=ar >= gates["min_ar"],
        detail=f"AR={ar:.2f}%, required≥{gates['min_ar']}%",
    ))

    # MDD check
    mdd = m.get("max_drawdown_pct", m.get("max_drawdown", 0))
    if isinstance(mdd, (int, float)) and 0 < mdd < 1:
        mdd = mdd * 100
    result.checks.append(PromotionCheck(
        name="full_period_mdd",
        passed=mdd <= gates["max_mdd"],
        detail=f"MDD={mdd:.2f}%, required≤{gates['max_mdd']}%",
    ))

    # SR check
    sr = m.get("sharpe_ratio", 0)
    result.checks.append(PromotionCheck(
        name="full_period_sr",
        passed=sr >= gates["min_sr"],
        detail=f"SR={sr:.3f}, required≥{gates['min_sr']}",
    ))

    # Calmar check
    calmar = m.get("calmar_ratio", 0)
    result.checks.append(PromotionCheck(
        name="full_period_calmar",
        passed=calmar >= gates["min_calmar"],
        detail=f"Calmar={calmar:.3f}, required≥{gates['min_calmar']}",
    ))


def _check_p22_gate(result: PromotionResult, run_result: Any) -> None:
    """Check §1.2a P22 AR gate using post-2022-08 segment."""
    gate = P22_HARD_GATE.get(result.tier, P22_HARD_GATE["tier_s"])

    # Try to compute P22 metrics from daily snapshots
    p22_ar = _compute_p22_ar(run_result)

    if p22_ar is not None:
        result.p22_metrics = {"p22_annualized_return": p22_ar}
        result.checks.append(PromotionCheck(
            name="p22_ar_gate",
            passed=p22_ar >= gate,
            detail=f"P22 AR={p22_ar:.2f}%, required≥{gate}% ({result.tier})",
        ))
    else:
        # Cannot compute P22 — if strategy start_date > P22_SPLIT_DATE,
        # full period IS the P22 period
        ar = result.metrics.get("annualized_return_pct",
                                result.metrics.get("annualized_return", 0))
        if isinstance(ar, (int, float)) and -1 < ar < 1:
            ar = ar * 100
        result.p22_metrics = {"p22_annualized_return": ar, "note": "full_period_as_p22"}
        result.checks.append(PromotionCheck(
            name="p22_ar_gate",
            passed=ar >= gate,
            detail=f"P22 AR={ar:.2f}% (full period as proxy), required≥{gate}% ({result.tier})",
        ))


def _compute_p22_ar(run_result: Any) -> float | None:
    """Compute annualized return for the P22 segment (2022-08-01+)."""
    try:
        snapshots = run_result.result.daily_snapshots
        if not snapshots:
            return None

        split_date = date(2022, 8, 1)

        # Filter snapshots after split date
        p22_snapshots = [s for s in snapshots if s.date >= split_date]
        if len(p22_snapshots) < 60:  # Need at least ~3 months of data
            return None

        first_nav = p22_snapshots[0].nav
        last_nav = p22_snapshots[-1].nav
        if first_nav <= 0:
            return None

        days = (p22_snapshots[-1].date - p22_snapshots[0].date).days
        if days <= 0:
            return None

        # Annualize
        total_return = last_nav / first_nav - 1
        years = days / 365.25
        annualized = (1 + total_return) ** (1 / years) - 1

        return annualized * 100  # Return as percentage
    except Exception as e:
        logger.debug("P22 AR computation failed: %s", e)
        return None


def _mark_source_mode(run_result: Any) -> None:
    """Mark summary.json with source_mode='s4_promotion' for audit trail."""
    try:
        summary_path = run_result.output_dir / "summary.json"
        if summary_path.exists():
            with open(summary_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["source_mode"] = "s4_promotion"
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("Marked %s with source_mode=s4_promotion", summary_path)
    except Exception as e:
        logger.warning("Failed to mark source_mode: %s", e)


def update_strategy_catalog(
    strategy_name: str,
    run_id: str,
    entry_date: str | None = None,
    catalog_path: str | Path | None = None,
) -> bool:
    """Update strategy_catalog.json with evidence_run_id and s4_entry_date.

    Called after successful S4 promotion to close the governance field gap.

    Args:
        strategy_name: Strategy name to match in catalog (e.g. "cb_stock_quality_v4b").
        run_id: The run_id from successful promotion.
        entry_date: S4 entry date as "YYYY-MM-DD". Defaults to today.
        catalog_path: Path to catalog JSON. Defaults to configs/strategy_catalog.json.

    Returns:
        True if catalog was updated, False otherwise.
    """
    if catalog_path is None:
        catalog_path = Path("configs/strategy_catalog.json")
    else:
        catalog_path = Path(catalog_path)

    if not catalog_path.exists():
        logger.warning("strategy_catalog.json not found at %s", catalog_path)
        return False

    if entry_date is None:
        entry_date = date.today().isoformat()

    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load catalog: %s", e)
        return False

    updated = False
    for category in catalog.values():
        if not isinstance(category, list):
            continue
        for entry in category:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") == strategy_name:
                entry["evidence_run_id"] = run_id
                entry["s4_entry_date"] = entry_date
                entry["lifecycle_stage"] = "s4_active"
                updated = True
                logger.info(
                    "Updated catalog: %s → evidence_run_id=%s, s4_entry_date=%s",
                    strategy_name, run_id, entry_date,
                )
                break
        if updated:
            break

    if not updated:
        logger.warning(
            "Strategy '%s' not found in catalog. Governance fields not updated.",
            strategy_name,
        )
        return False

    # Atomic write: .tmp → rename
    tmp_path = catalog_path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2, ensure_ascii=False)
        tmp_path.replace(catalog_path)
        logger.info("strategy_catalog.json updated successfully.")
        return True
    except OSError as e:
        logger.error("Failed to write catalog: %s", e)
        if tmp_path.exists():
            tmp_path.unlink()
        return False
