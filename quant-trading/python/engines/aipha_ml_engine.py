"""Legacy compatibility wrapper for the canonical A-share ML bridge.

调用方式: QUANT_CONFIG='{...}' python -m engines.aipha_ml_engine

The default TypeScript tool invokes ``aipha.engines.aipha_ml_engine``.
This module remains import-compatible for old callers but must not advertise
the retired GRU/ensemble model list.
"""
from __future__ import annotations

import json
import os
import sys
import traceback

_dir = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_dir)
if _parent not in sys.path:
    sys.path.insert(0, _parent)


def run(config: dict) -> dict:
    action = config.get("action", "predict")

    if action == "predict":
        return _predict(config)
    elif action == "train":
        return _train(config)
    elif action == "evaluate":
        return _evaluate(config)
    elif action == "list_models":
        return {
            "ok": True,
            "data": {
                "models": ["lgbm", "linear"],
                "default": "lgbm",
                "deprecated_entrypoint": True,
                "canonical_entrypoint": "aipha.engines.aipha_ml_engine",
            },
        }
    else:
        return {"ok": False, "error": f"未知 action: {action}"}


def _predict(config: dict) -> dict:
    model_path = config.get("modelPath")
    if not model_path:
        return {"ok": False, "error": "需要 modelPath"}

    from aipha.models.predictor import Predictor, PredictorConfig

    predictor = Predictor(PredictorConfig(
        model_path=model_path,
        output_type=config.get("outputType", "rank"),
    ))
    predictor.load(model_path, verify_hash=config.get("verifyHash", False))

    import numpy as np
    import pandas as pd

    features = config.get("features")
    symbols = config.get("symbols", [])

    if features is None:
        return {"ok": False, "error": "需要 features 数据"}

    X = pd.DataFrame(features)
    result = predictor.predict(X, symbols)
    top_n = config.get("topN", 30)
    top_df = result.get_top_n(top_n)

    return {
        "ok": True,
        "data": {
            "rankings": top_df.to_dict("records"),
            "totalSymbols": len(symbols),
            "topN": top_n,
        },
    }


def _train(config: dict) -> dict:
    model_type = config.get("modelType", "lgbm")

    if model_type == "lgbm":
        from aipha.models.lgbm_ranker import LGBMRanker, LGBMRankerConfig
        import numpy as np
        import pandas as pd

        X_train = pd.DataFrame(config["trainFeatures"])
        y_train = np.array(config["trainLabels"])
        group_train = np.array(config["trainGroup"])

        lgbm_config = LGBMRankerConfig(**config.get("modelParams", {}))
        model = LGBMRanker(lgbm_config)

        X_valid = pd.DataFrame(config["validFeatures"]) if "validFeatures" in config else None
        y_valid = np.array(config["validLabels"]) if "validLabels" in config else None
        group_valid = np.array(config["validGroup"]) if "validGroup" in config else None

        train_result = model.fit(X_train, y_train, group_train, X_valid, y_valid, group_valid)

        save_path = config.get("savePath", "models/lgbm_ranker.joblib")
        model.save(save_path)

        return {
            "ok": True,
            "data": {
                "modelType": "lgbm",
                "trainResult": train_result,
                "savePath": save_path,
                "featureImportance": dict(list(model.feature_importance().items())[:20]),
            },
        }

    return {"ok": False, "error": f"不支持的模型类型: {model_type}"}


def _evaluate(config: dict) -> dict:
    """评估 IC/IR"""
    import numpy as np

    predictions = np.array(config.get("predictions", []))
    actuals = np.array(config.get("actuals", []))

    if len(predictions) != len(actuals) or len(predictions) == 0:
        return {"ok": False, "error": "predictions 和 actuals 长度不匹配或为空"}

    # IC (Information Coefficient) = Spearman rank correlation
    from scipy.stats import spearmanr
    ic, p_value = spearmanr(predictions, actuals)

    return {
        "ok": True,
        "data": {
            "ic": round(float(ic), 6),
            "p_value": round(float(p_value), 6),
            "n_samples": len(predictions),
        },
    }


if __name__ == "__main__":
    try:
        raw = os.environ.get("QUANT_CONFIG", "{}")
        config = json.loads(raw)
        result = run(config)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()}, ensure_ascii=False))
