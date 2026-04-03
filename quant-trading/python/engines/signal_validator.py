"""
Signal backtesting validator — verifies how historical strategy signals performed.

Runs a strategy backtest, collects signals, and for each signal measures
subsequent price movement to determine if the signal was profitable.

Input (JSON via stdin or QUANT_CONFIG env var):
  {
    "action": "validate_signals",
    "strategyId": "rsi",
    "symbols": ["000300"],
    "startDate": "2023-01-01",
    "endDate": "2024-01-01",
    "initialCash": 100000,
    "parameters": {},
    "holdingDays": [1, 3, 5, 10],    // measure returns after N days
    "market": null
  }

Output (JSON to stdout):
  {
    "ok": true,
    "strategyId": "rsi",
    "totalSignals": 24,
    "buySignals": 12,
    "sellSignals": 12,
    "accuracy": {
      "1d": {"buy": 58.3, "sell": 66.7},
      "3d": {"buy": 50.0, "sell": 75.0},
      ...
    },
    "avgReturn": {
      "1d": {"buy": 0.32, "sell": -0.21},
      ...
    },
    "signals": [
      {
        "date": "2023-03-15",
        "direction": "buy",
        "price": 10.50,
        "returns": {"1d": 0.5, "3d": 1.2, "5d": -0.3, "10d": 2.1},
        "correct": {"1d": true, "3d": true, "5d": false, "10d": true}
      },
      ...
    ]
  }

Usage:
  echo '{"action":"validate_signals","strategyId":"rsi",...}' | python -m engines.signal_validator
"""

from __future__ import annotations

import json
import os
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


# ── Data Fetching ─────────────────────────────────────────────────


def _fetch_data(symbol: str, start_date: str, end_date: str, market: str | None = None):
    """Fetch data via provider_factory, with legacy fallback."""
    try:
        from data_providers.provider_factory import fetch_data_for_backtest
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


# ── Signal Collection via Backtest ────────────────────────────────


def _collect_signals(
    strategy_id: str,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_cash: float,
    parameters: dict,
    market: str | None = None,
) -> tuple[list[dict], Any]:
    """Run a backtest and collect signals from the strategy.

    Returns:
        Tuple of (signals_list, price_dataframe_dict)
    """
    try:
        import backtrader as bt
        import pandas as pd
    except ImportError as e:
        raise ImportError(f"Missing dependency: {e}. Run: pip install backtrader pandas")

    # Resolve strategy
    try:
        from strategies.base import get_strategy_class
        strategy_class = get_strategy_class(strategy_id)
    except ImportError:
        strategy_class = None

    if strategy_class is None:
        raise ValueError(
            f"Strategy '{strategy_id}' not found. "
            "Only registry strategies support signal validation."
        )

    # Convert camelCase params to snake_case
    from engines.backtrader_engine import _camel_to_snake_params
    py_params = _camel_to_snake_params(parameters)

    cerebro = bt.Cerebro()
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=0.0003)
    cerebro.addstrategy(strategy_class, **py_params)

    # Add data and keep price DataFrames for post-analysis
    price_data: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = _fetch_data(symbol, start_date, end_date, market)
        if df is not None and not df.empty:
            price_data[symbol] = df
            bt_data = bt.feeds.PandasData(dataname=df)
            cerebro.adddata(bt_data, name=symbol)

    if not price_data:
        raise ValueError(f"Could not fetch data for any of: {symbols}")

    # Run
    results = cerebro.run()
    strat = results[0]

    signals = []
    if hasattr(strat, "get_signals"):
        signals = strat.get_signals()

    return signals, price_data


# ── Forward Return Calculation ────────────────────────────────────


def _calculate_forward_returns(
    signals: list[dict],
    price_data: dict[str, Any],
    holding_days: list[int],
) -> list[dict]:
    """For each signal, calculate forward returns at specified horizons.

    Args:
        signals: List of signal dicts with 'date', 'direction', 'price', 'symbol'
        price_data: Dict of symbol -> DataFrame with close prices
        holding_days: List of holding periods in trading days

    Returns:
        Enriched signal list with 'returns' and 'correct' dicts.
    """
    import pandas as pd

    enriched = []

    for sig in signals:
        symbol = sig.get("symbol", "")
        sig_date = sig.get("date", "")
        direction = sig.get("direction", "")
        entry_price = sig.get("price", 0)

        if not symbol or symbol not in price_data or entry_price <= 0:
            # Try using the first (or only) symbol's data
            if len(price_data) == 1:
                symbol = list(price_data.keys())[0]
            else:
                enriched.append({**sig, "returns": {}, "correct": {}})
                continue

        df = price_data[symbol]
        sig_dt = pd.Timestamp(sig_date)

        # Find the index position of signal date (or nearest after)
        idx_positions = df.index.get_indexer([sig_dt], method="ffill")
        if idx_positions[0] < 0:
            enriched.append({**sig, "returns": {}, "correct": {}})
            continue

        sig_idx = idx_positions[0]

        returns = {}
        correct = {}

        for days in holding_days:
            future_idx = sig_idx + days
            if future_idx < len(df):
                future_price = float(df.iloc[future_idx]["close"])
                pct_return = round((future_price - entry_price) / entry_price * 100, 3)
                returns[f"{days}d"] = pct_return

                # A signal is "correct" if:
                # - buy signal → price went up
                # - sell signal → price went down
                if direction in ("buy", "long"):
                    correct[f"{days}d"] = pct_return > 0
                elif direction in ("sell", "short"):
                    correct[f"{days}d"] = pct_return < 0
                else:
                    correct[f"{days}d"] = None
            else:
                returns[f"{days}d"] = None
                correct[f"{days}d"] = None

        enriched.append({
            **sig,
            "returns": returns,
            "correct": correct,
        })

    return enriched


# ── Summary Statistics ────────────────────────────────────────────


def _compute_summary(
    enriched_signals: list[dict],
    holding_days: list[int],
) -> tuple[dict, dict]:
    """Compute accuracy and average return summaries by direction and horizon.

    Returns:
        Tuple of (accuracy_dict, avg_return_dict)
    """
    accuracy: dict[str, dict[str, float | None]] = {}
    avg_return: dict[str, dict[str, float | None]] = {}

    for days in holding_days:
        key = f"{days}d"

        buy_correct = []
        sell_correct = []
        buy_returns = []
        sell_returns = []

        for sig in enriched_signals:
            direction = sig.get("direction", "")
            cor = sig.get("correct", {}).get(key)
            ret = sig.get("returns", {}).get(key)

            if direction in ("buy", "long"):
                if cor is not None:
                    buy_correct.append(1 if cor else 0)
                if ret is not None:
                    buy_returns.append(ret)
            elif direction in ("sell", "short"):
                if cor is not None:
                    sell_correct.append(1 if cor else 0)
                if ret is not None:
                    sell_returns.append(ret)

        accuracy[key] = {
            "buy": round(sum(buy_correct) / len(buy_correct) * 100, 1) if buy_correct else None,
            "sell": round(sum(sell_correct) / len(sell_correct) * 100, 1) if sell_correct else None,
        }
        avg_return[key] = {
            "buy": round(sum(buy_returns) / len(buy_returns), 3) if buy_returns else None,
            "sell": round(sum(sell_returns) / len(sell_returns), 3) if sell_returns else None,
        }

    return accuracy, avg_return


# ── Main Validate Function ────────────────────────────────────────


def validate_signals(config: dict) -> dict:
    """Run signal validation.

    Args:
        config: Full config dict.

    Returns:
        JSON-serializable result dict.
    """
    strategy_id = config.get("strategyId")
    if not strategy_id:
        return {"ok": False, "error": "Missing strategyId"}

    symbols = config.get("symbols", ["000300"])
    start_date = config.get("startDate", "2023-01-01")
    end_date = config.get("endDate", datetime.now().strftime("%Y-%m-%d"))
    initial_cash = config.get("initialCash", 100000)
    parameters = config.get("parameters", {})
    holding_days = config.get("holdingDays", [1, 3, 5, 10])
    market = config.get("market")

    try:
        signals, price_data = _collect_signals(
            strategy_id, symbols, start_date, end_date,
            initial_cash, parameters, market,
        )
    except (ImportError, ValueError) as e:
        return {"ok": False, "error": str(e)}

    if not signals:
        return {
            "ok": True,
            "strategyId": strategy_id,
            "totalSignals": 0,
            "buySignals": 0,
            "sellSignals": 0,
            "accuracy": {},
            "avgReturn": {},
            "signals": [],
            "note": "No signals generated in the given period.",
        }

    # Enrich signals with forward returns
    enriched = _calculate_forward_returns(signals, price_data, holding_days)

    # Compute summary stats
    buy_count = sum(1 for s in signals if s.get("direction") in ("buy", "long"))
    sell_count = sum(1 for s in signals if s.get("direction") in ("sell", "short"))

    accuracy, avg_return = _compute_summary(enriched, holding_days)

    return {
        "ok": True,
        "strategyId": strategy_id,
        "symbols": symbols,
        "dateRange": f"{start_date} ~ {end_date}",
        "totalSignals": len(signals),
        "buySignals": buy_count,
        "sellSignals": sell_count,
        "holdingDays": holding_days,
        "accuracy": accuracy,
        "avgReturn": avg_return,
        "signals": enriched,
    }


# ── CLI Entry Point ───────────────────────────────────────────────

if __name__ == "__main__":
    config = get_input()
    action = config.get("action", "validate_signals")

    if action == "validate_signals":
        result = validate_signals(config)
    else:
        result = {"ok": False, "error": f"Unknown action: {action}"}

    print(json.dumps(result, ensure_ascii=False, default=str))
