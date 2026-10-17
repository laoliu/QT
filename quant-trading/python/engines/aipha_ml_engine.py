"""Deprecated compatibility shim for the canonical A-share ML bridge.

The old ``engines.aipha_ml_engine`` module accepted in-memory training
arrays and loaded unversioned predictor artifacts. Keeping a second
implementation made it possible for callers to bypass PIT snapshots and
feature manifests. New callers must use ``aipha.engines.aipha_ml_engine``
through the ``ml_ranking`` tool.
"""
from __future__ import annotations

import json
import os
import traceback
from typing import Any

CANONICAL_ENTRYPOINT = "aipha.engines.aipha_ml_engine"
LEGACY_ENTRYPOINT = True


def run(config: dict[str, Any]) -> dict[str, Any]:
    """Forward only canonical actions; reject the retired in-memory API."""
    action = str(config.get("action", "list_models"))
    canonical_actions = {
        "train", "train_tushare", "predict", "evaluate", "export",
        "execute_paper", "list_models",
    }
    if action not in canonical_actions:
        return {
            "ok": False,
            "error": (
                "deprecated engines.aipha_ml_engine payload; use the canonical "
                "ml_ranking action with a versioned Parquet snapshot"
            ),
        }
    from aipha.engines.aipha_ml_engine import run as canonical_run

    return canonical_run(config)


if __name__ == "__main__":
    try:
        request = json.loads(os.environ.get("QUANT_CONFIG", "{}"))
        print(json.dumps(run(request), ensure_ascii=False, default=str))
    except Exception as error:
        print(json.dumps({
            "ok": False,
            "error": str(error),
            "trace": traceback.format_exc(),
        }, ensure_ascii=False))
