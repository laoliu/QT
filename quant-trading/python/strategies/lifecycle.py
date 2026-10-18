"""Shared lifecycle checks for the retained Python engine entry points."""
from __future__ import annotations

import json
from pathlib import Path


CATALOG_PATH = Path(__file__).resolve().parents[2] / "strategies" / "catalog.json"


def legacy_engine_error(strategy_id: str) -> str | None:
    # Fail closed if the shared manifest is missing or malformed.
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entry = next((item for item in catalog["entries"] if item["id"] == strategy_id), None)
    if entry is None:
        return None  # User-defined Python strategies are outside the retained catalog.
    migration = entry.get("migration", {})
    if migration.get("status") == "retired":
        return f"Strategy {strategy_id} is retired. {migration['note']}"
    retired = entry.get("retiredImplementations", [])
    if retired:
        return f"Legacy Python implementation {strategy_id} is retired; the canonical builtin remains available. {retired[0]['note']}"
    if migration.get("status") == "migrated_to_builtin" or entry["engine"] == "builtin":
        canonical = migration.get("canonicalId", strategy_id)
        return f"Strategy {strategy_id} uses canonical builtin {canonical}; the legacy Python engine is disabled."
    return None
