#!/usr/bin/env python3
"""Analyze 300342 data to validate scoring reasonableness."""
import json, sys
sys.stdin = open("/dev/null")
from engines.chip_accumulation import (
    _fetch_klines, _enrich_turnover_rate, _estimate_chip_distribution
)
import akshare as ak

symbol = "300342"
errors = []
klines = _fetch_klines(ak, symbol, 40, errors)
_enrich_turnover_rate(ak, symbol, klines, errors)

print(f"=== K-line Data (last 30 days) for {symbol} ===")
header = f"{'Date':>12}  {'Open':>8}  {'High':>8}  {'Low':>8}  {'Close':>8}  {'Volume':>12}  {'Turnover':>10}"
print(header)
for k in klines[-30:]:
    vol_str = f"{k['volume']:,.0f}" if k['volume'] else "N/A"
    tr = f"{k['turnover_rate']:.2f}%" if k.get('turnover_rate') is not None else "N/A"
    print(f"{k['date']:>12}  {k['open']:>8.2f}  {k['high']:>8.2f}  {k['low']:>8.2f}  {k['close']:>8.2f}  {vol_str:>12}  {tr:>10}")

prices = [k['close'] for k in klines[-30:]]
turnover_rates = [k['turnover_rate'] for k in klines[-30:] if k.get('turnover_rate')]

print(f"\n=== Summary ===")
print(f"Price range: {min(prices):.2f} ~ {max(prices):.2f}")
pct_change = (max(prices) - min(prices)) / min(prices) * 100
print(f"30-day change: {pct_change:.1f}%")
print(f"Current: {prices[-1]:.2f}")
if turnover_rates:
    avg_tr = sum(turnover_rates) / len(turnover_rates)
    print(f"Turnover avg: {avg_tr:.2f}%, max: {max(turnover_rates):.2f}%, min: {min(turnover_rates):.2f}%")

    # Check volume trend
    first_half = turnover_rates[:len(turnover_rates)//2]
    second_half = turnover_rates[len(turnover_rates)//2:]
    avg_first = sum(first_half) / len(first_half)
    avg_second = sum(second_half) / len(second_half)
    print(f"Turnover first half avg: {avg_first:.2f}%, second half avg: {avg_second:.2f}%")

# Volume analysis
vols = [k['volume'] for k in klines[-30:] if k['volume']]
up_vols = []
down_vols = []
for i in range(1, len(klines[-30:])):
    vol = klines[-30:][i]['volume'] or 0
    change = klines[-30:][i]['close'] - klines[-30:][i-1]['close']
    if change > 0:
        up_vols.append(vol)
    elif change < 0:
        down_vols.append(vol)

if up_vols and down_vols:
    ratio = (sum(up_vols)/len(up_vols)) / (sum(down_vols)/len(down_vols))
    print(f"\nUp-day avg vol / Down-day avg vol: {ratio:.2f}x")

# CYQ
cyq = _estimate_chip_distribution(klines[-60:], prices[-1])
if cyq:
    print(f"\n=== Estimated CYQ ===")
    for k, v in cyq.items():
        print(f"  {k}: {v}")
