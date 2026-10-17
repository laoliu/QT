#!/usr/bin/env python3
"""Probe xtdata sector naming conventions via the bridge."""
import json
import os
import urllib.request

def _read_bridge_url():
    try:
        with open(os.path.expanduser("~/.quant-trading/quant-trading.json")) as f:
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
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

# Test a wide variety of sector name formats
test_names = [
    # Standard industry names (同花顺行业)
    "电力设备", "电力", "半导体", "银行", "计算机", "医药生物",
    # Concept names
    "人工智能", "芯片概念", "光伏概念", "锂电池", "新能源汽车",
    # Index names
    "沪深300", "上证50", "中证500", "中证1000", "创业板指",
    # With prefix
    "同花顺行业.电力设备", "thshy.电力设备",
    # Region
    "北京", "上海", "深圳", "广东",
    # Alternative industry names
    "电气设备", "电子", "通信", "传媒", "房地产", "食品饮料",
    "汽车", "机械设备", "化工", "有色金属", "钢铁", "建筑材料",
    "国防军工", "非银金融", "农林牧渔", "ú̿", "石油石化",
    "交通运输", "纺织服饰", "商贸零售", "社会服务", "环保",
    "公用事业", "建筑装饰", "美容护理", "综合",
]

print(f"Bridge: {BRIDGE}\n")
print(f"Testing {len(test_names)} sector names...\n")

hits = []
misses = []

for name in test_names:
    try:
        data = post("/sector_members", {"sector": name})
        count = data.get("count", 0)
        if count > 0:
            preview = data.get("data", [])[:3]
            hits.append((name, count, preview))
            print(f"  ✅ {name:12s}  count={count:4d}  preview={preview}")
        else:
            misses.append(name)
    except Exception as e:
        misses.append(name)
        print(f"  ❌ {name:12s}  ERROR: {e}")

print(f"\n=== Summary ===")
print(f"  Hits: {len(hits)} / {len(test_names)}")
print(f"  Misses: {misses}")
