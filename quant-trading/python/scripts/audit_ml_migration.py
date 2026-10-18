#!/usr/bin/env python
"""Read-only ML migration inventory with snapshot and model contract checks.

Canonical means signed snapshot/policy and serialized file hashes verified;
it does not certify fitted model internals or successful inference. No model
pickle is deserialized. canonical_paper means a deployment profile passed a
read-only accounting preview; it does not verify its model artifacts. Legacy,
mixed and invalid configurations cannot pass --strict.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODEL_KEYS = {"model_dir", "model_path", "model_dirs"}
SNAPSHOT_KEYS = {"feature_snapshot_path", "research_snapshot_path"}
REFERENCE_KEYS = MODEL_KEYS | SNAPSHOT_KEYS | {"oos_predictions_path", "oos_predictions_paths"}


def _references(raw: dict, keys: set[str]) -> list[tuple[str, str]]:
    found = []
    for key in sorted(keys):
        value = raw.get(key)
        values = value if isinstance(value, list) else [value]
        found.extend((key, item.strip()) for item in values if isinstance(item, str) and item.strip())
    return found


def _scopes(value: Any, name: str = "$") -> list[tuple[str, dict]]:
    """Keep each component's model, snapshot and compatibility flag together."""
    found = []
    if isinstance(value, dict):
        handled = set()
        strategy, models = value.get("strategy"), value.get("models")
        # This historical single-strategy Paper shape splits its inputs across
        # two documented sections. Do not combine arbitrary siblings.
        if (isinstance(strategy, dict) and isinstance(models, dict)
                and strategy.get("name") in {"ml_factor", "ml_stock_selector", "ml_predict"}):
            params = strategy.get("parameters", {})
            merged = {**(params if isinstance(params, dict) else {}), **models,
                      **{k: v for k, v in strategy.items() if k != "parameters"}}
            found.append((name + ".strategy+models", merged))
            handled = {"strategy", "models"}
        if REFERENCE_KEYS.intersection(value) or "legacy_ml_compat" in value:
            found.append((name, value))
        for key, child in value.items():
            if key not in handled:
                found.extend(_scopes(child, name + "." + str(key)))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            found.extend(_scopes(child, f"{name}[{i}]"))
    return found


def _looks_ml(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (key in {"name", "strategy_name", "strategy"} and isinstance(child, str) and "ml" in child.lower())
            or _looks_ml(child) for key, child in value.items()
        )
    return isinstance(value, list) and any(_looks_ml(child) for child in value)


def _resolve(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def _contains_paper_profile(raw: Any) -> bool:
    if isinstance(raw, dict):
        return "research_paper" in raw or any(_contains_paper_profile(value) for value in raw.values())
    return isinstance(raw, list) and any(_contains_paper_profile(value) for value in raw)


def _audit_paper_profile(raw: dict, root: Path, trade_date: str | None) -> dict:
    result = {
        "scope": "$.research_paper", "kind": "research_paper", "status": "unmigrated",
        "legacy_ml_compat": False, "canonical_snapshot": [], "model_manifests": [], "legacy_references": [],
        "inference_exercised": False, "model_provenance_verified": False,
        "preview_succeeded": False, "committed": False,
    }
    try:
        import pandas as pd
        from aipha.research.paper_profile import resolve_paper_profile
        from aipha.research.paper_book import advance_paper_book, read_paper_book_evidence
        from aipha.research.execution_plan import read_execution_plan
        from aipha.research.snapshots import verify_attested_parquet

        if "research_paper" not in raw:
            raise ValueError("research_paper must be declared at the profile root")
        metadata = None
        if trade_date is None:
            profile = raw.get("research_paper")
            value = profile.get("snapshot_path") if isinstance(profile, dict) else None
            if not isinstance(value, str) or not value.strip():
                raise ValueError("research_paper snapshot_path must be a nonempty string")
            if "{" in value or "}" in value:
                raise ValueError("snapshot path template requires --trade-date")
            metadata = verify_attested_parquet(
                _resolve(value.strip(), root), require_artifact_hash=True,
                verify_content_hash=True, require_source_signature=True,
            )
            signal_date = date.fromisoformat(metadata["end_date"])
        else:
            signal_date = date.fromisoformat(trade_date)
        profile = resolve_paper_profile(raw, project_root=root, signal_date=signal_date)
        result.update({"canonical_snapshot": [("snapshot_path", str(profile.snapshot_path))],
                       "research_config": str(profile.research_path), "book_path": str(profile.book_path),
                       "book_exists": profile.book_path.is_file(),
                       "plan_path": str(profile.plan_path) if profile.plan_path else None})
        if metadata is None:
            metadata = verify_attested_parquet(
                profile.snapshot_path, require_artifact_hash=True,
                verify_content_hash=True, require_source_signature=True,
            )
        if metadata["end_date"] != signal_date.isoformat():
            raise ValueError("Paper snapshot must end on the audit trade date")
        panel = pd.read_parquet(profile.snapshot_path)
        panel.attrs["snapshot_metadata"] = metadata
        plan = read_execution_plan(profile.plan_path) if profile.plan_path else None
        if profile.book_path.is_file():
            read_paper_book_evidence(profile.book_path)
        # This is the runtime's in-memory preview, not execute_paper or a
        # subprocess launch. No book, plan acceptance or order is published.
        receipt = advance_paper_book(profile.book_path, panel, profile.policy, plan=plan, commit=False)
        result.update({"status": "canonical_paper", "verification_level": "paper_snapshot_plan_book_preview",
                       "preview_succeeded": True, "trade_date": signal_date.isoformat(),
                       "data_snapshot_id": metadata["data_snapshot_id"],
                       "model_config_hash": profile.policy.model_config_hash(),
                       "plan_present": plan is not None, "plan_id": plan.plan_id if plan else None,
                       "book_evidence_verified": profile.book_path.is_file(),
                       "preview_nav": receipt["nav"], "preview_replay_hash": receipt["replay_hash"]})
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _audit_scope(name: str, raw: dict, root: Path, trade_date: str | None) -> dict:
    models = _references(raw, MODEL_KEYS)
    snapshots = _references(raw, SNAPSHOT_KEYS)
    checks = []
    for _, value in models:
        path = _resolve(value, root)
        checks.append({"path": value, "resolved": str(path), "exists": path.is_dir(),
                       "manifest": (path / "model_manifest.json").is_file()})
    result = {"scope": name, "legacy_ml_compat": raw.get("legacy_ml_compat") is True,
              "canonical_snapshot": snapshots, "model_manifests": checks,
              "legacy_references": [{"key": k, "value": v} for k, v in _references(raw, REFERENCE_KEYS - SNAPSHOT_KEYS)],
              "status": "unmigrated", "inference_exercised": False}
    if result["legacy_ml_compat"]:
        result["status"] = "legacy_compat"
        return result
    try:
        for key in MODEL_KEYS & raw.keys():
            value = raw[key]
            values = value if isinstance(value, list) and key == "model_dirs" else [value]
            if not values or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError(f"invalid {key}: model paths must be nonempty strings")
        if any(not isinstance(raw[key], str) or not raw[key].strip() for key in SNAPSHOT_KEYS & raw.keys()):
            raise ValueError("snapshot path must be a nonempty string")
        if not models or not snapshots:
            raise ValueError("component requires both an explicit model and a canonical snapshot")
        if len(snapshots) != 1:
            raise ValueError("component must declare exactly one snapshot path")
        if "universe" not in raw:
            raise ValueError("component requires an explicit universe for policy verification")
        import pandas as pd
        from aipha.research.artifacts import read_verified_model_manifest
        from aipha.research.snapshots import verify_attested_parquet
        from aipha.runner.scorers import _load_canonical_ml_cross_section

        value = snapshots[0][1]
        if "{" in value or "}" in value:
            if trade_date is None:
                raise ValueError("snapshot path template requires --trade-date")
            day = date.fromisoformat(trade_date)
            value = value.format(trade_date=day.strftime("%Y%m%d"), date=day.isoformat())
        path = _resolve(value, root)
        metadata = verify_attested_parquet(
            path, require_artifact_hash=True, verify_content_hash=True, require_source_signature=True,
        )
        target_date = trade_date or metadata["end_date"]
        cross = _load_canonical_ml_cross_section(
            str(path), str(raw["universe"]), target_date,
            feature_version=str(raw.get("feature_version", "cn_equity_v1")),
            min_listing_days=raw.get("min_listing_days", 252),
            feature_groups=raw.get("feature_groups"),
            feature_column_groups=raw.get("feature_column_groups", {}),
        )
        if cross.empty:
            raise ValueError("snapshot has no eligible historical-universe rows on the audit date")
        for item in checks:
            manifest = read_verified_model_manifest(
                item["resolved"], expected_feature_version=cross.attrs["feature_version"],
                expected_universe=cross.attrs["universe"],
                expected_min_listing_days=cross.attrs["min_listing_days"],
                expected_feature_groups=cross.attrs["feature_groups"],
                expected_feature_column_groups=cross.attrs["feature_column_groups"],
            )
            if not any(name.startswith("fold_") for name in manifest["model_files"]):
                raise ValueError("model artifact contains no fitted fold files")
            columns = manifest["feature_columns"]
            missing = set(columns) - set(cross)
            if missing:
                raise ValueError(f"snapshot missing model feature columns: {sorted(missing)}")
            if any(not pd.api.types.is_numeric_dtype(cross[col]) for col in columns):
                raise ValueError("model feature columns must be numeric")
            item.update({"verified": True, "model_id": manifest["model_id"],
                         "feature_columns": columns,
                         "missing_rates": {col: float(cross[col].isna().mean()) for col in columns}})
        result.update({"status": "canonical", "verification_level": "contracts_and_hashes",
                       "trade_date": str(target_date), "data_snapshot_id": metadata["data_snapshot_id"]})
    except Exception as exc:
        # Invalid/unavailable inputs must not execute a fallback or abort the
        # remaining directory inventory.
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def audit_config(path: Path, *, project_root: Path, trade_date: str | None = None) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("config root is not a mapping")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return {"path": str(path), "status": "parse_error", "parse_error": str(exc)}
    scopes = ([_audit_paper_profile(raw, project_root, trade_date)] if _contains_paper_profile(raw)
              else [_audit_scope(name, scope, project_root, trade_date) for name, scope in _scopes(raw)])
    statuses = {scope["status"] for scope in scopes}
    status = (next(iter(statuses)) if len(statuses) == 1 else "mixed") if scopes else (
        "unmigrated" if _looks_ml(raw) else "not_applicable"
    )
    return {"path": str(path), "status": status, "scopes": scopes,
            "legacy_ml_compat": any(scope["legacy_ml_compat"] for scope in scopes),
            "canonical_snapshot": [item for scope in scopes for item in scope["canonical_snapshot"]],
            "model_manifests": [item for scope in scopes for item in scope["model_manifests"]],
            "legacy_references": [item for scope in scopes for item in scope["legacy_references"]]}


def audit_tree(config_dir: Path, *, project_root: Path, trade_date: str | None = None) -> dict[str, Any]:
    rows = [audit_config(path, project_root=project_root, trade_date=trade_date)
            for path in sorted(config_dir.glob("*.y*ml")) if path.is_file()]
    books: dict[Path, list[dict]] = {}
    for row in rows:
        if row["status"] == "canonical_paper":
            books.setdefault(Path(row["scopes"][0]["book_path"]), []).append(row)
    for path, group in books.items():
        if len(group) > 1:
            for row in group:
                row["status"] = row["scopes"][0]["status"] = "unmigrated"
                row["scopes"][0]["error"] = f"shared Paper book path across profiles: {path}"
    counts: dict[str, int] = {}
    for row in rows:
        status = row["status"]
        counts[status] = counts.get(status, 0) + 1
    return {"config_dir": str(config_dir), "total": len(rows), "counts": counts, "configs": rows,
            "directory_exists": config_dir.is_dir()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("configs/paper"))
    parser.add_argument("--trade-date", type=lambda value: date.fromisoformat(value).isoformat(),
                        help="ISO signal date; required for snapshot templates (otherwise snapshot end date)")
    parser.add_argument("--strict", action="store_true",
                        help="fail on empty/missing directories, parse errors, or any applicable noncanonical config")
    args = parser.parse_args(argv)
    report = audit_tree(_resolve(str(args.config_dir), ROOT), project_root=ROOT, trade_date=args.trade_date)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    applicable = report["total"] - report["counts"].get("not_applicable", 0)
    verified = report["counts"].get("canonical", 0) + report["counts"].get("canonical_paper", 0)
    if args.strict and (not report["directory_exists"] or not report["total"] or verified != applicable):
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
