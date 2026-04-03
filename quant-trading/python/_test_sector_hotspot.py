"""Smoke test for sector_hotspot_screener."""
import sys
sys.path.insert(0, ".")

# Test 1: Verify screener registration
from screeners.base import list_screener_ids, get_screener

print("=== Test 1: Screener Registry ===")
ids = list_screener_ids()
print(f"Registered screeners ({len(ids)}): {ids}")
assert "sector_hotspot" in ids, "sector_hotspot not registered!"
print("OK: sector_hotspot is registered")

# Test 2: Instantiate screener
print("\n=== Test 2: Instantiate ===")
screener = get_screener("sector_hotspot")
assert screener is not None
print(f"ID: {screener.screener_id}")
print(f"Name: {screener.screener_name}")
print(f"Markets: {screener.supported_markets}")
print("OK: Instantiation passed")

# Test 3: Scoring methods (unit test with mock data)
print("\n=== Test 3: Scoring Methods ===")
params = screener._DEFAULTS

# Sector heat scoring
heat = screener._score_sector_heat(
    "光伏", 5,
    {"光伏": [{"code": str(i)} for i in range(5)]},
    "000001", params,
)
print(f"Sector heat (5 ZT): {heat:.1f} / {params['w_sector_heat']}")
assert heat > 0

heat2 = screener._score_sector_heat(
    "光伏", 2,
    {"光伏": [{"code": "1"}, {"code": "2"}]},
    "000001", params,
)
print(f"Sector heat (2 ZT): {heat2:.1f} / {params['w_sector_heat']}")
assert heat2 < heat, "2 ZT should score less than 5"

# Yesterday performance scoring
perf, sigs = screener._score_yesterday_perf(5.0, 8.0, params)
print(f"Yesterday perf (5%, 8% turn): {perf:.1f} / {params['w_yesterday_perf']} signals={sigs}")
assert perf > 0

perf2, _ = screener._score_yesterday_perf(-1.0, 2.0, params)
print(f"Yesterday perf (-1%, 2% turn): {perf2:.1f} / {params['w_yesterday_perf']}")
assert perf2 < perf, "negative change should score less"

# Fund flow scoring
flow, fsigs = screener._score_fund_flow({"mainNetIn": 5000, "mainNetInPct": 3.0}, params)
print(f"Fund flow (inflow): {flow:.1f} / {params['w_fund_flow']} signals={fsigs}")
assert flow > params["w_fund_flow"] * 0.5

flow2, _ = screener._score_fund_flow({"mainNetIn": -2000, "mainNetInPct": -1.0}, params)
print(f"Fund flow (outflow): {flow2:.1f} / {params['w_fund_flow']}")
assert flow2 < flow

# Market cap scoring
cap = screener._score_market_cap(50e8, params)
print(f"Market cap (50B): {cap:.1f} / {params['w_market_cap']}")
cap2 = screener._score_market_cap(200e8, params)
print(f"Market cap (200B): {cap2:.1f} / {params['w_market_cap']}")
assert cap > cap2, "50B (in optimal) should score higher than 200B"

# Technical scoring
tech, _ = screener._score_technical([], params)
print(f"Technical (no data): {tech:.1f} / {params['w_technical']}")

mock_klines = [{"close": 10 + i * 0.3, "volume": 1000 + i * 100} for i in range(20)]
tech2, tsigs2 = screener._score_technical(mock_klines, params)
print(f"Technical (uptrend): {tech2:.1f} / {params['w_technical']} signals={tsigs2}")
assert tech2 > tech

# Multi-sector check
extras = screener._check_multi_sector(
    "1",
    {"光伏": [{"code": "1"}, {"code": "2"}], "新能源": [{"code": "1"}, {"code": "3"}]},
    "光伏",
)
print(f"Multi-sector check: {extras}")
assert "新能源" in extras

print("\nOK: All scoring methods work correctly")
print("\n=== All smoke tests passed ===")
