"""
API bridge for Portfolio Combiner + Statistical Validator.

Reads JSON from QUANT_CONFIG env var or stdin, dispatches to the appropriate
engine function, and writes JSON to stdout.

Actions:
  combine      — Build combined portfolio from sub-strategy equity curves
  validate     — Run DSR / HLZ / PBO statistical validation
  perturbation — Weight perturbation sensitivity test
  slippage     — Slippage sensitivity test
"""

from __future__ import annotations

import json
import os
import sys


def get_input() -> dict:
    raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
    return json.loads(raw)


def action_combine(config: dict) -> dict:
    from aipha.engines.portfolio_combiner import PortfolioCombiner, SubStrategy

    strategies_raw = config.get("strategies", [])
    strategies = []
    for s in strategies_raw:
        strategies.append(SubStrategy(
            name=s.get("name", "unknown"),
            weight=float(s.get("weight", 0)),
            asset_class=s.get("asset_class", "equity"),
            slippage_bps=float(s.get("slippage_bps", 10)),
        ))

    method = config.get("method", "fixed")
    rebalance_freq = config.get("rebalance_freq", "monthly")
    portfolio_vol_target = config.get("portfolio_vol_target")

    kwargs = dict(method=method, rebalance_freq=rebalance_freq)
    if portfolio_vol_target is not None:
        kwargs["portfolio_vol_target"] = float(portfolio_vol_target)

    combiner = PortfolioCombiner(
        strategies=strategies,
        **kwargs,
    )

    equity_curves: dict[str, list[float]] = config.get("equity_curves", {})
    result = combiner.combine(equity_curves)
    return result


def action_validate(config: dict) -> dict:
    from aipha.engines.statistical_validator import StatisticalValidator

    daily_returns = [float(r) for r in config.get("daily_returns", [])]
    if len(daily_returns) < 30:
        return {"error": "需要至少 30 个日收益率数据点"}

    observed_sharpe = config.get("observed_sharpe")
    n_trials = int(config.get("n_trials", 8))

    validator = StatisticalValidator(
        daily_returns=daily_returns,
        observed_sharpe=float(observed_sharpe) if observed_sharpe is not None else None,
        n_trials=n_trials,
    )
    report = validator.full_report()
    return report


def action_perturbation(config: dict) -> dict:
    from aipha.engines.portfolio_combiner import PortfolioCombiner, SubStrategy

    strategies_raw = config.get("strategies", [])
    strategies = []
    for s in strategies_raw:
        strategies.append(SubStrategy(
            name=s.get("name", "unknown"),
            weight=float(s.get("weight", 0)),
            asset_class=s.get("asset_class", "equity"),
            slippage_bps=float(s.get("slippage_bps", 10)),
        ))

    combiner = PortfolioCombiner(
        strategies=strategies,
        method=config.get("method", "fixed"),
        rebalance_freq=config.get("rebalance_freq", "monthly"),
    )

    equity_curves: dict[str, list[float]] = config.get("equity_curves", {})
    noise_pct = float(config.get("noise_pct", 0.05))
    n_runs = int(config.get("n_runs", 50))

    result = combiner.weight_perturbation_test(equity_curves, noise_pct=noise_pct, n_runs=n_runs)
    return result


def action_slippage(config: dict) -> dict:
    from aipha.engines.portfolio_combiner import PortfolioCombiner, SubStrategy

    strategies_raw = config.get("strategies", [])
    strategies = []
    for s in strategies_raw:
        strategies.append(SubStrategy(
            name=s.get("name", "unknown"),
            weight=float(s.get("weight", 0)),
            asset_class=s.get("asset_class", "equity"),
            slippage_bps=float(s.get("slippage_bps", 10)),
        ))

    combiner = PortfolioCombiner(
        strategies=strategies,
        method=config.get("method", "fixed"),
        rebalance_freq=config.get("rebalance_freq", "monthly"),
    )

    equity_curves: dict[str, list[float]] = config.get("equity_curves", {})
    bps_range = config.get("bps_range")
    if bps_range is None:
        bps_range = [0, 5, 10, 20, 30, 50]
    else:
        bps_range = [float(b) for b in bps_range]

    result = combiner.slippage_sensitivity_test(equity_curves, bps_range=bps_range)
    return result


ACTIONS = {
    "combine": action_combine,
    "validate": action_validate,
    "perturbation": action_perturbation,
    "slippage": action_slippage,
}


if __name__ == "__main__":
    config = get_input()
    action = config.get("action", "combine")

    try:
        handler = ACTIONS.get(action)
        if handler is None:
            result = {"error": f"未知操作: {action}，支持: {', '.join(ACTIONS.keys())}"}
        else:
            result = handler(config)
    except Exception as e:
        import traceback
        result = {"error": str(e), "traceback": traceback.format_exc()}

    print(json.dumps(result, ensure_ascii=False, default=str))
