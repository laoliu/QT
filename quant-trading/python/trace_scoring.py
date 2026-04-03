#!/usr/bin/env python3
"""Trace scoring logic step by step for 300342."""
import json, sys
sys.stdin = open("/dev/null")
from engines.chip_accumulation import (
    _fetch_klines, _enrich_turnover_rate, _estimate_chip_distribution,
    _score_turnover, _score_volume_price
)
from engines.chip_distribution import score_chip_distribution
import akshare as ak

symbol = "300342"
errors = []
klines = _fetch_klines(ak, symbol, 40, errors)
_enrich_turnover_rate(ak, symbol, klines, errors)
klines = klines[-35:]  # same as engine: -(lookback+5)
current_price = klines[-1]["close"]

print(f"Total klines used: {len(klines)}, current price: {current_price}")

# 1) Turnover scoring trace
print("\n=== TURNOVER SCORING ===")
turnover_rates = [k.get("turnover_rate") for k in klines if k.get("turnover_rate") is not None]
avg_tr = sum(turnover_rates) / len(turnover_rates)
max_tr = max(turnover_rates)
recent_tr = sum(turnover_rates[-5:]) / min(5, len(turnover_rates[-5:]))
first_half = turnover_rates[:len(turnover_rates)//2]
second_half = turnover_rates[len(turnover_rates)//2:]
avg_first = sum(first_half) / len(first_half)
avg_second = sum(second_half) / len(second_half)
print(f"avg={avg_tr:.2f}%, max={max_tr:.2f}%, recent5={recent_tr:.2f}%")
print(f"first_half_avg={avg_first:.2f}%, second_half_avg={avg_second:.2f}%")
print(f"second/first ratio={avg_second/avg_first:.3f} (threshold: <0.7 for +6, <0.85 for +3)")
print(f"Thresholds: <2%=+8, <5%=+5, 5-15%=0 (dead zone!), >15%=-3")
details = []
score = _score_turnover(klines, details)
print(f"Score: {score}/20, Details: {details}")

# 2) Chip scoring trace
print("\n=== CHIP SCORING ===")
cyq = _estimate_chip_distribution(klines, current_price)
print(f"Estimated CYQ: {json.dumps(cyq, ensure_ascii=False)}")
if cyq:
    chip_score, chip_reasons, evidence = score_chip_distribution(cyq, current_price)
    normalized = (chip_score + 10) / 22 * 20
    normalized = max(0, min(20, normalized))
    print(f"Raw chip_score={chip_score}, reasons={chip_reasons}, evidence={evidence}")
    print(f"Normalized: ({chip_score}+10)/22*20 = {normalized:.1f}/20")

# 3) Volume-price trace
print("\n=== VOLUME-PRICE SCORING ===")
up_vols, down_vols = [], []
for i in range(1, len(klines)):
    vol = klines[i].get("volume", 0) or 0
    change = klines[i]["close"] - klines[i-1]["close"]
    if change > 0: up_vols.append(vol)
    elif change < 0: down_vols.append(vol)
avg_up = sum(up_vols)/len(up_vols) if up_vols else 0
avg_down = sum(down_vols)/len(down_vols) if down_vols else 0
ratio = avg_up/avg_down if avg_down > 0 else 0
half = len(klines)//2
first_vol = sum(k.get("volume",0) or 0 for k in klines[:half]) / half
second_vol = sum(k.get("volume",0) or 0 for k in klines[half:]) / (len(klines)-half)
shrink = second_vol / first_vol if first_vol > 0 else 1
recent_highs = [k["high"] for k in klines[-10:]]
recent_lows = [k["low"] for k in klines[-10:]]
price_range = (max(recent_highs) - min(recent_lows)) / max(recent_highs) if max(recent_highs) > 0 else 0
print(f"Up/Down vol ratio: {ratio:.2f}x (need >=1.2 for score)")
print(f"Volume shrink ratio: {shrink:.2f} (need <0.6 for +6, <0.8 for +3)")
print(f"10-day price range: {price_range:.1%} (need <5% for +4, <10% for +2)")
details2 = []
score2 = _score_volume_price(klines, details2)
print(f"Score: {score2}/20, Details: {details2}")
