#!/usr/bin/env python3
"""Check what signals strategies actually produce."""
import sys, os, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CACHE_DB = os.path.expanduser("~/.openclaw/extensions/quant-trading/cache/stock_data.db")
import pandas as pd
def _fetch_from_cache(symbol, start_date, end_date, market=None):
    conn = sqlite3.connect(CACHE_DB)
    df = pd.read_sql_query(
        "SELECT trade_date, open_price as open, high, low, close_price as close, volume "
        "FROM kline_data WHERE symbol = ? AND period = 'daily' "
        "AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
        conn, params=(symbol, start_date, end_date))
    conn.close()
    if df.empty: return None
    df.rename(columns={"trade_date": "date"}, inplace=True)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df

import engines.backtrader_engine as bt_engine
bt_engine._fetch_data = _fetch_from_cache
from engines.backtrader_engine import run_backtest

for sid in ["macd", "rsi", "kdj", "bollinger", "turtle", "auction"]:
    r = run_backtest({
        "strategyId": sid, "symbols": ["000001"],
        "startDate": "2024-10-08", "endDate": "2026-03-20",
        "initialCash": 100000,
    })
    sigs = r.get("signals", [])
    print(f"\n{sid}: {len(sigs)} signals, return={r.get('totalReturn',0):.1f}%")
    for s in sigs[:5]:
        print(f"  {s}")
