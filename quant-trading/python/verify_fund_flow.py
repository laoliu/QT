#!/usr/bin/env python3
"""Verify fund flow data for 300342."""
import sys
sys.stdin = open("/dev/null")

# Import engine to get proxy setup
import engines.chip_accumulation as _  # noqa

import akshare as ak

df = ak.stock_individual_fund_flow(stock="300342", market="sz")
print(f"Columns: {list(df.columns)}")
print(f"Total rows: {len(df)}")
print()

# Show last 30 rows
recent = df.tail(30)
for _, row in recent.iterrows():
    date = str(row.get("日期", ""))[:10]
    main_net = row.get("主力净流入-净额", 0)
    super_large = row.get("超大单净流入-净额", 0)
    large = row.get("大单净流入-净额", 0)
    # Display in 万元
    print(f"{date}  主力: {main_net/10000:>10.1f}万  超大单: {super_large/10000:>10.1f}万  大单: {large/10000:>10.1f}万")
