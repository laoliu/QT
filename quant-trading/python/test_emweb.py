#!/usr/bin/env python3
"""Explore emweb.securities.eastmoney.com for float shares."""
import requests, json, os

for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

symbol = "300342"
s = requests.Session()
s.trust_env = False

# CompanySurvey
r = s.get("https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax",
          params={"code": f"SZ{symbol}"}, timeout=10,
          headers={"User-Agent": "Mozilla/5.0"})
data = r.json()

# jbzl = 基本资料 (basic info)
jbzl = data.get("jbzl", [])
if jbzl:
    print("=== jbzl (基本资料) ===")
    for k, v in jbzl[0].items():
        print(f"  {k}: {v}")

# Check other keys
for key in data.keys():
    val = data[key]
    if isinstance(val, list) and val:
        sample = val[0]
        if isinstance(sample, dict):
            # Check for share-related fields
            for sk, sv in sample.items():
                if any(word in str(sk).lower() for word in ["share", "stock", "ltg", "zg", "gb"]):
                    print(f"\n{key}.{sk}: {sv}")
