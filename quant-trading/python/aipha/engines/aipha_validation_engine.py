"""Aipha 验证桥接引擎 — 为 TS tools 提供策略验证服务"""
from __future__ import annotations

import json
import sys
from typing import Any


def main() -> None:
    raw = sys.stdin.read()
    request = json.loads(raw)
    action = request.get("action", "quality_gates")

    try:
        if action == "quality_gates":
            result = _quality_gates(request)
        elif action == "walk_forward":
            result = _walk_forward(request)
        elif action == "pbo":
            result = _pbo(request)
        else:
            result = {"error": f"Unknown action: {action}"}

        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))


def _quality_gates(req: dict) -> dict:
    from aipha.validation.stats import run_quality_gates
    sharpe = req.get("sharpe", 0.0)
    n_trials = req.get("n_trials", 1)
    report = run_quality_gates(
        observed_sharpe=sharpe,
        n_trials=n_trials,
        sr_benchmark=0.0,
        annual_returns_std=0.15,
        n_years=3.0,
    )
    return {
        "passed": report.passed,
        "dsr_passed": report.dsr_passed,
        "pbo_passed": report.pbo_passed,
        "hlz_passed": report.hlz_passed,
        "details": {
            "dsr_pvalue": report.dsr_pvalue,
            "hlz_pvalue": report.hlz_pvalue,
        },
    }


def _walk_forward(req: dict) -> dict:
    from aipha.validation.walk_forward import WalkForwardSplit
    n_splits = req.get("n_splits", 5)
    train_size = req.get("train_size", 500)
    test_size = req.get("test_size", 60)

    splitter = WalkForwardSplit(
        n_splits=n_splits,
        train_size=train_size,
        test_size=test_size,
    )

    # 返回切分规格；实际验证需要数据
    folds = splitter.split(train_size + test_size * n_splits)
    return {
        "n_folds": len(folds),
        "folds": [
            {
                "fold_id": f.fold_id,
                "train_range": [f.train_start, f.train_end],
                "test_range": [f.test_start, f.test_end],
            }
            for f in folds
        ],
        "note": "Pass strategy data via strategy_id to run full Walk-Forward validation",
    }


def _pbo(req: dict) -> dict:
    return {
        "note": "PBO requires multiple strategy variants' performance matrix. "
                "Use quality_gates for single strategy verification.",
    }


if __name__ == "__main__":
    main()
