"""Read-only statistical validation using explicit observed inputs.

Walk-forward evaluates selection among already supplied strategy returns. It
does not retrain a model or infer missing history from a strategy identifier.
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import asdict

import numpy as np


def _missing(fields: list[str]) -> dict:
    return {"status": "insufficient_data", "validated": False, "missing": fields,
            "message": "需要真实回测数据；未执行验证，也不代表通过"}


def _matrix(req: dict) -> np.ndarray | None:
    raw = req.get("returns_matrix")
    if raw is None:
        return None
    values = np.asarray(raw, dtype=float)
    if values.ndim != 2 or values.shape[0] < 2 or values.shape[1] < 1 or not np.isfinite(values).all():
        raise ValueError("returns_matrix must be a finite observations-by-strategies matrix")
    if np.any(values < -1):
        raise ValueError("returns_matrix must contain decimal simple returns, each >= -1")
    return values


def _integer(req: dict, key: str, default: int, minimum: int, maximum: int = 1_000_000) -> int:
    value = req.get(key)
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or not minimum <= value <= maximum:
        raise ValueError(f"invalid {key}")
    return int(value)


def _partitions(req: dict, matrix: np.ndarray) -> int:
    partitions = _integer(req, "n_partitions", 10, 2, 20)
    if partitions % 2 or matrix.shape[0] < partitions * 2 or matrix.shape[1] < 2:
        raise ValueError("PBO needs an even partition count, at least 2 observations per partition and 2 strategies")
    return partitions


def _quality_gates(req: dict) -> dict:
    from aipha.validation.stats import run_quality_gates

    fields = ["sharpe", "max_drawdown", "total_trades", "n_obs"]
    missing = [key for key in fields if req.get(key) is None]
    if missing:
        return _missing(missing)
    if any(isinstance(req[key], bool) or not isinstance(req[key], (int, float)) or not math.isfinite(req[key]) for key in fields):
        raise ValueError("quality statistics must be finite numbers")
    if not 0 <= req["max_drawdown"] <= 1:
        raise ValueError("max_drawdown must be a decimal fraction between 0 and 1")
    n_obs = _integer(req, "n_obs", 0, 2)
    total_trades = _integer(req, "total_trades", 0, 0)
    n_trials = _integer(req, "n_trials", 1, 1)
    matrix = _matrix(req)
    if matrix is not None:
        _partitions(req, matrix)
        if len(matrix) != n_obs:
            raise ValueError("n_obs does not match returns_matrix")
    # The library's quality gate PBO uses ten partitions. Custom partitions are
    # exposed on the explicit pbo operation, keeping gate thresholds consistent.
    if matrix is not None and (req.get("n_partitions") or 10) != 10:
        raise ValueError("quality_gates uses 10 partitions; use action=pbo for a custom partition count")
    report = run_quality_gates(sharpe=req["sharpe"], max_drawdown=req["max_drawdown"],
                               total_trades=total_trades, n_days=n_obs,
                               n_trials=n_trials, returns_matrix=matrix)
    performed = ["basic_quality"] + (["dsr"] if n_trials > 1 else []) + (["pbo_sampled_cscv"] if matrix is not None else [])
    return {"status": "complete", "validated": True, "passed": report.all_passed,
            "gates": [asdict(gate) for gate in report.gates], "performed": performed,
            "not_performed": ([] if n_trials > 1 else ["dsr: requires more than one trial"]) + ([] if matrix is not None else ["pbo: requires multiple strategy return series"]),
            "n_obs": n_obs, "n_trials": n_trials}


def _sharpes(values: np.ndarray) -> np.ndarray:
    return values.mean(axis=0) / (values.std(axis=0, ddof=1) + 1e-10) * np.sqrt(252)


def _walk_forward(req: dict) -> dict:
    from aipha.validation.walk_forward import WalkForwardSplit

    matrix = _matrix(req)
    if matrix is None and req.get("total_size") is None:
        return _missing(["returns_matrix (evaluation) or total_size (split plan only)"])
    n_splits = _integer(req, "n_splits", 5, 1, 100)
    train_size = _integer(req, "train_size", 500, 2)
    test_size = _integer(req, "test_size", 60, 2)
    purge_gap = _integer(req, "purge_gap", 5, 0)
    mode = req.get("mode") or "rolling"
    if mode not in {"rolling", "expanding"}:
        raise ValueError("mode must be rolling or expanding")
    total = len(matrix) if matrix is not None else _integer(req, "total_size", 0, 1)
    if total < train_size + purge_gap + test_size * n_splits:
        raise ValueError("not enough observations for all requested full training/test windows")
    splitter = WalkForwardSplit(n_splits=n_splits, train_size=train_size, test_size=test_size, purge_gap=purge_gap, mode=mode)
    folds = splitter.split(total)
    output = []
    oos_returns = []
    for fold in folds:
        item = {"fold_id": fold.fold_id, "train_range": [fold.train_start, fold.train_end], "test_range": [fold.test_start, fold.test_end]}
        if matrix is not None:
            train = matrix[fold.train_start:fold.train_end]
            test = matrix[fold.test_start:fold.test_end]
            selected = int(np.argmax(_sharpes(train)))
            item.update(selected_strategy_column=selected, train_sharpe=float(_sharpes(train)[selected]), test_sharpe=float(_sharpes(test)[selected]))
            oos_returns.extend(test[:, selected].tolist())
        output.append(item)
    result = {"status": "complete" if matrix is not None else "split_plan", "validated": matrix is not None,
              "validation_scope": "selection_from_fixed_return_matrix" if matrix is not None else "split_plan_only",
              "retrained": False, "n_folds": len(output), "folds": output, "purge_gap": purge_gap, "mode": mode}
    if matrix is not None:
        result.update(oos_observations=len(oos_returns), oos_returns=oos_returns,
                      oos_sharpe=float(_sharpes(np.asarray(oos_returns).reshape(-1, 1))[0]))
    return result


def _pbo(req: dict) -> dict:
    from aipha.validation.stats import probability_of_backtest_overfitting

    matrix = _matrix(req)
    if matrix is None:
        return _missing(["returns_matrix"])
    partitions = _partitions(req, matrix)
    value = probability_of_backtest_overfitting(matrix, n_partitions=partitions)
    return {"status": "complete", "validated": True, "pbo": float(value),
            "method": "sampled_cscv_fixed_seed", "n_partitions": partitions,
            "observations": len(matrix), "strategies": matrix.shape[1]}


def run(req: dict) -> dict:
    handlers = {"quality_gates": _quality_gates, "walk_forward": _walk_forward, "pbo": _pbo}
    try:
        handler = handlers.get(req.get("action", "quality_gates"))
        if handler is None:
            raise ValueError("unknown validation action")
        return {"ok": True, "data": handler(req)}
    except (ValueError, TypeError, OverflowError) as error:
        return {"ok": False, "error": str(error), "validated": False}


if __name__ == "__main__":
    request = json.loads(os.environ["QUANT_CONFIG"]) if os.environ.get("QUANT_CONFIG") else json.load(sys.stdin)
    print(json.dumps(run(request), ensure_ascii=False, allow_nan=False))
