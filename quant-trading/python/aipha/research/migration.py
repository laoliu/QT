"""Explicit migration helpers for legacy model artifacts.

Legacy folders have no provenance contract. Migration therefore requires an
operator supplied feature version and column order and refuses to guess them.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def migrate_legacy_model(
    source: str | Path,
    destination: str | Path,
    *,
    feature_version: str,
    feature_columns: list[str],
    model_type: str,
    data_snapshot_id: str,
    acknowledge_legacy: bool = False,
) -> Path:
    """Copy a legacy selector and attach an explicit schema manifest.

    The migration never infers PIT safety or feature order from filenames. It
    only becomes possible after the operator explicitly attests the supplied
    metadata. Existing destination directories are never overwritten.
    """
    if not acknowledge_legacy:
        raise ValueError("legacy migration requires acknowledge_legacy=True")
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if not source_path.is_dir() or not (source_path / "meta.pkl").is_file():
        raise FileNotFoundError(f"legacy selector meta.pkl missing: {source_path}")
    if destination_path.exists():
        raise FileExistsError(destination_path)
    if not feature_version or not feature_columns or not all(feature_columns):
        raise ValueError("feature_version and feature_columns are required")
    if not model_type or not data_snapshot_id:
        raise ValueError("model_type and data_snapshot_id are required")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_path, destination_path)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "model_type": model_type,
        "feature_version": feature_version,
        "feature_columns": list(feature_columns),
        "data_snapshot_id": data_snapshot_id,
        "migration": {
            "source": str(source_path),
            "legacy_attested": True,
        },
    }
    (destination_path / "model_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return destination_path
