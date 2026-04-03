#!/usr/bin/env python3
"""Smoke test for multi-source sector data provider integration."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Test 1: Import multi-source provider
from data_providers.sector_data import (
    fetch_sector_members_multi,
    fetch_zt_pool_multi,
    fetch_fund_flow_multi,
    fetch_sector_fund_flow_direct,
    _em_resolve_board_code,
    _em_sector_members,
    _em_zt_pool,
    _em_zt_pool_previous,
    _em_fund_flow_rank,
    _em_sector_fund_flow,
)
print("OK Import data_providers.sector_data")

# Test 2: Import screener
from screeners.base import _load_all_screeners, _SCREENER_REGISTRY
_load_all_screeners()
print(f"OK Loaded {len(_SCREENER_REGISTRY)} screeners")
assert "sector_hotspot" in _SCREENER_REGISTRY, "sector_hotspot not found!"
print("OK sector_hotspot registered")

# Test 3: Verify multi-source functions exist
from screeners.sector_hotspot_screener import (
    _fetch_sector_members,
    _fetch_fund_flow_rank,
    _fetch_hot_concept_boards,
    _fetch_concept_members,
)
print("OK All screener functions importable")

# Test 4: Try Eastmoney direct - resolve a board code
print("\n--- Live API tests ---")
try:
    bk = _em_resolve_board_code("电力")
    print(f"EM board code for '电力': {bk or '(not found)'}")
except Exception as e:
    print(f"EM board resolve failed: {e}")

# Test 5: Try Eastmoney direct sector members
try:
    members = _em_sector_members("电力")
    print(f"EM sector members '电力': {len(members)} stocks")
    if members:
        print(f"  Sample: {members[0]['code']} {members[0]['name']}")
except Exception as e:
    print(f"EM sector members failed: {e}")

# Test 6: Try Eastmoney direct ZT pool
try:
    stocks = _em_zt_pool_previous("20260320")
    print(f"EM zt_pool_previous(20260320): {len(stocks)} stocks")
    if stocks:
        print(f"  Sample: {stocks[0]['code']} {stocks[0]['name']} {stocks[0].get('industry','')}")
except Exception as e:
    print(f"EM zt_pool_previous failed: {e}")

# Test 7: Multi-source ZT pool
try:
    result = fetch_zt_pool_multi("20260320", use_previous=True)
    print(f"Multi-source zt_pool: {len(result.get('stocks',[]))} stocks, source={result.get('source','?')}")
except Exception as e:
    print(f"Multi-source zt_pool failed: {e}")

# Test 8: Multi-source sector members
try:
    members, source = fetch_sector_members_multi("电力")
    print(f"Multi-source sector_members('电力'): {len(members)} stocks, source={source}")
except Exception as e:
    print(f"Multi-source sector_members failed: {e}")

# Test 9: Multi-source fund flow
try:
    flow, source = fetch_fund_flow_multi()
    print(f"Multi-source fund_flow: {len(flow)} stocks, source={source}")
except Exception as e:
    print(f"Multi-source fund_flow failed: {e}")

# Test 10: Sector fund flow
try:
    sf = fetch_sector_fund_flow_direct()
    print(f"Sector fund flow: {len(sf)} sectors")
    if sf:
        top = sorted(sf.items(), key=lambda x: x[1].get("mainNetIn", 0), reverse=True)[:3]
        for name, data in top:
            print(f"  {name}: mainNetIn={data.get('mainNetIn',0):.0f}, chg={data.get('changePct',0):.2f}%")
except Exception as e:
    print(f"Sector fund flow failed: {e}")

print("\nAll tests done!")
