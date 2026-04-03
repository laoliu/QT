"""Verify enhanced sector_hotspot_screener features."""
import sys
sys.path.insert(0, ".")

from screeners.base import get_screener, list_screener_ids
from engines.lianban import _safe_int

# 1. Registry
ids = list_screener_ids()
assert "sector_hotspot" in ids, f"Missing! {ids}"
print(f"Registry OK: {len(ids)} screeners")

# 2. Instantiate + defaults
s = get_screener("sector_hotspot")
assert s.screener_id == "sector_hotspot"
assert s._DEFAULTS.get("use_concept_boards") is True
assert s._DEFAULTS.get("concept_min_change_pct") == 3.0
assert s._DEFAULTS.get("concept_max_boards") == 5
print("Defaults OK: concept board params present")

# 3. Exit hints (aggressive env)
sig1, fac1 = [], {}
s._add_exit_hints(sig1, fac1, 65)
assert fac1["exitMode"] == "\u79ef\u6781"  # 积极
assert "exitHints" in fac1
assert any("\u6b62\u635f" in x for x in sig1)
assert any("\u6b62\u76c8" in x for x in sig1)
assert any("\u6301\u4ed3" in x for x in sig1)
print(f"Exit hints (env=65): mode={fac1['exitMode']}, hints={len(fac1['exitHints'])}")

# 4. Exit hints (weak env)
sig2, fac2 = [], {}
s._add_exit_hints(sig2, fac2, 45)
assert fac2["exitMode"] == "\u4fdd\u5b88"  # 保守
print(f"Exit hints (env=45): mode={fac2['exitMode']}")

# 5. Sector leader info construction
hotspots_mock = {
    "AI": [
        {"code": "001", "name": "A\u80a1", "lian_ban_count": 3},
        {"code": "002", "name": "B\u80a1", "lian_ban_count": 2},
        {"code": "003", "name": "C\u80a1", "lian_ban_count": 1},
    ],
}
leaders = sorted(
    hotspots_mock["AI"],
    key=lambda x: _safe_int(x.get("lian_ban_count")),
    reverse=True,
)
leader_info = [
    {"code": l["code"], "name": l.get("name", ""), "boards": _safe_int(l.get("lian_ban_count"))}
    for l in leaders[:3]
]
assert leader_info[0]["boards"] == 3
assert leader_info[0]["name"] == "A\u80a1"
print(f"Leaders: {leader_info}")

# 6. All original scoring still works
params = s._DEFAULTS
heat = s._score_sector_heat("AI", 4, hotspots_mock, "999", params)
assert heat > 0
perf, _ = s._score_yesterday_perf(5.0, 8.0, params)
assert perf > 0
flow, _ = s._score_fund_flow({"mainNetIn": 5000, "mainNetInPct": 3.0}, params)
assert flow > 0
cap = s._score_market_cap(50e8, params)
assert cap > 0
tech, _ = s._score_technical([], params)
assert tech > 0
multi = s._check_multi_sector("001", hotspots_mock, "AI")
assert multi == []  # only 1 sector in mock
print("All scoring methods OK")

print("\n=== All enhanced tests passed ===")
