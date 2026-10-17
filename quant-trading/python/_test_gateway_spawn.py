#!/usr/bin/env python3
"""Simulate exact gateway spawn: python -m scripts.stock_recommend --json
with QUANT_CONFIG via stdin, to diagnose where it hangs."""
import subprocess, json, time, os

env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"
env["WINDOWS_XTDATA_URL"] = "http://127.0.0.1:18888"
env["QUANT_CONFIG"] = json.dumps({
    "capital": 500000,
    "strategy": "momentum",
    "risk_tolerance": "medium",
    "symbols": ["002051", "600875"],
    "start_date": "2025-06-01",
    "rebalance_days": 5,
    "exclude_star": True,
})

t0 = time.time()
proc = subprocess.Popen(
    ["python", "-m", "scripts.stock_recommend", "--json"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=env,
    cwd=r"D:\work\QT\quant-trading\python",
)

# Send the same JSON via stdin (as gateway does)
stdin_data = json.dumps({
    "capital": 500000,
    "strategy": "momentum",
    "risk_tolerance": "medium",
    "symbols": ["002051", "600875"],
    "start_date": "2025-06-01",
    "rebalance_days": 5,
    "exclude_star": True,
})
proc.stdin.write(stdin_data.encode())
proc.stdin.close()

# Wait with timeout
try:
    stdout, stderr = proc.communicate(timeout=60)
    elapsed = time.time() - t0
    print(f"=== Completed in {elapsed:.1f}s, exit code: {proc.returncode} ===")
    print(f"=== STDERR ({len(stderr)} bytes) ===")
    print(stderr.decode("utf-8", errors="replace")[-2000:])
    print(f"=== STDOUT ({len(stdout)} bytes) ===")
    print(stdout.decode("utf-8", errors="replace")[:500])
except subprocess.TimeoutExpired:
    elapsed = time.time() - t0
    proc.kill()
    stdout, stderr = proc.communicate()
    print(f"=== TIMED OUT after {elapsed:.1f}s ===")
    print(f"=== STDERR ({len(stderr)} bytes) ===")
    print(stderr.decode("utf-8", errors="replace")[-2000:])
    print(f"=== STDOUT ({len(stdout)} bytes) ===")
    print(stdout.decode("utf-8", errors="replace")[:500])
