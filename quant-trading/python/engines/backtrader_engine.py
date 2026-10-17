#!/usr/bin/env python3
"""
Backtrader engine bridge — runs backtrader strategies from Node.js.

Input: JSON via stdin or QUANT_CONFIG env var.
Output: JSON to stdout with backtest results.

Supports 10 strategies:
  - 3 legacy inline strategies (dual-ma-crossover, momentum-breakout, mean-reversion)
  - 7 strategies from strategies/ library (macd, rsi, kdj, turtle, pair-trading, grid, auction)

Usage:
  echo '{"strategyId": "macd", ...}' | python -m engines.backtrader_engine --json

Requirements:
  pip install backtrader akshare pandas yfinance
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime

# Ensure the python/ directory is on sys.path for strategy imports
_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)


def get_input():
    """Read input from stdin or QUANT_CONFIG env."""
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


def run_backtest(config):
    """Run a backtrader backtest."""
    try:
        import backtrader as bt
        import pandas as pd
    except ImportError as e:
        return {"ok": False, "error": f"Missing dependency: {e}. Run: pip install backtrader pandas"}

    strategy_id = config.get("strategyId", "dual-ma-crossover")
    run_id = str(config.get("runId") or uuid.uuid4().hex)
    symbols = config.get("symbols", ["000300"])
    start_date = config.get("startDate", "2024-01-01")
    end_date = config.get("endDate", datetime.now().strftime("%Y-%m-%d"))
    initial_cash = config.get("initialCash", 100000)
    commission = config.get("commission", 0.0003)
    parameters = config.get("parameters", {})
    market = config.get("market")  # Optional market hint

    # Create cerebro
    cerebro = bt.Cerebro()
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=commission)

    # Add strategy — try registry first, then legacy inline
    strategy_class = _resolve_strategy(strategy_id, parameters)
    if strategy_class is None:
        return {"ok": False, "error": f"Unknown strategy: {strategy_id}"}

    # Convert parameter keys from camelCase to snake_case for Python strategies
    py_params = _camel_to_snake_params(parameters)
    cerebro.addstrategy(strategy_class, **py_params)

    # Add data — use provider_factory for market-aware routing
    data_count = 0
    data_as_of = ""
    for symbol in symbols:
        data = _fetch_data(symbol, start_date, end_date, market)
        if data is not None and not data.empty:
            observed = data.index.max()
            if hasattr(observed, "strftime"):
                observed = observed.strftime("%Y-%m-%d")
            data_as_of = max(data_as_of, str(observed)[:10])
            bt_data = bt.feeds.PandasData(dataname=data)
            cerebro.adddata(bt_data, name=symbol)
            data_count += 1

    if data_count == 0:
        return {"ok": False, "error": f"Could not fetch data for any of: {symbols}"}

    # Add analyzers
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe", riskfreerate=0.0)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")

    # Run
    results = cerebro.run()
    strat = results[0]

    # Extract results
    sharpe = strat.analyzers.sharpe.get_analysis()
    dd = strat.analyzers.drawdown.get_analysis()
    trades = strat.analyzers.trades.get_analysis()
    returns = strat.analyzers.returns.get_analysis()

    final_value = cerebro.broker.getvalue()
    total_return = (final_value - initial_cash) / initial_cash * 100

    total_trades = trades.get("total", {}).get("total", 0)
    won = trades.get("won", {}).get("total", 0)
    win_rate = (won / total_trades * 100) if total_trades > 0 else 0

    # Collect signals from BaseStrategy subclasses
    signals = []
    if hasattr(strat, "get_signals"):
        signals = strat.get_signals()
    strategy_version = "1.0.0"
    if getattr(strat, "STRATEGY_META", None) is not None:
        strategy_version = getattr(strat.STRATEGY_META, "version", "1.0.0")
    for index, signal in enumerate(signals):
        signal.setdefault("runId", run_id)
        signal.setdefault("strategyVersion", strategy_version)
        signal.setdefault("dataTimestamp", signal.get("timestamp"))
        signal.setdefault("signalId", f"{run_id}:{strategy_id}:{signal.get('symbol', '')}:{signal.get('timestamp', '')}:{index}")

    return {
        "runId": run_id,
        "strategyVersion": strategy_version,
        "dataAsOf": data_as_of or end_date,
        "signalFormatVersion": "1.0",
        "runEnvelope": {
            "schemaVersion": "1.0",
            "runId": run_id,
            "strategyId": strategy_id,
            "strategyVersion": strategy_version,
            "engine": "backtrader",
            "dataAsOf": data_as_of or end_date,
            "signalFormat": "trading_signal",
            "signals": signals,
        },
        "strategyId": strategy_id,
        "symbols": symbols,
        "startDate": start_date,
        "endDate": end_date,
        "initialCash": initial_cash,
        "finalValue": round(final_value, 2),
        "totalReturn": round(total_return, 2),
        "annualizedReturn": round(returns.get("rnorm100", 0), 2),
        "maxDrawdown": round(dd.get("max", {}).get("drawdown", 0), 2),
        "maxDrawdownDuration": dd.get("max", {}).get("len", 0),
        "sharpeRatio": round(sharpe.get("sharperatio", 0) or 0, 3),
        "winRate": round(win_rate, 1),
        "totalTrades": total_trades,
        "signals": signals,
        "equityCurve": [],
    }


# ── Strategy Resolution ──────────────────────────────────────────


def _resolve_strategy(strategy_id: str, parameters: dict):
    """Resolve strategy class: check registry first, then legacy inline."""
    # 1. Try the strategy registry (new 6 strategies)
    try:
        from strategies.base import get_strategy_class  # noqa: E402
        cls = get_strategy_class(strategy_id)
        if cls is not None:
            return cls
    except ImportError:
        print("[backtrader_engine] strategies.base not available, using legacy only", file=sys.stderr)

    # 2. Fall back to legacy inline strategies
    return _get_legacy_strategy(strategy_id, parameters)


def _get_legacy_strategy(strategy_id: str, parameters: dict):
    """Legacy inline strategy definitions (backward-compatible)."""
    import backtrader as bt

    if strategy_id == "dual-ma-crossover":
        class DualMACrossover(bt.Strategy):
            params = (
                ("fast_period", parameters.get("fastPeriod", 10)),
                ("slow_period", parameters.get("slowPeriod", 30)),
            )

            def __init__(self):
                self.fast_ma = bt.indicators.SMA(self.data.close, period=self.p.fast_period)
                self.slow_ma = bt.indicators.SMA(self.data.close, period=self.p.slow_period)
                self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)

            def next(self):
                if self.crossover > 0:
                    self.buy()
                elif self.crossover < 0:
                    self.sell()

        return DualMACrossover

    elif strategy_id == "momentum-breakout":
        class MomentumBreakout(bt.Strategy):
            params = (("lookback", parameters.get("lookback", 20)),)

            def __init__(self):
                self.highest = bt.indicators.Highest(self.data.high, period=self.p.lookback)
                self.lowest = bt.indicators.Lowest(self.data.low, period=self.p.lookback)

            def next(self):
                if self.data.close[0] > self.highest[-1]:
                    self.buy()
                elif self.data.close[0] < self.lowest[-1]:
                    self.sell()

        return MomentumBreakout

    elif strategy_id == "mean-reversion":
        class MeanReversion(bt.Strategy):
            params = (
                ("period", parameters.get("period", 20)),
                ("devfactor", parameters.get("stdDev", 2.0)),
            )

            def __init__(self):
                self.bband = bt.indicators.BollingerBands(
                    self.data.close,
                    period=self.p.period,
                    devfactor=self.p.devfactor,
                )

            def next(self):
                if self.data.close[0] < self.bband.lines.bot[0]:
                    self.buy()
                elif self.data.close[0] > self.bband.lines.top[0]:
                    self.sell()

        return MeanReversion

    return None


# ── Data Fetching ─────────────────────────────────────────────────


def _fetch_data(symbol: str, start_date: str, end_date: str, market: str | None = None):
    """Fetch data via provider_factory (market-aware), with legacy fallback."""
    try:
        from data_providers.provider_factory import fetch_data_for_backtest
        return fetch_data_for_backtest(symbol, start_date, end_date, market)
    except ImportError:
        print("[backtrader_engine] provider_factory not available, using legacy akshare", file=sys.stderr)
        return _fetch_data_legacy(symbol, start_date, end_date)


def _fetch_data_legacy(symbol: str, start_date: str, end_date: str):
    """Legacy: fetch A-share data via akshare only."""
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
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
        })

        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df = df[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()
        return df

    except Exception as e:
        print(f"Warning: Could not fetch data for {symbol}: {e}", file=sys.stderr)
        return None


# ── Utils ─────────────────────────────────────────────────────────

def _camel_to_snake_params(params: dict) -> dict:
    """Convert camelCase parameter keys to snake_case for Python strategies.

    E.g., fastPeriod → fast_period, gridPct → grid_pct.
    Legacy strategies use their own param names so they skip this.
    """
    import re
    result = {}
    for key, value in params.items():
        snake = re.sub(r"([A-Z])", r"_\1", key).lower()
        result[snake] = value
    return result


# ── Strategy Listing ──────────────────────────────────────────────


def list_strategies():
    """Return all available strategy definitions (legacy + registry)."""
    # Start with legacy strategies
    strategies = [
        {
            "id": "dual-ma-crossover",
            "name": "双均线交叉 (Dual MA Crossover)",
            "description": "经典双均线策略：短期均线上穿长期均线买入，下穿卖出。",
            "category": "trend",
        },
        {
            "id": "momentum-breakout",
            "name": "动量突破 (Momentum Breakout)",
            "description": "N日最高价突破买入，跌破N日最低价卖出。",
            "category": "trend",
        },
        {
            "id": "mean-reversion",
            "name": "均值回归 (Mean Reversion)",
            "description": "布林带策略：价格触及下轨买入，触及上轨卖出。",
            "category": "oscillator",
        },
    ]

    # Merge in registry strategies (avoids duplicates)
    try:
        from strategies.base import get_all_strategies
        registry_ids = {s["id"] for s in strategies}
        for s in get_all_strategies():
            if s["id"] not in registry_ids:
                strategies.append(s)
    except ImportError:
        pass

    return strategies


if __name__ == "__main__":
    config = get_input()
    action = config.get("action")

    if action == "list_strategies":
        result = list_strategies()
    else:
        result = run_backtest(config)

    print(json.dumps(result, ensure_ascii=False))
