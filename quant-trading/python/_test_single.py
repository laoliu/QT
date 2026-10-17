#!/usr/bin/env python3
"""Quick test: single combo backtest."""
import sys, os, sqlite3, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CACHE_DB = os.path.expanduser("~/data/cache/stock_data.db")

import pandas as pd
def _fetch_from_cache(symbol, start_date, end_date, market=None):
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

import engines.backtrader_engine as bt_engine
bt_engine._fetch_data = _fetch_from_cache
from engines.dynamic_composer import compose_backtest

config = {
    "action": "compose_backtest",
    "method": "voting",
    "strategies": [{"strategyId": "macd", "weight": 1}, {"strategyId": "kdj", "weight": 1}],
    "symbols": ["000001"],
    "startDate": "2024-10-08",
    "endDate": "2026-03-20",
    "initialCash": 100000,
    "minVotes": 2,
}
t0 = time.time()
r = compose_backtest(config)
print(f"Time: {time.time()-t0:.1f}s")
print(f"OK: {r.get('ok')}")
print(f"\nSub-strategy results:")
subs = r.get("subStrategySummaries", [])
for s in subs:
    print(f"  {s.get('strategyId')}: return={s.get('totalReturn',0):.1f}%, sharpe={s.get('sharpeRatio',0):.2f}")
print(f"\nCombined signals: {r.get('combinedSignalCount',0)}")
cm = r.get("combinedMetrics", {})
print(f"Combined P&L:  return={cm.get('totalReturn',0):.1f}%, sharpe={cm.get('sharpeRatio',0):.2f}, "
      f"maxDD={cm.get('maxDrawdown',0):.1f}%, winRate={cm.get('winRate',0):.1f}%, "
      f"trades={cm.get('totalTrades',0)}")
trades = cm.get("tradeLog", [])
for t in trades[:10]:
    print(f"  {t['date']} {t['direction']} @ {t['price']:.2f} x{t['shares']}"
          + (f" pnl={t.get('pnl',0):.2f}" if 'pnl' in t else ""))
