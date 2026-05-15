"""Quick test of the swing suitability screener."""
import json
import random
import traceback

try:
    from screeners.swing_suitability_screener import SwingSuitabilityScreener

    klines = []
    base = 15.0
    for i in range(90):
        c = base + random.uniform(-0.5, 0.5) + i * 0.02
        klines.append({
            "open": c - 0.1,
            "high": c + 0.3,
            "low": c - 0.3,
            "close": c,
            "volume": 5000000 + random.randint(0, 2000000),
        })

    stock_data = {"600703": {"name": "三安光电", "klines": klines}}
    screener = SwingSuitabilityScreener(params={"lookback": 60})
    results = screener.screen(stock_data, market="cn_stock", limit=50)
    print(f"Results count: {len(results)}")
    for r in results:
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
except Exception as e:
    traceback.print_exc()
    print(f"ERROR: {e}")
