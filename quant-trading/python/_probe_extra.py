#!/usr/bin/env python3
"""Probe more sector name patterns."""
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

extras = [
    "A股", "SH", "SZ", "BJ", "上证A股", "深证A股", "北交所",
    "ST板块", "科创板", "创业板", "主板",
    "半导体及元件", "电池", "光伏设备", "电网设备", "风电设备",
    "银行I", "银行II", "֤ȯ", "保险", "多元金融",
    "白酒", "中药", "化学制药", "医疗器械", "医疗服务",
    "消费电子", "光学光电子", "计算机设备", "计算机应用", "IT服务",
    "通信设备", "通信服务",
    "电力", "燃气", "水务",
    #申万二级
    "半导体", "光伏设备", "储能设备", "锂电设备",
    # Try with 板块 suffix  
    "电力设备板块", "银行板块", "半导体板块",
]

print(f"Bridge: {BRIDGE}")
print(f"Testing {len(extras)} names...\n")

for name in extras:
    try:
        data = post("/sector_members", {"sector": name})
        count = data.get("count", 0)
        if count > 0:
            preview = data.get("data", [])[:3]
            print(f"  OK {name:14s}  count={count:4d}  {preview}")
    except Exception:
        pass

print("\nDone.")
