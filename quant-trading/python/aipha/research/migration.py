"""Explicit migration helpers for legacy model artifacts.

Legacy folders have no provenance contract. Migration therefore requires an
operator supplied feature version and column order and refuses to guess them.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path


def _publish_model(staged: Path, destination: Path) -> None:
    """Retry transient Windows sharing locks without copying a partial model."""
    for attempt in range(5):
        if destination.exists():
            raise FileExistsError(destination)
        try:
            staged.rename(destination)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 4:
                raise
            time.sleep(0.05 * 2 ** attempt)


def migrate_legacy_model(
    source: str | Path,
    destination: str | Path,
    *,
    feature_version: str,
    feature_columns: list[str],
    model_type: str,
    data_snapshot_id: str,
    acknowledge_legacy: bool = False,
    universe: str | None = None,
    min_listing_days: int | None = None,
    feature_groups=None,
    feature_column_groups=(),
    model_id: str | None = None,
    model_config_hash: str | None = None,
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
    if (not model_type or not data_snapshot_id or universe is None or min_listing_days is None
            or feature_groups is None or not model_id or not model_config_hash):
        raise ValueError("migration requires model_type, data_snapshot_id, universe, min_listing_days, "
                         "feature_groups, model_id and model_config_hash explicitly")
    if destination_path.is_relative_to(source_path):
        raise ValueError("migration destination must be outside the source directory")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    from .artifacts import write_model_manifest, load_verified_selector

    with tempfile.TemporaryDirectory(prefix=".model-migration-", dir=destination_path.parent) as temp:
        staged = Path(temp) / "model"
        shutil.copytree(source_path, staged)
        manifest_path = write_model_manifest(
            staged, model_type=model_type, feature_version=feature_version,
            feature_cols=feature_columns, model_config_hash=model_config_hash,
            universe=universe, min_listing_days=min_listing_days, feature_groups=feature_groups,
            feature_column_groups=feature_column_groups, model_id=model_id,
            data_snapshot_id=data_snapshot_id,
        )
        load_verified_selector(staged, expected_feature_version=feature_version)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["migration"] = {
            "source": str(source_path),
            "legacy_attested": True,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        _publish_model(staged, destination_path)
    return destination_path
