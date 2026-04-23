"""
Bridge script: Swing Suitability Scan.

Called by TypeScript bridge to evaluate portfolio holdings for
swing overlay trading suitability.

Input (JSON via stdin):
  {
    "stock_data": { "600519": { "name": "贵州茅台", "klines": [...] }, ... },
    "lookback": 60
  }

Output (JSON via stdout):
  {
    "results": [
      {
        "stock_id": "600519",
        "name": "贵州茅台",
        "score": 65.2,
        "rank": 1,
        "factors": { ... },
        "signals": [...]
      },
      ...
    ]
  }
"""
from __future__ import annotations

import json
import sys


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        print(json.dumps({"error": f"Invalid input: {e}"}))
        return

    stock_data_raw = data.get("stock_data", {})
    lookback = data.get("lookback", 60)

    if not stock_data_raw:
        print(json.dumps({"results": []}))
        return

    # Import screener
    try:
        sys.path.insert(0, ".")
        from screeners.swing_suitability_screener import SwingSuitabilityScreener
    except ImportError as e:
        print(json.dumps({"error": f"Failed to import screener: {e}"}), file=sys.stderr)
        print(json.dumps({"results": []}))
        return

    # Normalize kline data
    stock_data = {}
    for code, info in stock_data_raw.items():
        name = info.get("name", code)
        klines = info.get("klines", [])
        if not klines:
            continue

        # Normalize kline format (handle both camelCase and snake_case)
        normalized = []
        for k in klines:
            normalized.append({
                "close": float(k.get("close", 0)),
                "high": float(k.get("high", 0)),
                "low": float(k.get("low", 0)),
                "volume": float(k.get("volume", 0)),
                "open": float(k.get("open", 0)),
            })
        if normalized:
            stock_data[code] = {"name": name, "klines": normalized}

    if not stock_data:
        print(json.dumps({"results": []}))
        return

    # Run screener
    screener = SwingSuitabilityScreener(params={"lookback": lookback})
    results = screener.screen(stock_data, market="cn_stock", limit=50)

    # Serialize
    output = [r.to_dict() for r in results]
    print(json.dumps({"results": output}, ensure_ascii=False))


if __name__ == "__main__":
    main()
