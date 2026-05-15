"""Check quarterly data from financial_provider."""
import sys
sys.path.insert(0, ".")
from data_providers.financial_provider import fetch_financials

snap = fetch_financials("600519", market="cn_stock")
if snap:
    # Check quarterly data
    if hasattr(snap, "quarterly_data") and snap.quarterly_data:
        q = snap.quarterly_data[0]
        print(f"Latest quarter keys: {list(q.keys())[:30]}")
        for k, v in list(q.items())[:20]:
            print(f"  {k}: {v}")
    else:
        print("No quarterly_data")
    
    # All non-None fields
    print("\nAll available fields:")
    for attr in dir(snap):
        if not attr.startswith("_"):
            val = getattr(snap, attr, None)
            if val is not None and not callable(val):
                print(f"  {attr}: {val}")
