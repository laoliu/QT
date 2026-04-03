"""
Dynamic Strategy Composer — combine any N backtrader strategies at runtime.

Four combination methods:
  - voting:         Majority vote — trade when >= minVotes strategies agree
  - weighted-score: Weighted signal sum — trade when score exceeds threshold
  - gated:          Primary strategy + filter strategies must all confirm
  - cascade:        Sequential filters — signal passes through each stage

Input (JSON via stdin or QUANT_CONFIG env var):
  {
    "action": "compose_backtest",
    "method": "voting",
    "minVotes": 2,
    "strategies": [
      {"strategyId": "macd", "weight": 1, "role": "primary", "params": {"fast_period": 12}},
      {"strategyId": "rsi", "weight": 2, "params": {"period": 14}},
      ...
    ],
    "symbols": ["600519"],
    "startDate": "2024-01-01",
    "endDate": "2025-01-01",
    "initialCash": 100000
  }

Output (JSON to stdout):
  {
    "ok": true,
    "compositionMethod": "voting",
    "strategyCount": 3,
    "results": { ...backtest metrics... }
  }
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any

_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)


def get_input() -> dict:
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


def _run_sub_backtest(strategy_id: str, params: dict, symbols: list[str],
                       start_date: str, end_date: str, initial_cash: float,
                       commission: float = 0.0003) -> dict:
    """Run a single strategy backtest and return signals + metrics."""
    try:
        from engines.backtrader_engine import run_backtest as bt_run

        result = bt_run({
            "strategyId": strategy_id,
            "symbols": symbols,
            "startDate": start_date,
            "endDate": end_date,
            "initialCash": initial_cash,
            "commission": commission,
            "parameters": params,
        })
        return result if isinstance(result, dict) else {"ok": False, "error": "invalid result"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _extract_signals(result: dict) -> list[dict]:
    """Extract trading signals from a backtest result."""
    signals = result.get("signals", [])
    if not isinstance(signals, list):
        return []
    return signals


def _signals_to_daily_state(signals: list[dict], date_index: list[str]) -> list[int]:
    """Convert discrete buy/sell signals to daily position state (1=long, 0=flat).
    Each buy sets state to 1, each sell sets state to 0."""
    # Build signal map: date -> direction
    sig_map: dict[str, str] = {}
    for sig in signals:
        d = sig.get("date", "")[:10]
        direction = sig.get("direction", "")
        if direction in ("buy", "sell"):
            # Last signal on a date wins
            sig_map[d] = direction

    state = 0
    daily: list[int] = []
    for d in date_index:
        if d in sig_map:
            state = 1 if sig_map[d] == "buy" else 0
        daily.append(state)
    return daily


def _state_to_signals(daily_states: list[int], date_index: list[str]) -> list[dict]:
    """Convert daily states back to discrete buy/sell signals on transitions."""
    signals: list[dict] = []
    prev = 0
    for i, (state, date) in enumerate(zip(daily_states, date_index)):
        if state == 1 and prev == 0:
            signals.append({"date": date, "direction": "buy",
                            "reason": "combined entry"})
        elif state == 0 and prev == 1:
            signals.append({"date": date, "direction": "sell",
                            "reason": "combined exit"})
        prev = state
    return signals


def _build_date_index(sub_results: list[dict]) -> list[str]:
    """Build a sorted union of all dates from sub-strategy signals."""
    all_dates: set[str] = set()
    for result in sub_results:
        for sig in _extract_signals(result):
            d = sig.get("date", "")[:10]
            if d:
                all_dates.add(d)
    return sorted(all_dates)


def _combine_voting(sub_results: list[dict], configs: list[dict], min_votes: int,
                    full_dates: list[str] | None = None) -> list[dict]:
    """Combine via voting on daily position states.
    For each date: count how many sub-strategies are 'long'. If >= minVotes, combined is long."""
    date_index = full_dates or _build_date_index(sub_results)
    if not date_index:
        return []

    # Build per-strategy daily states
    all_states: list[list[int]] = []
    for result in sub_results:
        sigs = _extract_signals(result)
        all_states.append(_signals_to_daily_state(sigs, date_index))

    # Vote: combined long if enough strategies are long
    combined_states: list[int] = []
    for i in range(len(date_index)):
        votes = sum(s[i] for s in all_states)
        combined_states.append(1 if votes >= min_votes else 0)

    return _state_to_signals(combined_states, date_index)


def _combine_weighted(sub_results: list[dict], configs: list[dict],
                       threshold: float, full_dates: list[str] | None = None) -> list[dict]:
    """Combine via weighted daily position score.
    For each date: weighted sum of position states. If normalized score >= threshold, combined is long."""
    date_index = full_dates or _build_date_index(sub_results)
    if not date_index:
        return []

    total_weight = sum(c.get("weight", 1) for c in configs)
    if total_weight == 0:
        return []

    all_states: list[list[int]] = []
    weights: list[float] = []
    for result, cfg in zip(sub_results, configs):
        sigs = _extract_signals(result)
        all_states.append(_signals_to_daily_state(sigs, date_index))
        weights.append(cfg.get("weight", 1))

    combined_states: list[int] = []
    for i in range(len(date_index)):
        score = sum(s[i] * w for s, w in zip(all_states, weights)) / total_weight
        combined_states.append(1 if score >= threshold else 0)

    return _state_to_signals(combined_states, date_index)


def _combine_gated(sub_results: list[dict], configs: list[dict],
                   full_dates: list[str] | None = None) -> list[dict]:
    """Combine via gated: primary must be long AND all filters must be long."""
    date_index = full_dates or _build_date_index(sub_results)
    if not date_index:
        return []

    primary_idx = [i for i, c in enumerate(configs) if c.get("role") == "primary"]
    if not primary_idx:
        primary_idx = [0]
    filter_idx = [i for i in range(len(configs)) if i not in primary_idx]

    all_states: list[list[int]] = []
    for result in sub_results:
        sigs = _extract_signals(result)
        all_states.append(_signals_to_daily_state(sigs, date_index))

    combined_states: list[int] = []
    for i in range(len(date_index)):
        # Primary must be long
        primary_long = any(all_states[p][i] == 1 for p in primary_idx)
        # All filters must be long
        filters_long = all(all_states[f][i] == 1 for f in filter_idx) if filter_idx else True
        combined_states.append(1 if (primary_long and filters_long) else 0)

    return _state_to_signals(combined_states, date_index)


def _combine_cascade(sub_results: list[dict], configs: list[dict],
                     full_dates: list[str] | None = None) -> list[dict]:
    """Combine via cascade: all strategies must be long simultaneously (AND chain)."""
    date_index = full_dates or _build_date_index(sub_results)
    if not date_index:
        return []

    all_states: list[list[int]] = []
    for result in sub_results:
        sigs = _extract_signals(result)
        all_states.append(_signals_to_daily_state(sigs, date_index))

    combined_states: list[int] = []
    for i in range(len(date_index)):
        # All strategies must be long
        combined_states.append(1 if all(s[i] == 1 for s in all_states) else 0)

    return _state_to_signals(combined_states, date_index)


def _calc_metrics(equity_curve: list[float], signals: list[dict],
                   initial_cash: float) -> dict:
    """Calculate performance metrics from an equity curve."""
    if not equity_curve or len(equity_curve) < 2:
        return {
            "totalReturn": 0, "annualizedReturn": 0, "maxDrawdown": 0,
            "sharpeRatio": 0, "winRate": 0, "totalTrades": len(signals),
            "finalValue": initial_cash,
        }

    final = equity_curve[-1]
    total_return = (final - initial_cash) / initial_cash * 100
    days = len(equity_curve)
    ann_return = ((final / initial_cash) ** (252 / max(days, 1)) - 1) * 100 if days > 0 else 0

    # Max drawdown
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Sharpe (daily returns)
    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev > 0:
            returns.append((equity_curve[i] - prev) / prev)
    if returns:
        avg_r = sum(returns) / len(returns)
        std_r = (sum((r - avg_r) ** 2 for r in returns) / len(returns)) ** 0.5
        sharpe = (avg_r / std_r * math.sqrt(252)) if std_r > 0 else 0
    else:
        sharpe = 0

    return {
        "totalReturn": round(total_return, 2),
        "annualizedReturn": round(ann_return, 2),
        "maxDrawdown": round(max_dd, 2),
        "sharpeRatio": round(sharpe, 2),
        "winRate": 0,  # Simplified; full implementation needs trade-level P&L
        "totalTrades": len(signals),
        "finalValue": round(final, 2),
    }


def _simulate_combined_signals(combined_signals: list[dict], symbols: list[str],
                                start_date: str, end_date: str,
                                initial_cash: float, commission: float) -> dict:
    """Simulate trading combined signals against actual price data."""
    import pandas as pd
    from engines.backtrader_engine import _fetch_data

    if not combined_signals or not symbols:
        return {"totalReturn": 0, "sharpeRatio": 0, "maxDrawdown": 0,
                "winRate": 0, "totalTrades": 0, "finalValue": initial_cash}

    # Fetch price data for the first symbol
    symbol = symbols[0]
    df = _fetch_data(symbol, start_date, end_date)
    if df is None or df.empty:
        return {"totalReturn": 0, "sharpeRatio": 0, "maxDrawdown": 0,
                "winRate": 0, "totalTrades": 0, "finalValue": initial_cash}

    # Build signal lookup: date_str -> direction
    signal_map: dict[str, str] = {}
    for sig in combined_signals:
        date_str = sig.get("date", "")
        if len(date_str) >= 10:
            signal_map[date_str[:10]] = sig.get("direction", "")

    # Simulate: simple long-only with buy/sell signals
    cash = initial_cash
    position = 0  # shares held
    avg_cost = 0.0
    equity_curve: list[float] = []
    trades: list[dict] = []
    wins = 0

    for idx_date, row in df.iterrows():
        date_str = str(idx_date)[:10]
        close = float(row["close"])
        direction = signal_map.get(date_str, "")

        if direction == "buy" and position == 0 and cash > 0:
            # Buy: use 95% of cash
            buy_cash = cash * 0.95
            shares = int(buy_cash / (close * (1 + commission)))
            if shares > 0:
                cost = shares * close * (1 + commission)
                cash -= cost
                avg_cost = close
                position = shares
                trades.append({"date": date_str, "direction": "buy",
                               "price": close, "shares": shares})

        elif direction == "sell" and position > 0:
            # Sell all
            proceeds = position * close * (1 - commission)
            cash += proceeds
            pnl = close - avg_cost
            if pnl > 0:
                wins += 1
            trades.append({"date": date_str, "direction": "sell",
                           "price": close, "shares": position, "pnl": pnl})
            position = 0

        # Mark to market
        equity = cash + position * close
        equity_curve.append(equity)

    # Close any open position at last price
    if position > 0 and len(df) > 0:
        last_close = float(df.iloc[-1]["close"])
        cash += position * last_close * (1 - commission)
        pnl = last_close - avg_cost
        if pnl > 0:
            wins += 1
        position = 0

    final_value = cash
    total_return = (final_value - initial_cash) / initial_cash * 100
    total_trades = len([t for t in trades if t["direction"] == "sell"])
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    # Max drawdown from equity curve
    max_dd = 0.0
    peak = initial_cash
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio from daily equity returns
    sharpe = 0.0
    if len(equity_curve) > 1:
        daily_returns = []
        for i in range(1, len(equity_curve)):
            if equity_curve[i - 1] > 0:
                daily_returns.append(
                    (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1])
        if daily_returns:
            avg_r = sum(daily_returns) / len(daily_returns)
            std_r = (sum((r - avg_r) ** 2 for r in daily_returns)
                     / len(daily_returns)) ** 0.5
            sharpe = (avg_r / std_r * (252 ** 0.5)) if std_r > 0 else 0

    return {
        "totalReturn": round(total_return, 2),
        "sharpeRatio": round(sharpe, 2),
        "maxDrawdown": round(max_dd, 2),
        "winRate": round(win_rate, 1),
        "totalTrades": total_trades,
        "finalValue": round(final_value, 2),
        "tradeLog": trades,
    }


def _get_full_date_index(symbols: list[str], start_date: str, end_date: str) -> list[str]:
    """Get all trading dates from price data (not just signal dates)."""
    from engines.backtrader_engine import _fetch_data
    for symbol in symbols:
        df = _fetch_data(symbol, start_date, end_date)
        if df is not None and not df.empty:
            return [str(d)[:10] for d in df.index]
    return []


def compose_backtest(config: dict) -> dict:
    """Main entry: run composed backtest."""
    method = config.get("method", "voting")
    strategies = config.get("strategies", [])
    symbols = config.get("symbols", ["000300"])
    start_date = config.get("startDate", "2024-01-01")
    end_date = config.get("endDate", "2025-01-01")
    initial_cash = float(config.get("initialCash", 100000))
    commission = float(config.get("commission", 0.0003))
    min_votes = config.get("minVotes", max(1, len(strategies) // 2))
    score_threshold = float(config.get("scoreThreshold", 0.5))

    if not strategies:
        return {"ok": False, "error": "No strategies provided"}

    # Run each sub-strategy backtest
    sub_results: list[dict] = []
    sub_configs: list[dict] = []
    for strat_cfg in strategies:
        sid = strat_cfg.get("strategyId", "")
        params = strat_cfg.get("params", {})
        result = _run_sub_backtest(sid, params, symbols, start_date, end_date,
                                    initial_cash, commission)
        sub_results.append(result)
        sub_configs.append(strat_cfg)

    # Get full trading date index from price data
    full_dates = _get_full_date_index(symbols, start_date, end_date)

    # Combine signals based on method
    if method == "voting":
        combined_signals = _combine_voting(sub_results, sub_configs, min_votes, full_dates)
    elif method == "weighted-score":
        combined_signals = _combine_weighted(sub_results, sub_configs, score_threshold, full_dates)
    elif method == "gated":
        combined_signals = _combine_gated(sub_results, sub_configs, full_dates)
    elif method == "cascade":
        combined_signals = _combine_cascade(sub_results, sub_configs, full_dates)
    else:
        return {"ok": False, "error": f"Unknown method: {method}"}

    # Simulate combined signals to get actual P&L
    combined_metrics = _simulate_combined_signals(
        combined_signals, symbols, start_date, end_date, initial_cash, commission)

    # Aggregate sub-strategy metrics for reference
    sub_summaries = []
    for result, cfg in zip(sub_results, sub_configs):
        sub_summaries.append({
            "strategyId": cfg.get("strategyId", ""),
            "ok": result.get("ok", False),
            "totalReturn": result.get("totalReturn", 0),
            "sharpeRatio": result.get("sharpeRatio", 0),
            "maxDrawdown": result.get("maxDrawdown", 0),
            "winRate": result.get("winRate", 0),
            "totalTrades": result.get("totalTrades", 0),
        })

    return {
        "ok": True,
        "compositionMethod": method,
        "strategyCount": len(strategies),
        "combinedSignals": combined_signals,
        "combinedSignalCount": len(combined_signals),
        "combinedMetrics": combined_metrics,
        "subStrategySummaries": sub_summaries,
    }


def main():
    try:
        config = get_input()
        action = config.get("action", "compose_backtest")

        if action == "compose_backtest":
            result = compose_backtest(config)
        else:
            result = {"ok": False, "error": f"Unknown action: {action}"}

        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
