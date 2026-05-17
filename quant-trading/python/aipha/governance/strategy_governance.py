"""Strategy governance audit utilities."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

VALID_QUALITY_STATUSES = {"active", "archived", "deprecated", "experimental"}
ACTIVE_PAPER_STATUSES = {"ready", "running", "active"}
REQUIRED_CATALOG_FIELDS = (
    "name",
    "class",
    "module",
    "default_params",
    "category",
    "backtest_results",
    "paper_usages",
)
RECOMMENDED_GOVERNANCE_FIELDS = (
    "owner",
    "reviewer",
    "last_review_date",
    "next_review_date",
    "eval_version",
    "capacity_conservative_wan",
    "evidence_run_id",
    "s4_entry_date",
)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _effective_quality_status(name: str, item: dict[str, Any], overrides: dict[str, Any]) -> str:
    if name in overrides:
        return str(overrides[name].get("status", "active"))
    category = str(item.get("category", ""))
    lifecycle = str(item.get("lifecycle_stage", ""))
    # Strategies with explicit lifecycle_stage of paper_candidate or above are active
    if lifecycle in ("paper_candidate", "live_candidate"):
        return "active"
    if category in {"demo", "other"} and lifecycle in ("", "research"):
        return "experimental"
    return "active"


def _issue(
    issues: list[dict[str, Any]],
    severity: str,
    code: str,
    message: str,
    *,
    strategy: str | None = None,
    source: str | None = None,
) -> None:
    issues.append(
        {
            "severity": severity,
            "code": code,
            "strategy": strategy,
            "source": source,
            "message": message,
        }
    )


def audit_strategy_governance(
    catalog_path: str | Path,
    status_path: str | Path,
    paper_registry_path: str | Path | None = None,
) -> dict[str, Any]:
    """Audit catalog/status/paper registry consistency and governance gaps."""
    catalog = _load_json(Path(catalog_path))
    status_doc = _load_json(Path(status_path))
    paper_doc = _load_json(Path(paper_registry_path)) if paper_registry_path else {}
    overrides = status_doc.get("overrides", {})
    entries = paper_doc.get("entries", [])

    issues: list[dict[str, Any]] = []
    strategies: dict[str, dict[str, Any]] = {}
    active_strategy_count = 0

    for asset_type, items in catalog.items():
        if not isinstance(items, list):
            continue
        for item in items:
            name = str(item.get("name", ""))
            if not name:
                _issue(issues, "error", "missing_name", "Catalog entry missing strategy name", source=asset_type)
                continue
            strategies[name] = item
            for field in REQUIRED_CATALOG_FIELDS:
                if field not in item:
                    _issue(issues, "error", "missing_catalog_field", f"Missing required field '{field}'", strategy=name, source="catalog")
            quality_status = _effective_quality_status(name, item, overrides)
            if quality_status == "active":
                active_strategy_count += 1
                for field in RECOMMENDED_GOVERNANCE_FIELDS:
                    if field not in item:
                        _issue(issues, "warning", "missing_governance_field", f"Missing governance field '{field}'", strategy=name, source="catalog")
                    elif item[field] is None or item[field] == "":
                        _issue(issues, "info", "empty_governance_field", f"Governance field '{field}' present but empty", strategy=name, source="catalog")
                    elif item[field] == "unassigned":
                        _issue(issues, "info", "unassigned_governance_field", f"Governance field '{field}' is 'unassigned'", strategy=name, source="catalog")
                if not item.get("backtest_results") and not item.get("paper_usages"):
                    _issue(issues, "warning", "active_without_evidence", "Active strategy has no backtest_results or paper_usages", strategy=name, source="catalog")

    for name, override in overrides.items():
        if name not in strategies:
            _issue(issues, "error", "unknown_status_override", "Status override references unknown strategy", strategy=name, source="status")
            continue
        status = str(override.get("status", ""))
        if status not in VALID_QUALITY_STATUSES:
            _issue(issues, "error", "invalid_status", f"Invalid status '{status}'", strategy=name, source="status")
        if status in {"archived", "deprecated"}:
            if not override.get("reason"):
                _issue(issues, "error", "missing_status_reason", "Archived/deprecated override missing reason", strategy=name, source="status")
            if not override.get("archived_date"):
                _issue(issues, "error", "missing_archived_date", "Archived/deprecated override missing archived_date", strategy=name, source="status")

    active_paper_entries = 0
    root_dir = Path(catalog_path).resolve().parents[0].parent  # configs/../ = project root
    for entry in entries:
        entry_id = str(entry.get("entry_id", ""))
        paper_status = str(entry.get("paper_status", ""))
        linked = entry.get("strategies", []) or []
        if paper_status in ACTIVE_PAPER_STATUSES:
            active_paper_entries += 1
            # Validate config_path exists
            cfg = entry.get("config_path", "")
            if cfg:
                cfg_full = root_dir / cfg
                if not cfg_full.exists():
                    _issue(issues, "warning", "paper_config_missing", f"Config file not found: {cfg}", strategy=entry_id, source="paper_registry")
        for strategy_name in linked:
            if strategy_name not in strategies:
                _issue(issues, "error", "unknown_paper_strategy", f"Paper registry references unknown strategy '{strategy_name}'", strategy=strategy_name, source=entry_id)
                continue
            quality_status = _effective_quality_status(strategy_name, strategies[strategy_name], overrides)
            if paper_status in ACTIVE_PAPER_STATUSES and quality_status in {"archived", "deprecated", "experimental"}:
                _issue(issues, "error", "paper_uses_inactive_strategy", f"Paper entry '{entry_id}' uses {quality_status} strategy", strategy=strategy_name, source=entry_id)

    counts = Counter(issue["severity"] for issue in issues)
    code_counts = Counter(issue["code"] for issue in issues)
    return {
        "summary": {
            "strategy_count": len(strategies),
            "active_strategy_count": active_strategy_count,
            "status_override_count": len(overrides),
            "paper_entry_count": len(entries),
            "active_paper_entry_count": active_paper_entries,
            "error_count": counts.get("error", 0),
            "warning_count": counts.get("warning", 0),
            "info_count": counts.get("info", 0),
        },
        "issue_counts": dict(code_counts),
        "issues": issues,
    }