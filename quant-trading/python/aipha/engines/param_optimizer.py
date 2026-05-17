"""
Strategy parameter optimizer — grid search and random search over backtrader strategy parameters.

Input (JSON via stdin or QUANT_CONFIG env var):
  {
    "action": "optimize",
    "strategyId": "macd",
    "symbols": ["000300"],
    "startDate": "2023-01-01",
    "endDate": "2024-01-01",
    "initialCash": 100000,
    "commission": 0.0003,
    "method": "grid",          // "grid" | "random"
    "maxTrials": 50,           // max combinations for random search
    "metric": "sharpeRatio",   // metric to optimize: sharpeRatio | totalReturn | maxDrawdown | winRate
    "paramRanges": {
      "fast_period": {"min": 5, "max": 20, "step": 5},
      "slow_period": {"min": 20, "max": 60, "step": 10},
      "signal_period": {"min": 5, "max": 15, "step": 5}
    }
  }

Output (JSON to stdout):
  {
    "ok": true,
    "bestParams": {"fast_period": 10, "slow_period": 30, "signal_period": 9},
    "bestMetric": 1.23,
    "metric": "sharpeRatio",
    "method": "grid",
    "totalTrials": 24,
    "topResults": [
      {"params": {...}, "sharpeRatio": 1.23, "totalReturn": 15.2, ...},
      ...
    ]
  }

Usage:
  echo '{"action":"optimize","strategyId":"macd",...}' | python -m engines.param_optimizer
"""

from __future__ import annotations

import itertools
import json
import os
import random
import sys
from datetime import datetime
from typing import Any

# Ensure the python/ directory is on sys.path for strategy imports
_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)


def get_input() -> dict:
    """Read input from stdin or QUANT_CONFIG env."""
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


# ── Parameter Grid Generation ─────────────────────────────────────


def _generate_param_values(range_spec: dict) -> list:
    """Generate parameter values from a range specification.

    Supports:
      - {"min": 5, "max": 20, "step": 5}  → [5, 10, 15, 20]
      - {"values": [5, 10, 15]}             → [5, 10, 15]
    """
    if "values" in range_spec:
        return list(range_spec["values"])

    min_val = range_spec.get("min", 0)
    max_val = range_spec.get("max", 100)
    step = range_spec.get("step", 1)

    # Handle float steps properly
    if isinstance(step, float) or isinstance(min_val, float) or isinstance(max_val, float):
        values = []
        current = float(min_val)
        while current <= float(max_val) + 1e-9:
            values.append(round(current, 6))
            current += step
        return values

    return list(range(int(min_val), int(max_val) + 1, int(step)))


def _generate_grid_combinations(param_ranges: dict[str, dict]) -> list[dict[str, Any]]:
    """Generate all parameter combinations for grid search."""
    param_names = list(param_ranges.keys())
    param_values = [_generate_param_values(param_ranges[name]) for name in param_names]

    combinations = []
    for values in itertools.product(*param_values):
        combo = dict(zip(param_names, values))
        combinations.append(combo)

    return combinations


def _generate_random_combinations(
    param_ranges: dict[str, dict], max_trials: int
) -> list[dict[str, Any]]:
    """Generate random parameter combinations for random search."""
    combinations = []

    for _ in range(max_trials):
        combo = {}
        for name, range_spec in param_ranges.items():
            if "values" in range_spec:
                combo[name] = random.choice(range_spec["values"])
            else:
                min_val = range_spec.get("min", 0)
                max_val = range_spec.get("max", 100)
                step = range_spec.get("step", 1)

                if isinstance(step, float) or isinstance(min_val, float):
                    # Float range: sample continuously, then snap to step grid
                    raw = random.uniform(float(min_val), float(max_val))
                    snapped = round(round((raw - min_val) / step) * step + min_val, 6)
                    combo[name] = min(snapped, max_val)
                else:
                    # Integer range: sample from step grid
                    steps = list(range(int(min_val), int(max_val) + 1, int(step)))
                    combo[name] = random.choice(steps) if steps else int(min_val)

        combinations.append(combo)

    # Deduplicate
    seen = set()
    unique = []
    for combo in combinations:
        key = tuple(sorted(combo.items()))
        if key not in seen:
            seen.add(key)
            unique.append(combo)

    return unique


# ── Single Backtest Run ───────────────────────────────────────────


def _run_single_backtest(
    strategy_class,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_cash: float,
    commission: float,
    params: dict[str, Any],
    market: str | None = None,
) -> dict[str, Any] | None:
    """Run a single backtest with given parameters. Returns metrics or None on failure."""
    try:
        import backtrader as bt
    except ImportError:
        return None

    cerebro = bt.Cerebro()
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=commission)

    # Add strategy with params
    cerebro.addstrategy(strategy_class, **params)

    # Add data
    data_count = 0
    for symbol in symbols:
        data = _fetch_data(symbol, start_date, end_date, market)
        if data is not None and not data.empty:
            bt_data = bt.feeds.PandasData(dataname=data)
            cerebro.adddata(bt_data, name=symbol)
            data_count += 1

    if data_count == 0:
        return None

    # Add analyzers
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.0)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")

    # Run
    results = cerebro.run()
    strat = results[0]

    # Extract metrics
    sharpe = strat.analyzers.sharpe.get_analysis()
    dd = strat.analyzers.drawdown.get_analysis()
    trades = strat.analyzers.trades.get_analysis()
    returns = strat.analyzers.returns.get_analysis()

    final_value = cerebro.broker.getvalue()
    total_return = (final_value - initial_cash) / initial_cash * 100

    total_trades = trades.get("total", {}).get("total", 0)
    won = trades.get("won", {}).get("total", 0)
    win_rate = (won / total_trades * 100) if total_trades > 0 else 0

    return {
        "params": params,
        "finalValue": round(final_value, 2),
        "totalReturn": round(total_return, 2),
        "annualizedReturn": round(returns.get("rnorm100", 0), 2),
        "maxDrawdown": round(dd.get("max", {}).get("drawdown", 0), 2),
        "sharpeRatio": round(sharpe.get("sharperatio", 0) or 0, 3),
        "winRate": round(win_rate, 1),
        "totalTrades": total_trades,
    }


# ── Data Fetching (reuse from backtrader_engine) ──────────────────


def _fetch_data(symbol: str, start_date: str, end_date: str, market: str | None = None):
    """Fetch data via provider_factory, with legacy fallback."""
    try:
        from aipha.data.providers.provider_factory import fetch_data_for_backtest
        return fetch_data_for_backtest(symbol, start_date, end_date, market)
    except ImportError:
        return _fetch_data_legacy(symbol, start_date, end_date)


def _fetch_data_legacy(symbol: str, start_date: str, end_date: str):
    """Legacy: fetch A-share data via akshare."""
    try:
        import akshare as ak
        import pandas as pd

        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",
        )
        if df is None or df.empty:
            return None

        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
        })
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df = df[["open", "high", "low", "close", "volume"]].sort_index()
        return df
    except Exception as e:
        print(f"Warning: Could not fetch data for {symbol}: {e}", file=sys.stderr)
        return None


# ── Metric Extraction ─────────────────────────────────────────────


def _get_metric_value(result: dict, metric: str) -> float:
    """Extract metric value for comparison. Higher is better (drawdown is inverted)."""
    if metric == "maxDrawdown":
        # Lower drawdown is better → negate for max-comparison
        return -result.get("maxDrawdown", 999)
    return result.get(metric, float("-inf"))


# ── Strategy Resolution ───────────────────────────────────────────


def _resolve_strategy(strategy_id: str):
    """Resolve strategy class from registry or legacy inline."""
    try:
        from strategies.base import get_strategy_class
        cls = get_strategy_class(strategy_id)
        if cls is not None:
            return cls
    except ImportError:
        pass

    # Legacy strategies need special handling — they accept params at class-definition time
    # For optimization, we can't easily grid-search legacy strategies since params are baked in.
    # Return None and let caller handle the error.
    return None


def _get_default_param_ranges(strategy_id: str) -> dict[str, dict] | None:
    """Get default parameter ranges from strategy metadata.

    If the strategy defines min/max/step in its STRATEGY_META.parameters,
    use those as default ranges. Returns None if no metadata available.
    """
    try:
        from strategies.base import get_strategy_class
        cls = get_strategy_class(strategy_id)
        if cls is None or cls.STRATEGY_META is None:
            return None

        ranges = {}
        for p in cls.STRATEGY_META.parameters:
            if p.type != "number":
                continue
            if p.min is not None and p.max is not None:
                step = p.step if p.step is not None else 1
                ranges[p.name] = {"min": p.min, "max": p.max, "step": step}
            elif p.default is not None:
                # Create a range around the default value
                default = p.default
                step = p.step if p.step is not None else max(1, int(default * 0.1))
                lo = max(1, int(default * 0.5))
                hi = int(default * 2)
                ranges[p.name] = {"min": lo, "max": hi, "step": step}

        return ranges if ranges else None
    except ImportError:
        return None


# ── Main Optimize Function ────────────────────────────────────────


def optimize(config: dict) -> dict:
    """Run parameter optimization.

    Args:
        config: Full config dict with strategyId, paramRanges, method, etc.

    Returns:
        JSON-serializable result dict.
    """
    strategy_id = config.get("strategyId")
    if not strategy_id:
        return {"ok": False, "error": "Missing strategyId"}

    strategy_class = _resolve_strategy(strategy_id)
    if strategy_class is None:
        return {
            "ok": False,
            "error": f"Strategy '{strategy_id}' not found or is a legacy strategy. "
                     f"Only registry strategies (macd, rsi, kdj, turtle, grid, pair-trading, auction) support optimization.",
        }

    symbols = config.get("symbols", ["000300"])
    start_date = config.get("startDate", "2023-01-01")
    end_date = config.get("endDate", datetime.now().strftime("%Y-%m-%d"))
    initial_cash = config.get("initialCash", 100000)
    commission = config.get("commission", 0.0003)
    method = config.get("method", "grid")
    max_trials = config.get("maxTrials", 50)
    metric = config.get("metric", "sharpeRatio")
    market = config.get("market")

    # Parameter ranges — user-provided or auto-derived from strategy metadata
    param_ranges = config.get("paramRanges")
    if not param_ranges:
        param_ranges = _get_default_param_ranges(strategy_id)
        if not param_ranges:
            return {"ok": False, "error": "No paramRanges provided and could not derive defaults from strategy metadata."}

    # Validate metric
    valid_metrics = {"sharpeRatio", "totalReturn", "annualizedReturn", "maxDrawdown", "winRate"}
    if metric not in valid_metrics:
        return {"ok": False, "error": f"Invalid metric '{metric}'. Must be one of: {', '.join(sorted(valid_metrics))}"}

    # Generate parameter combinations
    if method == "random":
        combinations = _generate_random_combinations(param_ranges, max_trials)
    else:
        combinations = _generate_grid_combinations(param_ranges)
        # Cap grid search if too many combinations
        if len(combinations) > max_trials * 10:
            print(
                f"[param_optimizer] Grid has {len(combinations)} combos, "
                f"sampling {max_trials * 10} randomly",
                file=sys.stderr,
            )
            random.shuffle(combinations)
            combinations = combinations[: max_trials * 10]

    if not combinations:
        return {"ok": False, "error": "No parameter combinations generated. Check paramRanges."}

    print(f"[param_optimizer] Running {len(combinations)} trials for '{strategy_id}' on {symbols}", file=sys.stderr)

    # Run backtests
    results: list[dict[str, Any]] = []
    for i, params in enumerate(combinations):
        if (i + 1) % 10 == 0 or i == 0:
            print(f"[param_optimizer] Trial {i + 1}/{len(combinations)}: {params}", file=sys.stderr)

        result = _run_single_backtest(
            strategy_class, symbols, start_date, end_date,
            initial_cash, commission, params, market,
        )
        if result is not None:
            results.append(result)

    if not results:
        return {"ok": False, "error": "All backtest trials failed. Check data availability."}

    # Sort by target metric (best first)
    results.sort(key=lambda r: _get_metric_value(r, metric), reverse=True)

    best = results[0]
    top_n = min(10, len(results))

    return {
        "ok": True,
        "strategyId": strategy_id,
        "bestParams": best["params"],
        "bestMetric": best.get(metric, 0),
        "metric": metric,
        "method": method,
        "totalTrials": len(results),
        "totalCombinations": len(combinations),
        "symbols": symbols,
        "dateRange": f"{start_date} ~ {end_date}",
        "topResults": results[:top_n],
    }


# ── CLI Entry Point ───────────────────────────────────────────────

if __name__ == "__main__":
    config = get_input()
    action = config.get("action", "optimize")

    if action == "optimize":
        result = optimize(config)
    else:
        result = {"ok": False, "error": f"Unknown action: {action}"}

    print(json.dumps(result, ensure_ascii=False))
