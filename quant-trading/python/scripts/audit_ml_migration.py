#!/usr/bin/env python
"""Audit legacy ML configs and model provenance without modifying files.

The report is intentionally conservative: a config is canonical only when it
declares a feature snapshot and every referenced model directory has a
``model_manifest.json``.  A legacy compatibility flag is reported separately
so operators can migrate historical Paper jobs one by one.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml


def _walk_strings(value: Any, *, keys: set[str], found: list[tuple[str, str]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and isinstance(child, str) and child.strip():
                found.append((key, child.strip()))
            elif key in keys and isinstance(child, list):
                found.extend(
                    (key, item.strip()) for item in child
                    if isinstance(item, str) and item.strip()
                )
            _walk_strings(child, keys=keys, found=found)
    elif isinstance(value, list):
        for child in value:
            _walk_strings(child, keys=keys, found=found)


def audit_config(path: Path, *, project_root: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {"path": str(path), "parse_error": "config root is not a mapping"}
    params: list[tuple[str, str]] = []
    _walk_strings(
        raw,
        keys={"model_dir", "model_path", "model_dirs", "oos_predictions_path", "oos_predictions_paths"},
        found=params,
    )
    snapshots: list[tuple[str, str]] = []
    _walk_strings(raw, keys={"feature_snapshot_path", "research_snapshot_path"}, found=snapshots)
    model_dirs: list[str] = []
    for key, value in params:
        if key in {"model_dir", "model_path", "model_dirs"}:
            model_dirs.append(value)
    manifest_checks = []
    for model_dir in sorted(set(model_dirs)):
        resolved = Path(model_dir)
        if not resolved.is_absolute():
            resolved = project_root / resolved
        manifest_checks.append({
            "path": model_dir,
            "resolved": str(resolved),
            "exists": resolved.is_dir(),
            "manifest": (resolved / "model_manifest.json").is_file(),
        })
    legacy = _has_true_key(raw, "legacy_ml_compat")
    strategy_names = _collect_named_values(raw)
    has_ml = bool(legacy or params or snapshots or any("ml" in name.lower() for name in strategy_names))
    canonical = bool(snapshots) and all(item["manifest"] for item in manifest_checks)
    status = (
        "not_applicable" if not has_ml else
        "canonical" if canonical else
        "legacy_compat" if legacy else "unmigrated"
    )
    return {
        "path": str(path),
        "legacy_ml_compat": legacy,
        "canonical_snapshot": snapshots,
        "legacy_references": [{"key": key, "value": value} for key, value in params],
        "model_manifests": manifest_checks,
        "status": status,
    }


def _has_true_key(value: Any, target: str) -> bool:
    if isinstance(value, dict):
        return any(
            (key == target and child is True) or _has_true_key(child, target)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_true_key(child, target) for child in value)
    return False


def _collect_named_values(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"name", "strategy_name", "strategy"} and isinstance(child, str):
                names.append(child)
            names.extend(_collect_named_values(child))
    elif isinstance(value, list):
        for child in value:
            names.extend(_collect_named_values(child))
    return names


def audit_tree(config_dir: Path, *, project_root: Path) -> dict[str, Any]:
    rows = [audit_config(path, project_root=project_root) for path in sorted(config_dir.glob("*.y*ml"))]
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "parse_error"))
        counts[status] = counts.get(status, 0) + 1
    return {
        "config_dir": str(config_dir),
        "total": len(rows),
        "counts": counts,
        "configs": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir", type=Path, default=Path("configs/paper"),
        help="directory containing YAML configs (relative to the Python project)",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="return non-zero unless every config is canonical",
    )
    args = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    config_dir = args.config_dir
    if not config_dir.is_absolute():
        config_dir = project_root / config_dir
    report = audit_tree(config_dir, project_root=project_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    applicable = report["total"] - report["counts"].get("not_applicable", 0)
    if args.strict and report["counts"].get("canonical", 0) != applicable:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
