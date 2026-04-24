#!/usr/bin/env python3
"""
Download tick data via xtdata (QMT) — callable via Python bridge (stdin JSON → stdout JSON).

Input (JSON via stdin):
  { "codes": ["600519.SH"], "date": "20260424" }

Output (JSON to stdout):
  { "status": "ok", "600519.SH": { "count": 4993, "valid": 4803,
    "price_range": [1413.1, 1458.88], "time_range": ["09:25:04", "15:00:04"],
    "ticks": [ ... sample ticks ... ] } }
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

TICK_CACHE_DIR = os.environ.get("QT_TICK_CACHE_DIR", "G:/qtdata/cache/tick")


def download_ticks(codes: list[str], trade_date: str) -> dict:
    """Download tick data from xtdata for given codes and date."""
    try:
        # Suppress xtdata connection banner on stdout
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            from xtquant import xtdata
            xtdata.enable_hello = False
    except ImportError:
        return {"status": "error", "message": "xtquant 未安装"}

    result: dict = {"status": "ok"}

    for code in codes:
        try:
            # Download tick history
            xtdata.download_history_data(code, "tick", trade_date, trade_date)

            # Fetch data
            data = xtdata.get_market_data_ex(
                [], [code], period="tick",
                start_time=trade_date, end_time=trade_date,
            )

            df = data.get(code)
            if df is None or df.empty:
                result[code] = {"count": 0, "valid": 0, "ticks": [], "message": "无数据"}
                continue

            total = len(df)

            # Filter valid ticks (price > 0)
            valid_df = df[df["lastPrice"] > 0]
            valid_count = len(valid_df)

            # Price range
            price_min = float(valid_df["lastPrice"].min()) if valid_count else 0
            price_max = float(valid_df["lastPrice"].max()) if valid_count else 0

            # Time range
            time_range = [None, None]
            if valid_count > 0 and "time" in valid_df.columns:
                first_t = int(valid_df["time"].iloc[0])
                last_t = int(valid_df["time"].iloc[-1])
                if first_t > 1e12:
                    time_range = [
                        datetime.fromtimestamp(first_t / 1000).strftime("%H:%M:%S"),
                        datetime.fromtimestamp(last_t / 1000).strftime("%H:%M:%S"),
                    ]

            # Cache to Parquet
            cache_path = Path(TICK_CACHE_DIR) / code / f"{trade_date}.parquet"
            cache_msg = ""
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(str(cache_path))
                cache_msg = f"已缓存到 {cache_path}"
            except Exception as e:
                cache_msg = f"缓存失败: {e}"

            # Build sample ticks (first 100 + last 100)
            sample_df = valid_df if valid_count <= 200 else valid_df.iloc[list(range(100)) + list(range(-100, 0))]
            ticks = []
            for _, row in sample_df.iterrows():
                tick = {
                    "price": float(row.get("lastPrice", 0)),
                    "volume": int(row.get("volume", 0)),
                    "amount": float(row.get("amount", 0)),
                }
                if "time" in row.index:
                    tick["timestamp"] = int(row["time"])
                # Bid/ask arrays
                for field in ("bidPrice", "askPrice", "bidVol", "askVol"):
                    if field in row.index:
                        val = row[field]
                        if isinstance(val, list):
                            tick[field] = val
                ticks.append(tick)

            result[code] = {
                "count": total,
                "valid": valid_count,
                "price_range": [price_min, price_max],
                "time_range": time_range,
                "ticks": ticks,
                "cache": cache_msg,
                "message": f"共 {total} 条, 有效 {valid_count} 条",
            }

        except Exception as e:
            result[code] = {"count": 0, "valid": 0, "ticks": [], "error": str(e)}

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.parse_args()

    raw = sys.stdin.read()
    try:
        params = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        params = {}

    codes = params.get("codes", [])
    date = params.get("date", "")

    if not codes or not date:
        result = {"status": "error", "message": "codes 和 date 必填"}
    else:
        result = download_ticks(codes, date)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
