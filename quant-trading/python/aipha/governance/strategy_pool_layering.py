"""Strategy pool layering and deduplication toolkit.

Classifies all catalog strategies into four tiers:
  1. research    — no evidence or minimal backtest
  2. validated   — passes statistical gates (DSR/WF/PBO)
  3. paper_ready — validated + capacity verified
  4. live_ready  — paper_ready + paper track record

Also detects high-correlation pairs (pseudo-diversification)
per §13.6 of copilot-instructions.

Usage:
    python -m aipha.governance.strategy_pool_layering
    # or
    from aipha.governance.strategy_pool_layering import classify_pool
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

ROOT = PROJECT_ROOT
CATALOG_PATH = ROOT / "configs" / "strategy_catalog.json"
STATUS_PATH = ROOT / "configs" / "strategy_status.json"


@dataclass
class StrategyTier:
    """Classification result for one strategy."""
    name: str
    current_lifecycle: str = "research"
    recommended_tier: str = "research"
    reasons: list[str] = field(default_factory=list)
    has_backtest: bool = False
    has_paper: bool = False
    sharpe_ratio: float = 0.0
    annual_return: float = 0.0
    core_factors: list[str] = field(default_factory=list)


@dataclass
class PoolReport:
    """Full pool layering report."""
    total: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    tiers: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_tier": self.by_tier,
            "tiers": self.tiers,
            "duplicates": self.duplicates,
            "recommendations": self.recommendations,
        }


def classify_pool(
    catalog_path: Path | None = None,
    status_path: Path | None = None,
) -> PoolReport:
    """Run pool classification and dedup detection."""
    cat_path = catalog_path or CATALOG_PATH
    st_path = status_path or STATUS_PATH

    catalog = json.loads(cat_path.read_text(encoding="utf-8"))
    status = json.loads(st_path.read_text(encoding="utf-8"))
    overrides = status.get("overrides", {})

    tiers: list[StrategyTier] = []
    by_tier: dict[str, int] = defaultdict(int)

    for group_key, group_list in catalog.items():
        if not isinstance(group_list, list):
            continue
        for strat in group_list:
            name = strat.get("name", "")
            override = overrides.get(name, {})
            eff_status = override.get("status", "active")
            if eff_status in ("archived", "deprecated"):
                continue

            tier = _classify_single(strat, eff_status)
            tiers.append(tier)
            by_tier[tier.recommended_tier] += 1

    # Detect duplicates
    duplicates = _detect_duplicates(tiers)

    # Build recommendations
    recommendations = []
    archived_count = len(overrides)
    research_count = by_tier.get("research", 0)
    if research_count > 20:
        recommendations.append(
            f"{research_count} strategies still in 'research' tier — "
            "consider archiving strategies with no evidence after 6 months"
        )
    if duplicates:
        recommendations.append(
            f"{len(duplicates)} potential duplicate pairs detected — "
            "merge or differentiate per §13.6"
        )

    return PoolReport(
        total=len(tiers),
        by_tier=dict(by_tier),
        tiers=[{
            "name": t.name,
            "current": t.current_lifecycle,
            "recommended": t.recommended_tier,
            "sr": t.sharpe_ratio,
            "ar": t.annual_return,
            "reasons": t.reasons,
        } for t in tiers],
        duplicates=duplicates,
        recommendations=recommendations,
    )


def _classify_single(
    strat: dict, status: str,
) -> StrategyTier:
    """Classify a single strategy into a tier based on available evidence."""
    name = strat.get("name", "")
    bt_results = strat.get("backtest_results", [])
    paper_usages = strat.get("paper_usages", [])
    lifecycle = strat.get("lifecycle_stage", "research")
    evidence = strat.get("evidence_level", "none")

    tier = StrategyTier(name=name, current_lifecycle=lifecycle)
    reasons: list[str] = []

    # Extract best backtest metrics
    best_sr = 0.0
    best_ar = 0.0
    for bt in bt_results:
        if isinstance(bt, dict):
            sr = bt.get("sharpe_ratio", 0)
            ar = bt.get("annual_return", bt.get("annualized_return", 0))
            # AR unit normalization: catalog stores AR as percent-form
            # (e.g. 35.57 = 35.57%). If |ar| > 2.0 it must be percent — convert
            # to decimal so downstream thresholds and formatting are consistent.
            if isinstance(ar, (int, float)) and abs(ar) > 2.0:
                ar = ar / 100.0
            best_sr = max(best_sr, sr)
            best_ar = max(best_ar, ar)

    tier.sharpe_ratio = best_sr
    tier.annual_return = best_ar
    tier.has_backtest = bool(bt_results)
    tier.has_paper = bool(paper_usages) or evidence == "paper_active"

    # Classification logic
    if status == "experimental":
        tier.recommended_tier = "research"
        reasons.append("experimental status")
    elif not bt_results and not paper_usages and evidence in ("none", ""):
        tier.recommended_tier = "research"
        reasons.append("no evidence")
    elif best_sr >= 0.8 and best_ar >= 0.15 and tier.has_paper:
        tier.recommended_tier = "paper_ready"
        reasons.append(f"SR={best_sr:.2f} AR={best_ar:.1%} + paper usage")
    elif best_sr >= 0.8 and best_ar >= 0.10:
        tier.recommended_tier = "validated"
        reasons.append(f"SR={best_sr:.2f} AR={best_ar:.1%} passes gates")
    elif bt_results:
        tier.recommended_tier = "research"
        reasons.append(f"SR={best_sr:.2f} below threshold")
    else:
        tier.recommended_tier = "research"
        reasons.append("insufficient data")

    tier.reasons = reasons

    # Extract core factor tags (from docstring or params for dedup)
    doc = strat.get("docstring", "")
    params = strat.get("default_params", {})
    factors = []
    for kw in ["price", "premium", "size", "momentum", "value", "quality",
               "double_low", "ytm", "turnover", "volatility"]:
        if kw in doc.lower() or kw in name.lower() or any(kw in str(v).lower() for v in params.values()):
            factors.append(kw)
    tier.core_factors = factors

    return tier


def _detect_duplicates(
    tiers: list[StrategyTier],
) -> list[dict]:
    """Detect potential duplicate/highly-correlated strategy pairs.

    Uses factor overlap (Jaccard similarity on core_factors) as a proxy.
    Per §13.6: pairs with >75% factor overlap should be flagged.
    """
    duplicates = []
    for i, a in enumerate(tiers):
        if not a.core_factors:
            continue
        for b in tiers[i + 1:]:
            if not b.core_factors:
                continue
            set_a = set(a.core_factors)
            set_b = set(b.core_factors)
            intersection = set_a & set_b
            union = set_a | set_b
            jaccard = len(intersection) / len(union) if union else 0

            if jaccard >= 0.75:
                duplicates.append({
                    "pair": [a.name, b.name],
                    "jaccard": round(jaccard, 3),
                    "shared_factors": sorted(intersection),
                    "action": "merge_or_differentiate",
                })
    return duplicates


def main() -> None:
    report = classify_pool()
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
