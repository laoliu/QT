"""Aipha 组合优化引擎 — QT Python Bridge 入口。"""
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
    action = config.get("action", "optimize")

    if action == "optimize":
        return _optimize(config)
    elif action == "combine_strategies":
        return _combine(config)
    elif action == "detect_regime":
        return _detect_regime(config)
    elif action == "validate":
        return _validate(config)
    else:
        return {"ok": False, "error": f"未知 action: {action}"}


def _optimize(config: dict) -> dict:
    import pandas as pd
    from aipha.portfolio.optimizer import PortfolioOptimizer

    returns_data = config.get("returns")
    if not returns_data:
        return {"ok": False, "error": "需要 returns 数据"}

    returns = pd.DataFrame(returns_data)
    method = config.get("method", "equal")
    optimizer = PortfolioOptimizer(method=method)
    weights = optimizer.optimize(returns)

    return {
        "ok": True,
        "data": {
            "method": method,
            "weights": weights.to_dict(),
        },
    }


def _combine(config: dict) -> dict:
    import pandas as pd
    from aipha.portfolio.strategy_combo import StrategyCombiner

    navs_data = config.get("strategyNavs")
    if not navs_data:
        return {"ok": False, "error": "需要 strategyNavs 数据"}

    navs = {k: pd.Series(v) for k, v in navs_data.items()}
    method = config.get("method", "equal_weight")
    combiner = StrategyCombiner(method=method)
    result = combiner.combine(navs)

    return {
        "ok": True,
        "data": {
            "method": method,
            "metrics": result.metrics,
            "comboNav": result.combo_nav.tolist(),
        },
    }


def _detect_regime(config: dict) -> dict:
    import pandas as pd
    from aipha.portfolio.regime_detector import RegimeDetector

    prices = config.get("prices")
    if not prices:
        return {"ok": False, "error": "需要 prices 数据"}

    series = pd.Series(prices)
    detector = RegimeDetector()
    regime = detector.detect(series)

    return {
        "ok": True,
        "data": {"regime": regime.value},
    }


def _validate(config: dict) -> dict:
    from aipha.validation.stats import (
        deflated_sharpe_ratio,
        probability_of_backtest_overfitting,
        run_quality_gates,
    )
    import numpy as np

    sharpe = config.get("sharpe", 0)
    n_trials = config.get("nTrials", 1)
    n_obs = config.get("nObs", 252)
    max_drawdown = config.get("maxDrawdown", 0)
    total_trades = config.get("totalTrades", 0)

    dsr = deflated_sharpe_ratio(sharpe, n_trials, n_obs)
    qr = run_quality_gates(sharpe, max_drawdown, total_trades, n_obs)

    result_data = {
        "dsr": round(dsr, 4),
        "qualityGates": {
            "allPassed": qr.all_passed,
            "gates": [
                {"name": g.name, "passed": g.passed, "value": round(g.value, 4), "threshold": g.threshold}
                for g in qr.gates
            ],
        },
    }

    # PBO (如果提供了 returns_matrix)
    returns_matrix = config.get("returnsMatrix")
    if returns_matrix:
        rm = np.array(returns_matrix)
        pbo = probability_of_backtest_overfitting(rm)
        result_data["pbo"] = round(pbo, 4)

    return {"ok": True, "data": result_data}


if __name__ == "__main__":
    try:
        raw = os.environ.get("QUANT_CONFIG", "{}")
        config = json.loads(raw)
        result = run(config)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()}, ensure_ascii=False))
