#!/usr/bin/env python3
"""Deep probe: ask bridge for raw sector enumeration via various xtdata APIs."""
import json
import os
import urllib.request

def _read_bridge_url():
    try:
        with open(os.path.expanduser("~/.openclaw/openclaw.json")) as f:
            cfg = json.load(f)
        return (cfg.get("plugins", {}).get("entries", {})
                .get("quant-trading", {}).get("config", {})
                .get("tradingAccount", {}).get("qmt", {})
                .get("windowsBridgeUrl", "http://192.168.3.30:58088"))
    except Exception:
        return "http://192.168.3.30:58088"

BRIDGE = _read_bridge_url().rstrip("/")

def post(path, body):
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BRIDGE}{path}", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())

print(f"Bridge: {BRIDGE}\n")

# 1. Try sector_list with many possible type keys
type_keys = [
    "industry", "concept", "region",
    "thshy", "thsgn", "thsdy",
    "行业板块", "概念板块", "地域板块",
    "证监会行业板块", "申万行业", "中信行业",
    "同花顺概念", "同花顺地域",
    "指数板块", "指数",
    "风格板块", "风格",
    # xtdata internal type strings
    "stock_sector", "concept_sector", "industry_sector",
    "hushen", "sh", "sz", "bj",
    "",
]

print("=== sector_list with various type keys ===")
for tk in type_keys:
    try:
        data = post("/sector_list", {"sector_type": tk})
        count = data.get("count", 0)
        if count > 0:
            preview = data.get("data", [])[:8]
            print(f"  ✅ type={tk!r:25s}  count={count:5d}  preview={preview}")
        else:
            print(f"     type={tk!r:25s}  count=0")
    except Exception as e:
        print(f"  ❌ type={tk!r:25s}  ERROR: {e}")

# 2. Try sector_members with some known-working names and find patterns
print("\n=== Exploring working sector name patterns ===")
# These worked: 医药生物, 沪深300, 上证50, 中证500, 中证1000, 创业板指, 食品饮料, 有色金属
# Let's try more申万一级行业 names (31个)
sw_l1 = [
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "汽车",
    "家用电器", "食品饮料", "纺织服饰", "轻工制造", "医药生物",
    "公用事业", "交通运输", "房地产", "商贸零售", "社会服务",
    "银行", "非银金融", "综合", "建筑材料", "建筑装饰", "电力设备",
    "国防军工", "计算机", "传媒", "通信", "煤炭", "石油石化",
    "环保", "美容护理", "机械设备",
]
print("\n申万一级行业 (31):")
for name in sw_l1:
    try:
        data = post("/sector_members", {"sector": name})
        count = data.get("count", 0)
        mark = "✅" if count > 0 else "  "
        print(f"  {mark} {name:8s}  count={count}")
    except Exception as e:
        print(f"  ❌ {name:8s}  ERROR: {e}")
