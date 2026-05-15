"""Test fundamental data sources."""
import sys
sys.path.insert(0, ".")
import akshare as ak

# Test 1: East Money stock info
print("=== East Money stock_individual_info_em ===")
try:
    df = ak.stock_individual_info_em(symbol="600519")
    print(df.to_string())
except Exception as e:
    print(f"Failed: {e}")

# Test 2: AKShare valuation indicators
print("\n=== AKShare stock_a_indicator_lg ===")
try:
    df = ak.stock_a_indicator_lg(symbol="600519")
    print(f"Columns: {list(df.columns)}")
    print(df.tail(3).to_string())
except Exception as e:
    print(f"Failed: {e}")

# Test 3: Existing prefetch approach
print("\n=== Cache prefetch ===")
try:
    from cache.prefetch import prefetch_fundamentals
    print(f"prefetch_fundamentals available: True")
except Exception as e:
    print(f"Failed: {e}")

# Test 4: Financial provider
print("\n=== Financial provider ===")
try:
    from data_providers.financial_provider import fetch_financials
    snap = fetch_financials("600519", market="cn_stock")
    if snap:
        print(f"pe_ttm={snap.pe_ttm}, pb={snap.pb}, roe={snap.roe}, dv={getattr(snap, 'dividend_yield', None)}")
    else:
        print("No data")
except Exception as e:
    print(f"Failed: {e}")

# Test 5: stock_cache with East Money
print("\n=== stock_cache ===")
try:
    from cache.stock_cache import get_cache
    c = get_cache()
    cached, missing = c.get_fundamentals(["600519"])
    print(f"Cached: {cached}")
    print(f"Missing: {missing}")
except Exception as e:
    print(f"Failed: {e}")
