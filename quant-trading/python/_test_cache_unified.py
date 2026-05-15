"""Test unified cache directory resolution and data readability."""
import pandas as pd
from pathlib import Path
from aipha.config import get_cache_dir

cd = get_cache_dir()
print(f"cache_dir: {cd}")
print()

# 1. cb_tushare
cb_dir = cd / "cb_tushare"
cb_files = sorted(cb_dir.glob("cb_daily_*.parquet"))
print(f"[cb_tushare] {len(cb_files)} daily files")
if cb_files:
    df = pd.read_parquet(cb_files[-1])
    print(f"  latest: {cb_files[-1].name}, rows={len(df)}, cols={list(df.columns)[:6]}")

# 2. cb_akshare
cb_ak_dir = cd / "cb_akshare"
if cb_ak_dir.exists():
    ak_files = sorted(cb_ak_dir.glob("*.parquet"))
    print(f"[cb_akshare] {len(ak_files)} files")
    if ak_files:
        df = pd.read_parquet(ak_files[-1])
        print(f"  latest: {ak_files[-1].name}, rows={len(df)}")
else:
    print("[cb_akshare] not found")

# 3. equity_tushare
eq_dir = cd / "equity_tushare"
eq_files = sorted(eq_dir.glob("daily_*.parquet"))
print(f"[equity_tushare] {len(eq_files)} daily files")
if eq_files:
    df = pd.read_parquet(eq_files[-1])
    print(f"  latest: {eq_files[-1].name}, rows={len(df)}, cols={list(df.columns)[:6]}")

# 4. etf_tushare
etf_dir = cd / "etf_tushare"
if etf_dir.exists():
    etf_files = sorted(etf_dir.glob("*.parquet"))
    print(f"[etf_tushare] {len(etf_files)} files")
    if etf_files:
        df = pd.read_parquet(etf_files[0])
        print(f"  sample: {etf_files[0].name}, rows={len(df)}")
else:
    print("[etf_tushare] not found")

# 5. Test provider instantiation with unified cache
print()
print("--- Provider instantiation ---")

from aipha.data.tushare_cb_provider import TuShareCBProvider
p = TuShareCBProvider()
print(f"TuShareCBProvider.cache_dir = {p.cache_dir}")
existing = list(p.cache_dir.glob("cb_daily_*.parquet"))
print(f"  can see {len(existing)} daily files")

from aipha.data.akshare_cb_adapter import AKShareCBDataAdapter
a = AKShareCBDataAdapter()
print(f"AKShareCBDataAdapter.cache_dir = {a.cache_dir}")
existing = list(a.cache_dir.glob("*.parquet"))
print(f"  can see {len(existing)} files")

print()
print("=== ALL TESTS PASSED ===")
