"""Debug: show all FAILED/ERROR lines from pytest output."""
import subprocess, re, sys

ignores = [
    "test_adaptive_weight", "test_cb_enhanced_factors", "test_cb_pricing_bias",
    "test_equity_rotation_base", "test_index_enhance", "test_ml_predict",
    "test_regime_gate", "test_registry",
]
cmd = [sys.executable, "-m", "pytest", "tests/strategy/"]
for ig in ignores:
    cmd += [f"--ignore=tests/strategy/{ig}.py"]
cmd += ["--tb=line"]

result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
output = result.stdout + result.stderr

count = 0
for line in output.splitlines():
    if line.startswith("FAILED ") or line.startswith("ERROR "):
        count += 1
        if count <= 20:
            print(repr(line[:200]))
print(f"\n... total FAILED/ERROR lines: {count}")

# Also show some E lines
ecount = 0
for line in output.splitlines():
    if line.strip().startswith("E   "):
        ecount += 1
        if ecount <= 10:
            print(f"E-line: {repr(line[:150])}")
print(f"... total E lines: {ecount}")

# Show lines around "short test summary"
lines = output.splitlines()
for i, line in enumerate(lines):
    if "short test summary" in line:
        for j in range(i, min(i+30, len(lines))):
            print(f"[{j}] {lines[j][:200]}")
        break
