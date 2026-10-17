"""Canonical A-share research bridge.

Actions: ``train`` (offline Parquet panel), ``predict``, ``evaluate``,
``export`` (target positions), and ``list_models``. Historical training never
falls back to a network provider: it consumes a frozen local feature panel.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent
_PYTHON_ROOT = _DIR.parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

DEFAULT_CONFIG = _PYTHON_ROOT / "aipha" / "configs" / "research" / "a_stock_ml.yaml"


def _load_config(path: str | None) -> Any:
    from aipha.research.config import load_research_config

    return load_research_config(path or DEFAULT_CONFIG)


def _read_parquet(path: str | None):
    if not path:
        raise ValueError("dataPath is required")
    target = Path(path).expanduser().resolve()
    if not target.is_file() or target.suffix.lower() not in {".parquet", ".pq"}:
        raise ValueError(f"dataPath must point to an existing Parquet file: {target}")
    import pandas as pd

    frame = pd.read_parquet(target)
    sidecar = target.with_suffix(".metadata.json")
    if not sidecar.is_file():
        raise ValueError(
            f"snapshot metadata sidecar required: {sidecar} "
            "(provider, data_snapshot_id, feature_version)"
        )
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("snapshot metadata sidecar must contain a JSON object")
    frame.attrs["snapshot_metadata"] = metadata
    return frame


def run(config: dict) -> dict:
    action = config.get("action", "list_models")
    try:
        if action == "list_models":
            return {"ok": True, "data": {"models": ["lgbm", "linear"], "default": "lgbm",
                                           "linear_implementation": "elasticnet"}}
        if action == "train":
            from aipha.research import ResearchPipeline

            cfg = _load_config(config.get("configPath"))
            model_type = str(config.get("modelType", "lgbm")).lower()
            if model_type not in {"lgbm", "linear"}:
                raise ValueError("modelType must be lgbm or linear")
            from dataclasses import replace
            cfg = replace(cfg, model_type="elasticnet" if model_type == "linear" else "lightgbm",
                          objective="regression" if model_type == "linear" else cfg.objective)
            overrides = config.get("modelParams") or {}
            if not isinstance(overrides, dict):
                raise ValueError("modelParams must be an object")
            allowed = {"n_estimators", "train_years", "test_months", "purge_days", "top_k"}
            unknown = set(overrides) - allowed
            if unknown:
                raise ValueError(f"unsupported modelParams: {', '.join(sorted(unknown))}")
            if overrides:
                cfg = replace(cfg, **{key: int(value) for key, value in overrides.items()})
            pipeline = ResearchPipeline(cfg)
            result = pipeline.run(
                _read_parquet(config.get("dataPath")),
                config.get("outputDir", "output/research/runs"),
                data_snapshot_id=config.get("dataSnapshotId"),
            )
            return {"ok": True, "data": result}
        if action == "predict":
            from aipha.research import load_verified_selector

            cfg = _load_config(config.get("configPath"))
            feature_path = config.get("featurePath") or config.get("dataPath")
            frame = _read_parquet(feature_path)
            if "datetime" not in frame and "trade_date" in frame:
                frame["datetime"] = frame["trade_date"]
            model_path = config.get("modelPath")
            if not model_path:
                raise ValueError("modelPath is required")
            selector = load_verified_selector(
                model_path,
                expected_feature_version=cfg.feature_version,
                expected_feature_columns=config.get("featureColumns"),
                expected_model_type="elasticnet" if str(config.get("modelType", "lgbm")).lower() == "linear" else "lightgbm",
            )
            frame = frame.copy()
            frame["score"] = selector.predict(frame).to_numpy()
            frame["trade_date"] = frame["datetime"]
            frame["rank"] = frame.groupby("trade_date")["score"].rank(
                ascending=False, method="first",
            ).astype(int)
            top_n = int(config.get("topN", cfg.top_k))
            result = frame.sort_values(["trade_date", "rank"]).groupby(
                "trade_date", sort=True,
            ).head(top_n)
            columns = ["trade_date", "symbol", "score", "rank"]
            result = result[columns]
            return {"ok": True, "data": {"rankings": result.to_dict("records"), "totalSymbols": int(len(frame)), "topN": top_n}}
        if action == "evaluate":
            from aipha.research.pipeline import _evaluate_predictions

            path = config.get("predictionsPath") or config.get("dataPath")
            frame = _read_parquet(path)
            from aipha.research import build_target_positions
            cfg = _load_config(config.get("configPath"))
            normalized = frame.rename(columns={"datetime": "trade_date", "pred_score": "score"})
            normalized["trade_date"] = __import__("pandas").to_datetime(normalized["trade_date"]).dt.date
            targets = build_target_positions(
                normalized,
                top_k=cfg.top_k,
                max_position_weight=cfg.max_position_weight,
            )
            metrics = _evaluate_predictions(normalized, targets, cfg)
            return {"ok": True, "data": {"metrics": metrics, "rows": int(len(normalized))}}
        if action == "export":
            from aipha.research import build_target_positions
            from dataclasses import asdict

            cfg = _load_config(config.get("configPath"))
            source = _read_parquet(config.get("predictionsPath") or config.get("dataPath"))
            source = source.rename(columns={"datetime": "trade_date", "pred_score": "score"})
            targets = build_target_positions(
                source,
                top_k=int(config.get("topN", cfg.top_k)),
                max_position_weight=cfg.max_position_weight,
                run_id=str(config.get("runId", "")),
                model_id=str(config.get("modelId", "")),
            )
            output = Path(config.get("outputPath", "output/research/target_positions.json"))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps([
                    {**asdict(item), "rebalance_date": item.rebalance_date.isoformat(),
                     "risk_flags": list(item.risk_flags)}
                    for item in targets
                ], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return {"ok": True, "data": {"outputPath": str(output), "count": len(targets)}}
        return {"ok": False, "error": f"未知 action: {action}"}
    except Exception as error:
        return {"ok": False, "error": str(error)}


if __name__ == "__main__":
    try:
        request = json.loads(os.environ.get("QUANT_CONFIG", "{}"))
        print(json.dumps(run(request), ensure_ascii=False, default=str))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error), "trace": traceback.format_exc()}, ensure_ascii=False))
