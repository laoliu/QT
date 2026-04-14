#!/usr/bin/env python3
"""Test if stock_zh_a_hist triggers mini-racer crash."""
import os
for k in list(os.environ):
    if 'proxy' in k.lower():
        del os.environ[k]
os.environ["no_proxy"] = "*"

import akshare as ak
print("akshare imported OK")
df = ak.stock_zh_a_hist(symbol="600519", period="daily", start_date="20260401", end_date="20260408", adjust="qfq")
print(f"stock_zh_a_hist: Got {len(df)} rows")

# Now test the problematic function
try:
    df2 = ak.stock_cyq_em(symbol="600519", adjust="")
    print(f"stock_cyq_em: Got {len(df2)} rows")
except Exception as e:
    print(f"stock_cyq_em failed: {e}")
