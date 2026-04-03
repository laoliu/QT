#!/usr/bin/env python3
"""Explore emweb data structure."""
import requests, json, os

for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

symbol = "300342"
s = requests.Session()
s.trust_env = False

r = s.get("https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax",
          params={"code": f"SZ{symbol}"}, timeout=10,
          headers={"User-Agent": "Mozilla/5.0"})
data = r.json()

# Print full structure overview
for key in data.keys():
    val = data[key]
    if isinstance(val, dict):
        print(f"{key} (dict): {list(val.keys())[:10]}")
        # Check for share-related fields
        for sk, sv in val.items():
            sl = str(sk).lower()
            if any(w in sl for w in ["股", "share", "ltg", "zgb", "总股", "流通", "capital"]):
                print(f"  >>> {sk}: {sv}")
    elif isinstance(val, list):
        print(f"{key} (list len={len(val)})")
        if val and isinstance(val[0], dict):
            print(f"  sample keys: {list(val[0].keys())[:10]}")
    elif isinstance(val, str):
        print(f"{key}: {val[:100]}")
    else:
        print(f"{key}: {val}")
