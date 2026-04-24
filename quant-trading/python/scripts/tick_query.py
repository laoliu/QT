#!/usr/bin/env python3
"""
Query cached tick data (Parquet) — callable via Python bridge (stdin JSON → stdout JSON).

Input (JSON via stdin):
  { "symbol": "600519.SH", "date": "20260424" }

Output (JSON to stdout):
  { "status": "ok", "count": 4803, "valid": 4500,
    "price_range": [1413.1, 1458.88],
    "time_range": ["09:25:04", "15:00:04"],
    "ticks": [ ... first 200 ticks ... ] }
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

TICK_CACHE_DIR = os.environ.get("QT_TICK_CACHE_DIR", "G:/qtdata/cache/tick")


def query_cached_ticks(symbol: str, date: str) -> dict:
    """Load tick data from Parquet cache and return summary + sample."""
    cache_dir = Path(TICK_CACHE_DIR)
    parquet_path = cache_dir / symbol / f"{date}.parquet"

    if not parquet_path.exists():
        return {"status": "no_cache", "message": f"缓存文件不存在: {parquet_path}"}

    try:
        import pandas as pd
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        return {"status": "error", "message": f"读取 Parquet 失败: {e}"}

    if df.empty:
        return {"status": "empty", "message": "Parquet 文件为空", "count": 0}

    total = len(df)

    # Detect price column
    price_col = "lastPrice" if "lastPrice" in df.columns else "price"
    if price_col not in df.columns:
        return {"status": "error", "message": f"缺少价格列, 现有列: {list(df.columns)}"}

    valid_df = df[df[price_col] > 0]
    valid_count = len(valid_df)

    # Price range
    price_min = float(valid_df[price_col].min()) if valid_count else 0
    price_max = float(valid_df[price_col].max()) if valid_count else 0

    # Time range
    time_col = "time" if "time" in df.columns else "timestamp"
    time_range = [None, None]
    if time_col in df.columns and valid_count > 0:
        times = valid_df[time_col]
        first_t = int(times.iloc[0])
        last_t = int(times.iloc[-1])
        # Convert ms timestamp to time string
        if first_t > 1e12:
            time_range = [
                datetime.fromtimestamp(first_t / 1000).strftime("%H:%M:%S"),
                datetime.fromtimestamp(last_t / 1000).strftime("%H:%M:%S"),
            ]
        else:
            time_range = [str(first_t), str(last_t)]

    # Build tick list (sample: first 100 + last 100)
    sample_df = valid_df if valid_count <= 200 else pd.concat([valid_df.head(100), valid_df.tail(100)])
    ticks = []
    for _, row in sample_df.iterrows():
        tick = {
            "price": float(row.get(price_col, 0)),
            "volume": int(row.get("volume", 0)),
            "amount": float(row.get("amount", 0)),
        }
        if time_col in row.index:
            t = row[time_col]
            if isinstance(t, (int, float)) and t > 1e12:
                tick["timestamp"] = int(t)
            else:
                tick["timestamp"] = str(t)
        # Bid/ask
        for field in ("bidPrice", "askPrice", "bidVol", "askVol"):
            if field in row.index:
                val = row[field]
                if isinstance(val, list):
                    tick[field] = val
        ticks.append(tick)

    return {
        "status": "ok",
        "count": total,
        "valid": valid_count,
        "price_range": [price_min, price_max],
        "time_range": time_range,
        "ticks": ticks,
        "message": f"共 {total} 条, 有效 {valid_count} 条",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.parse_args()

    raw = sys.stdin.read()
    try:
        params = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        params = {}

    symbol = params.get("symbol", "")
    date = params.get("date", "")

    if not symbol or not date:
        result = {"status": "error", "message": "symbol 和 date 必填"}
    else:
        result = query_cached_ticks(symbol, date)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
