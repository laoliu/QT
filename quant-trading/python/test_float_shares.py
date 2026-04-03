#!/usr/bin/env python3
"""Test different ways to get float shares."""
import requests, json, os

# Clear proxy env
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

symbol = "300342"
secid = f"0.{symbol}"

# 1) Direct push2 (no proxy)
print("=== push2.eastmoney.com direct ===")
try:
    s = requests.Session()
    s.trust_env = False
    r = s.get("https://push2.eastmoney.com/api/qt/stock/get",
              params={"fltt": "2", "invt": "2", "secid": secid, "fields": "f116,f117"},
              timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:500]}")
except Exception as e:
    print(f"Error: {e}")

# 2) Try emweb endpoint
print("\n=== emweb.securities.eastmoney.com ===")
try:
    s = requests.Session()
    s.trust_env = False
    r = s.get(f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax",
              params={"code": f"SZ{symbol}"}, timeout=10,
              headers={"User-Agent": "Mozilla/5.0"})
    data = r.json()
    # Look for float shares in company survey
    shares_info = data.get("jbzl", [])
    print(f"Keys: {list(data.keys())[:10]}")
    if shares_info:
        for k, v in shares_info[0].items() if isinstance(shares_info, list) and shares_info else []:
            if "流通" in str(k) or "ltgb" in str(k).lower():
                print(f"  {k}: {v}")
except Exception as e:
    print(f"Error: {e}")

# 3) Try datacenter-web API
print("\n=== datacenter-web.eastmoney.com ===")
try:
    s = requests.Session()
    s.trust_env = False
    r = s.get("https://datacenter-web.eastmoney.com/api/data/v1/get",
              params={"reportName": "RPT_F10_FN_MAINFINADATA",
                      "columns": "SECURITY_CODE,TOTAL_SHARES,FREE_SHARES_A",
                      "filter": f'(SECURITY_CODE="{symbol}")',
                      "pageNumber": "1", "pageSize": "1",
                      "sortColumns": "REPORT_DATE", "sortTypes": "-1"},
              timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    print(f"Status: {r.status_code}")
    rj = r.json()
    if rj.get("result") and rj["result"].get("data"):
        print(f"Data: {rj['result']['data'][0]}")
    else:
        print(f"Response: {json.dumps(rj, ensure_ascii=False)[:500]}")
except Exception as e:
    print(f"Error: {e}")

# 4) Try finance.sina.com.cn F10 data
print("\n=== finance.sina.com.cn ===")
try:
    s = requests.Session()
    s.trust_env = False
    r = s.get(f"https://money.finance.sina.com.cn/corp/go.php/vCI_CorpInfo/stockid/{symbol}.phtml",
              timeout=10, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
    # Look for 流通股 in the HTML
    import re
    text = r.text
    match = re.search(r'流通股.*?([0-9,.]+)\s*(?:万|亿)?(?:股)?', text)
    if match:
        print(f"Found: {match.group()}")
    else:
        print("No float shares found in page")
        # Check for 流通 in text
        matches = [m.start() for m in re.finditer(r'流通', text)]
        for pos in matches[:3]:
            print(f"  Context: ...{text[pos:pos+80]}...")
except Exception as e:
    print(f"Error: {e}")
