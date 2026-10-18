"""Safe model artifact loading for versioned research runs."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from aipha.features.schema import (
    FEATURE_POLICY_VERSION, feature_catalog, normalize_column_groups, normalize_feature_groups,
)
from aipha.universe.indices import normalize_universe
from aipha.universe.historical import validate_listing_threshold


def model_file_hashes(model_dir: str | Path) -> dict[str, str]:
    path = Path(model_dir)
    files = [path / "meta.pkl", *sorted(path.glob("fold_*"))]
    if not files[0].is_file() or any(not file.is_file() for file in files):
        raise ValueError("model artifacts require meta.pkl and ordinary fold files")
    return {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in files}


def write_model_manifest(
    model_dir: str | Path, *, model_type: str, feature_version: str, feature_cols: list[str],
    model_config_hash: str, universe: str, min_listing_days: int, feature_groups,
    feature_column_groups, model_id: str, data_snapshot_id: str,
) -> Path:
    """Bind the frozen training policy and serialized model bytes in schema v2."""
    groups = normalize_feature_groups(feature_groups)
    custom = normalize_column_groups(feature_column_groups)
    catalog = feature_catalog(custom)
    if not feature_cols or len(set(feature_cols)) != len(feature_cols):
        raise ValueError("model feature columns must be non-empty and unique")
    if any(column not in catalog or catalog[column] not in groups for column in feature_cols):
        raise ValueError("model feature columns are outside the declared feature group policy")
    if model_type not in {"lightgbm", "elasticnet"}:
        raise ValueError("canonical model manifest supports lightgbm or elasticnet only")
    if not all(isinstance(value, str) and value.strip() for value in
               (feature_version, model_config_hash, model_id, data_snapshot_id)):
        raise ValueError("feature_version, model_config_hash, model_id and data_snapshot_id are required")
    manifest = {
        "schema_version": 2, "model_type": model_type, "feature_version": feature_version,
        "feature_columns": list(feature_cols), "model_config_hash": model_config_hash,
        "universe": normalize_universe(universe), "min_listing_days": validate_listing_threshold(min_listing_days),
        "feature_groups": list(groups), "feature_column_groups": dict(custom),
        "feature_policy_version": FEATURE_POLICY_VERSION,
        "model_id": model_id, "data_snapshot_id": data_snapshot_id,
        "model_files": model_file_hashes(model_dir),
    }
    target = Path(model_dir) / "model_manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return target


def read_verified_model_manifest(
    model_dir: str | Path,
    *,
    expected_feature_version: str,
    expected_feature_columns: list[str] | None = None,
    expected_model_type: str | None = None,
    expected_universe: str | None = None,
    expected_min_listing_days: int | None = None,
    expected_feature_groups=None,
    expected_feature_column_groups=None,
) -> dict[str, Any]:
    """Verify policy and artifact bytes without deserializing model pickles.

    This proves the manifest contract and file integrity only. Fitted fold
    metadata and inference still require ``load_verified_selector``.
    """
    path = Path(model_dir)
    manifest_path = path / "model_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"model manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("model manifest must be a JSON object")
    if manifest.get("schema_version") != 2:
        raise ValueError("model manifest schema_version=2 required; migrate or retrain the legacy model")
    if manifest.get("model_type") not in {"lightgbm", "elasticnet"}:
        raise ValueError("canonical model manifest supports lightgbm or elasticnet only")
    if manifest.get("feature_version") != expected_feature_version:
        raise ValueError(
            f"feature version mismatch: model={manifest.get('feature_version')!r}, "
            f"requested={expected_feature_version!r}"
        )
    if expected_model_type is not None and manifest.get("model_type") != expected_model_type:
        raise ValueError(
            f"model type mismatch: model={manifest.get('model_type')!r}, requested={expected_model_type!r}"
        )
    actual = manifest.get("feature_columns")
    if (not isinstance(actual, list) or not actual or not all(isinstance(x, str) for x in actual)
            or len(set(actual)) != len(actual)):
        raise ValueError("model manifest has invalid feature_columns")
    if expected_feature_columns is not None and actual != expected_feature_columns:
        raise ValueError("model feature columns/order do not match the requested schema")
    if manifest.get("feature_policy_version") != FEATURE_POLICY_VERSION:
        raise ValueError("model feature_policy_version missing or unsupported; migrate or retrain")
    universe = normalize_universe(manifest.get("universe"))
    min_days = validate_listing_threshold(manifest.get("min_listing_days"))
    groups = normalize_feature_groups(manifest.get("feature_groups"))
    custom = normalize_column_groups(manifest.get("feature_column_groups"))
    catalog = feature_catalog(custom)
    if any(column not in catalog or catalog[column] not in groups for column in actual):
        raise ValueError("model feature columns do not match the manifest feature policy")
    if expected_universe is not None and universe != normalize_universe(expected_universe):
        raise ValueError("model training universe does not match prediction universe")
    if expected_min_listing_days is not None and min_days != validate_listing_threshold(expected_min_listing_days):
        raise ValueError("model min_listing_days does not match prediction policy")
    if expected_feature_groups is not None and groups != normalize_feature_groups(expected_feature_groups):
        raise ValueError("model feature_groups do not match prediction policy")
    if expected_feature_column_groups is not None and custom != normalize_column_groups(expected_feature_column_groups):
        raise ValueError("model feature_column_groups do not match prediction policy")
    if not all(isinstance(manifest.get(key), str) and manifest[key].strip()
               for key in ("model_id", "data_snapshot_id", "model_config_hash")):
        raise ValueError("model manifest requires model_id, data_snapshot_id and model_config_hash")
    files = manifest.get("model_files")
    if not isinstance(files, dict) or not files or files != model_file_hashes(path):
        raise ValueError("model artifact file hashes do not match the manifest")
    return manifest


def load_verified_selector(
    model_dir: str | Path,
    *,
    expected_feature_version: str,
    expected_feature_columns: list[str] | None = None,
    expected_model_type: str | None = None,
    expected_universe: str | None = None,
    expected_min_listing_days: int | None = None,
    expected_feature_groups=None,
    expected_feature_column_groups=None,
) -> Any:
    """Verify the manifest before loading and validating the fitted selector."""
    manifest = read_verified_model_manifest(
        model_dir, expected_feature_version=expected_feature_version,
        expected_feature_columns=expected_feature_columns, expected_model_type=expected_model_type,
        expected_universe=expected_universe, expected_min_listing_days=expected_min_listing_days,
        expected_feature_groups=expected_feature_groups,
        expected_feature_column_groups=expected_feature_column_groups,
    )
    from aipha.models.ml_stock_selector import MLStockSelector

    path = Path(model_dir)
    actual = manifest["feature_columns"]
    selector = MLStockSelector()
    selector.load(path)
    if selector.config.model_type != manifest["model_type"]:
        raise ValueError("saved model type does not match model manifest")
    if selector.config.feature_cols != actual:
        raise ValueError("saved model metadata feature columns do not match model manifest")
    if not selector.n_folds:
        raise ValueError("model artifact contains no fitted folds")
    for fold in selector._models:
        columns = fold.feature_name() if hasattr(fold, "feature_name") else getattr(fold, "feature_cols", None)
        if columns != actual:
            raise ValueError("fitted fold feature columns/order do not match model manifest")
    selector.research_manifest = manifest
    selector._verified_feature_columns = list(actual)
    return selector
