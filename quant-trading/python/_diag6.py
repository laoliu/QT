"""Diagnose all test failures - parse ERROR/FAILURES sections properly."""
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
cmd += ["--tb=line"]

result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
output = result.stdout + result.stderr
lines = output.splitlines()

categories = defaultdict(list)

# Strategy: Look for "E   " lines followed by "filepath:line: ErrorType: msg" or "filepath:line: msg"
# The test name is in the ___ header above the E line
i = 0
while i < len(lines):
    line = lines[i]
    
    # Match header: "_ ERROR at setup of TestClass.test_method _" or "_ TestClass.test_method _"
    hdr_m = re.match(r'^_+ (?:ERROR at setup of )?(.+?)\s*_+$', line)
    if hdr_m:
        test_name = hdr_m.group(1).strip()
        # Collect E lines until next header or file path reference
        i += 1
        err = None
        while i < len(lines):
            el = lines[i]
            if el.strip().startswith("E   "):
                err = el.strip()[4:]
                break
            if re.match(r'^_+\s', el):
                break
            i += 1
        
        if err:
            # Categorize
            if "got an unexpected keyword argument" in err:
                m2 = re.search(r"(\w+)\.__init__\(\) got an unexpected keyword argument '(\w+)'", err)
                if m2:
                    categories[f"INIT_KWARG:{m2.group(1)}"].append((test_name, m2.group(2)))
                else:
                    categories["INIT_KWARG:other"].append((test_name, err))
            elif "'list' object has no attribute 'get'" in err:
                categories["LIST_NOT_DICT"].append((test_name, err))
            elif "has no attribute '_extra_filter'" in err:
                categories["ATTR:_extra_filter"].append((test_name, err))
            elif "has no attribute '_price_cache'" in err:
                categories["ATTR:_price_cache"].append((test_name, err))
            elif "has no attribute '_min_positions'" in err:
                categories["ATTR:_min_positions"].append((test_name, err))
            elif "has no attribute '_update_exposure'" in err:
                categories["ATTR:_update_exposure"].append((test_name, err))
            elif "has no attribute '_filter_candidates'" in err:
                categories["ATTR:_filter_candidates"].append((test_name, err))
            elif "has no attribute '_compute_bb_deviation'" in err:
                categories["ATTR:_compute_bb_deviation"].append((test_name, err))
            elif "delta_exposure" in err:
                categories["DELTA_EXPOSURE"].append((test_name, err))
            elif "ParamSpec" in err:
                categories["PARAMSPEC"].append((test_name, err))
            elif "has no attribute" in err:
                m2 = re.search(r"'(\w+)' object has no attribute '(\w+)'", err)
                if m2:
                    categories[f"ATTR:{m2.group(1)}.{m2.group(2)}"].append((test_name, err))
                else:
                    categories["ATTR:other"].append((test_name, err))
            elif "未注册" in err or "assert" in err and "in []" in err:
                categories["REGISTRY"].append((test_name, err))
            elif "missing 1 required positional argument" in err:
                categories["MISSING_POS_ARG"].append((test_name, err))
            elif err.startswith("assert"):
                categories["ASSERTION"].append((test_name, err))
            elif "AssertionError" in err or "AssertionError" in err:
                categories["ASSERTION"].append((test_name, err))
            elif "TypeError" in err:
                categories["TYPE_ERROR"].append((test_name, err))
            else:
                categories["OTHER"].append((test_name, err))
    i += 1

print("=" * 80)
total = sum(len(v) for v in categories.values())
print(f"TOTAL CATEGORIZED: {total}")
print("=" * 80)
for cat in sorted(categories, key=lambda c: -len(categories[c])):
    tests = categories[cat]
    print(f"\n[{len(tests):4d}] {cat}")
    if "INIT_KWARG" in cat:
        kws = Counter(kw for _, kw in tests)
        print(f"       Keywords: {dict(kws.most_common(10))}")
    # Show some examples
    seen = set()
    for t, e in tests[:5]:
        k = e[:60]
        if k not in seen:
            print(f"       eg: {e[:120]}")
            seen.add(k)
