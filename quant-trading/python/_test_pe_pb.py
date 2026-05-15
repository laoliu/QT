"""Test East Money for PE/PB data."""
import sys, os
sys.path.insert(0, ".")
# Clear proxy
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

import akshare as ak

# stock_individual_info_em - single stock detailed info
print("=== stock_individual_info_em (600519) ===")
try:
    df = ak.stock_individual_info_em(symbol="600519")
    for _, row in df.iterrows():
        print(f"  {row.iloc[0]}: {row.iloc[1]}")
except Exception as e:
    print(f"Failed: {e}")

# Try stock_a_lg_indicator for historical PE/PB
print("\n=== stock_a_lg_indicator (600519) ===")
for func_name in dir(ak):
    if "indicator" in func_name.lower() and "stock" in func_name.lower():
        pass  # Just enumerate
try:
    # Try several possible function names
    for fn in ["stock_a_lg_indicator", "stock_a_indicator_lg", "stock_a_ttm_lyr"]:
        if hasattr(ak, fn):
            print(f"Found: {fn}")
            df = getattr(ak, fn)(symbol="600519")
            print(f"Cols: {list(df.columns)}")
            print(df.tail(2).to_string())
            break
except Exception as e:
    print(f"Failed: {e}")

# Try Tencent for real-time
print("\n=== Tencent approach ===")
try:
    import urllib.request
    url = "http://qt.gtimg.cn/q=sh600519"
    resp = urllib.request.urlopen(url, timeout=5)
    line = resp.read().decode("gbk")
    parts = line.split("~")
    if len(parts) > 45:
        print(f"Price: {parts[3]}, PE(dynamic): {parts[39]}, PE(static): {parts[52] if len(parts) > 52 else 'N/A'}, PB: {parts[46] if len(parts) > 46 else 'N/A'}, Turnover%: {parts[38]}")
except Exception as e:
    print(f"Failed: {e}")
