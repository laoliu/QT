#!/usr/bin/env python3
"""Quick test: verify volume unit from sina and turnover calculation."""
import requests
import sys

s = requests.Session()
s.trust_env = False
r = s.get(
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData",
    params={"symbol": "sz300342", "scale": "240", "ma": "no", "datalen": "5"},
    timeout=15,
    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"},
)
data = r.json()
print("Sina K-line last 3 days:")
for item in data[-3:]:
    print(f"  date={item['day']}, volume={item['volume']}, close={item['close']}")

import akshare as ak
df = ak.stock_individual_info_em(symbol="300342")
float_shares = float(df[df["item"] == "流通股"].iloc[0]["value"])
print(f"\nFloat shares: {float_shares:,.0f}")

last_vol = float(data[-1]["volume"])
print(f"Last day volume (raw): {last_vol:,.0f}")
print(f"Turnover (volume/float_shares*100): {last_vol / float_shares * 100:.4f}%")
print(f"Turnover (volume*100/float_shares*100): {last_vol * 100 / float_shares * 100:.4f}%")

# Also check what the akshare K-line typically reports for turnover on this stock
# to calibrate expected range
print("\nExpected: 300342 daily turnover is typically 1-10% for an active stock")
