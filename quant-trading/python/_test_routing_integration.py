"""Quick integration test for new routing functions."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engines.strategy_router import (
    recommend_for_stock,
    profile_route_backtest,
    enrich_candidates_with_routing,
    get_market_regime,
    recommend_with_regime,
)

print("=" * 60)
print("  Integration Test: Strategy Router New Functions")
print("=" * 60)

# Test 1: recommend_for_stock (baseline)
print("\n[Test 1] recommend_for_stock('600519')")
rec = recommend_for_stock("600519", "2026-03-20")
print(f"  ok={rec['ok']}  combo={rec.get('combo',{}).get('name','?')}  source={rec.get('source','?')}")
assert rec["ok"], "recommend_for_stock failed"

# Test 2: profile_route_backtest (new!)
print("\n[Test 2] profile_route_backtest(['600519'])")
result = profile_route_backtest(["600519"], "2024-10-08", "2026-03-20")
print(f"  ok={result['ok']}  total={result.get('total', 0)}")
if result["results"]:
    r = result["results"][0]
    bt = r.get("backtest") or {}
    print(f"  symbol={r.get('symbol')}  profile={r.get('profileKey')}")
    print(f"  combo={r.get('combo',{}).get('name','?')}  source={r.get('routingSource')}")
    print(f"  return={bt.get('totalReturn',0):.1f}%  sharpe={bt.get('sharpeRatio',0):.2f}  maxDD={bt.get('maxDrawdown',0):.1f}%")
assert result["ok"], "profile_route_backtest failed"

# Test 3: enrich_candidates_with_routing (new!)
print("\n[Test 3] enrich_candidates_with_routing")
enriched = enrich_candidates_with_routing(
    [{"symbol": "600519", "name": "贵州茅台"}, {"symbol": "000858", "name": "五粮液"}],
    "2026-03-20",
)
for item in enriched:
    rt = item.get("routing") or {}
    print(f"  {item.get('symbol')} → combo={rt.get('combo','?')}  profile={rt.get('profileKey','?')}  source={rt.get('source','?')}")
assert len(enriched) == 2, "enrich should return 2 items"

# Test 4: recommend_with_regime (new!)
print("\n[Test 4] recommend_with_regime('600519')")
rec_regime = recommend_with_regime("600519", "2026-03-20")
print(f"  ok={rec_regime['ok']}  combo={rec_regime.get('combo',{}).get('name','?')}")
print(f"  regime={rec_regime.get('macroRegime','?')}  adjustment={rec_regime.get('regimeAdjustment','none')}")
assert rec_regime["ok"], "recommend_with_regime failed"

# Test 5: get_market_regime (new!)
print("\n[Test 5] get_market_regime()")
regime = get_market_regime()
print(f"  regime={regime.get('regime','?')}  description={regime.get('description','?')[:60]}")

# Test 6: Multi-stock profile_route_backtest
print("\n[Test 6] profile_route_backtest(['600519','000858','300750'])")
result3 = profile_route_backtest(["600519", "000858", "300750"], "2024-10-08", "2026-03-20")
print(f"  ok={result3['ok']}  total={result3.get('total', 0)}")
for r in result3.get("results", []):
    bt = r.get("backtest") or {}
    status = "✓" if r.get("ok") else "✗"
    print(f"  {status} {r.get('symbol')}  {r.get('profileKey','')}  combo={r.get('combo',{}).get('name','?')}  ret={bt.get('totalReturn',0):.1f}%")

print("\n" + "=" * 60)
print("  ALL TESTS PASSED ✓")
print("=" * 60)
