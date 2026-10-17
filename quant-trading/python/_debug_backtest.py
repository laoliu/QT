#!/usr/bin/env python3
"""Debug: single MACD backtest with cache data."""
import sqlite3, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd

CACHE_DB = os.path.expanduser("~/data/cache/stock_data.db")

def fetch_from_cache(symbol, start_date, end_date, market=None):
    conn = sqlite3.connect(CACHE_DB)
    df = pd.read_sql_query(
        "SELECT trade_date, open_price as open, high, low, close_price as close, volume "
        "FROM kline_data WHERE symbol = ? AND period = 'daily' "
        "AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
        conn, params=(symbol, start_date, end_date))
    conn.close()
    if df.empty:
        return None
    df.rename(columns={"trade_date": "date"}, inplace=True)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df

# Patch data fetching
import engines.backtrader_engine as bt_engine
bt_engine._fetch_data = fetch_from_cache

# Run single MACD backtest
result = bt_engine.run_backtest({
    "strategyId": "macd",
    "symbols": ["600519"],
    "startDate": "2024-06-01",
    "endDate": "2025-12-31",
    "initialCash": 100000,
})

# Print result (skip equity curve)
out = {k: v for k, v in result.items() if k != "equityCurve"}
out["signalCount"] = len(result.get("signals", []))
out["signals_sample"] = result.get("signals", [])[:5]
print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
