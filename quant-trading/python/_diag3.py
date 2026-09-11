"""Diagnose all test failures by parsing --tb=short output."""
import subprocess, re, sys
from collections import Counter, defaultdict

ignores = [
    "test_adaptive_weight", "test_cb_enhanced_factors", "test_cb_pricing_bias",
    "test_equity_rotation_base", "test_index_enhance", "test_ml_predict",
    "test_regime_gate", "test_registry",
]
cmd = [sys.executable, "-m", "pytest", "tests/strategy/"]
for ig in ignores:
    cmd += [f"--ignore=tests/strategy/{ig}.py"]
cmd += ["-q", "--tb=short", "--no-header"]

result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
output = result.stdout + result.stderr

# Strategy: Find every "E   " error line and the test it belongs to
# In --tb=short, each test block starts with "_ ... test_name ... _"
# followed by lines, then "E   ..." lines
lines = output.splitlines()

categories = defaultdict(list)
current_test = None
error_lines = []

# Parse section by section
for line in lines:
    # Detect test header
    m = re.match(r"^_{2,}\s*(ERROR at setup of |FAILED )?(.+?)\s*_{2,}$", line)
    if m:
        current_test = m.group(2).strip()
        error_lines = []
        continue
    
    if current_test and line.strip().startswith("E   "):
        err = line.strip()[4:]
        error_lines.append(err)
    
    # Detect end of test block (file path line)
    if current_test and error_lines and re.match(r"^D:\\.*\.py:\d+:", line):
        err = error_lines[0]
        
        if "got an unexpected keyword argument" in err:
            m2 = re.search(r"(\w+)\.__init__\(\) got an unexpected keyword argument '(\w+)'", err)
            if m2:
                categories[f"INIT_KWARG:{m2.group(1)}"].append((current_test, m2.group(2)))
            else:
                categories["INIT_KWARG:other"].append((current_test, err))
        elif "'list' object has no attribute 'get'" in err:
            categories["LIST_NOT_DICT"].append((current_test, err))
        elif "'list' object has no attribute" in err:
            categories["LIST_ATTR"].append((current_test, err))
        elif "has no attribute '_extra_filter'" in err:
            categories["ATTR:_extra_filter"].append((current_test, err))
        elif "has no attribute '_price_cache'" in err:
            categories["ATTR:_price_cache"].append((current_test, err))
        elif "has no attribute '_min_positions'" in err:
            categories["ATTR:_min_positions"].append((current_test, err))
        elif "has no attribute '_update_exposure'" in err:
            categories["ATTR:_update_exposure"].append((current_test, err))
        elif "has no attribute '_filter_candidates'" in err:
            categories["ATTR:_filter_candidates"].append((current_test, err))
        elif "has no attribute '_compute_bb_deviation'" in err:
            categories["ATTR:_compute_bb_deviation"].append((current_test, err))
        elif "delta_exposure" in err:
            categories["DELTA_EXPOSURE"].append((current_test, err))
        elif "ParamSpec" in err:
            categories["PARAMSPEC"].append((current_test, err))
        elif "has no attribute" in err:
            m2 = re.search(r"'(\w+)' object has no attribute '(\w+)'", err)
            if m2:
                categories[f"ATTR:{m2.group(1)}.{m2.group(2)}"].append((current_test, err))
            else:
                categories["ATTR:other"].append((current_test, err))
        elif "未注册" in err:
            categories["REGISTRY"].append((current_test, err))
        elif "missing 1 required positional argument" in err:
            categories["MISSING_POS_ARG"].append((current_test, err))
        elif "assert" in err.lower():
            categories["ASSERTION"].append((current_test, err))
        elif "TypeError" in err:
            categories["TYPE_ERROR"].append((current_test, err))
        else:
            categories["OTHER"].append((current_test, err))
        
        current_test = None
        error_lines = []

print("=" * 80)
total = sum(len(v) for v in categories.values())
print(f"TOTAL CATEGORIZED: {total}  (of 861+133)")
print("=" * 80)
for cat in sorted(categories, key=lambda c: -len(categories[c])):
    tests = categories[cat]
    print(f"\n[{len(tests):4d}] {cat}")
    if "INIT_KWARG" in cat:
        kws = Counter(kw for _, kw in tests)
        print(f"       Keywords: {dict(kws.most_common(10))}")
    # Show a few samples
    for t, e in tests[:3]:
        print(f"       {t[:60]}: {e[:80]}")
