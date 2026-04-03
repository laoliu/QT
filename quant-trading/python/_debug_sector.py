#!/usr/bin/env python3
"""Debug: trace sector hotspot screener data pipeline."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engines.lianban import fetch_zt_pool_previous, _safe_int
from screeners.sector_hotspot_screener import _fetch_sector_members, _prev_trading_date

prev = _prev_trading_date()
print(f"Previous trading date: {prev}")

pool = fetch_zt_pool_previous(prev)
stocks = pool.get("stocks", [])
print(f"Total ZT stocks: {len(stocks)}")

# Group by industry
sectors: dict[str, list] = {}
for s in stocks:
    ind = s.get("industry", "")
    if ind:
        sectors.setdefault(ind, []).append(s)

print(f"Industries found: {len(sectors)}\n")
for ind, lst in sorted(sectors.items(), key=lambda x: -len(x[1])):
    names = ", ".join(s["name"] for s in lst)
    print(f"  {ind}: {len(lst)} - [{names}]")

# Identify hotspots
hotspots = {k: v for k, v in sectors.items() if len(v) >= 2}
print(f"\nHotspots (>=2 ZT): {len(hotspots)} sectors")

if not hotspots:
    print("\nNo hotspot found. Try lowering min_sector_zt to 1 or use a different date.")
    sys.exit(0)

for ind, lst in hotspots.items():
    print(f"\n  [{ind}] {len(lst)} ZT stocks:")
    for s in lst:
        print(f"    {s['code']} {s['name']} boards={_safe_int(s.get('lian_ban_count'))}")

    # Fetch sector members
    members = _fetch_sector_members(ind)
    print(f"  -> Members via AKShare: {len(members)}")
    if members:
        # Filter: not in ZT, not ST, cap in range
        zt_codes = {s["code"] for s in stocks}
        candidates = [
            m for m in members
            if m["code"] not in zt_codes
            and "ST" not in m.get("name", "").upper()
            and -2.0 <= (m.get("changePct") or 0) <= 7.0
        ]
        print(f"  -> Candidates after filter: {len(candidates)}")
        for c in candidates[:5]:
            cap_yi = (c.get("marketCap") or 0) / 1e8
            print(f"    {c['code']} {c['name']} chg={c.get('changePct', 0):.1f}% cap={cap_yi:.0f}亿")
