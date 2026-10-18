"""Safe model artifact loading for versioned research runs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_verified_selector(
    model_dir: str | Path,
    *,
    expected_feature_version: str,
    expected_feature_columns: list[str] | None = None,
    expected_model_type: str | None = None,
) -> Any:
    """Load a selector only when its schema manifest is present and compatible.

    Legacy model folders without a manifest are intentionally rejected so that
    incompatible feature order or semantics cannot silently reach prediction.
    """
    from aipha.models.ml_stock_selector import MLStockSelector

    path = Path(model_dir)
    manifest_path = path / "model_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"model manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported model manifest schema_version")
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
    if not isinstance(actual, list) or not actual or not all(isinstance(x, str) for x in actual):
        raise ValueError("model manifest has invalid feature_columns")
    if expected_feature_columns is not None and actual != expected_feature_columns:
        raise ValueError("model feature columns/order do not match the requested schema")
    selector = MLStockSelector()
    selector.load(path)
    if selector.config.model_type != manifest["model_type"]:
        raise ValueError("saved model type does not match model manifest")
    if selector.config.feature_cols != actual:
        raise ValueError("saved model metadata feature columns do not match model manifest")
    return selector
