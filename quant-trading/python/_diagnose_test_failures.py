"""Diagnose all test failures and categorize them by root cause."""
import subprocess, re, sys
from collections import Counter, defaultdict

ignores = [
    "test_adaptive_weight","test_cb_enhanced_factors","test_cb_pricing_bias",
    "test_equity_rotation_base","test_index_enhance","test_ml_predict",
    "test_regime_gate","test_registry",
]
cmd = [sys.executable, "-m", "pytest", "tests/strategy/"]
for ig in ignores:
    cmd += [f"--ignore=tests/strategy/{ig}.py"]
cmd += ["-q", "--tb=line", "--no-header"]

result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
output = result.stdout + result.stderr

# Parse failures/errors
categories = defaultdict(list)
for line in output.splitlines():
    # Match: FAILED test_file.py::Class::method - E message
    # or: ERROR test_file.py::Class::method
    m = re.match(r"^(FAILED|ERROR) tests/strategy/(\S+?)(?:\s+-\s+(.*))?$", line)
    if not m:
        continue
    test_id = m.group(2)
    err_inline = m.group(3) or ""
    # Also check next-line style
    if not err_inline:
        continue
    err = err_inline
        # Categorize
        if "got an unexpected keyword argument" in err:
            m2 = re.search(r"(\w+)\.__init__\(\) got an unexpected keyword argument '(\w+)'", err)
            if m2:
                categories[f"INIT_KWARG:{m2.group(1)}"].append((current_test, m2.group(2)))
            else:
                categories["INIT_KWARG:other"].append((current_test, err))
        elif "has no attribute '_extra_filter'" in err:
            categories["MISSING:_extra_filter"].append((current_test, err))
        elif "has no attribute '_price_cache'" in err:
            categories["MISSING:_price_cache"].append((current_test, err))
        elif "has no attribute '_min_positions'" in err:
            categories["MISSING:_min_positions"].append((current_test, err))
        elif "has no attribute '_compute_bb_deviation'" in err:
            categories["MISSING:_compute_bb_deviation"].append((current_test, err))
        elif "'list' object has no attribute 'get'" in err:
            categories["RETURN_TYPE:list_not_dict"].append((current_test, err))
        elif "days_since_listed" in err:
            categories["MISSING:days_since_listed"].append((current_test, err))
        elif "delta_exposure" in err:
            categories["MISSING:delta_exposure"].append((current_test, err))
        elif "'Bar' object has no attribute" in err:
            categories["MISSING:Bar_attr"].append((current_test, err))
        elif "has no attribute" in err:
            m2 = re.search(r"'(\w+)' object has no attribute '(\w+)'", err)
            if m2:
                categories[f"ATTR_MISSING:{m2.group(1)}.{m2.group(2)}"].append((current_test, err))
            else:
                categories["ATTR_MISSING:other"].append((current_test, err))
        elif "KeyError" in err and "未注册" in err:
            categories["REGISTRY:not_registered"].append((current_test, err))
        elif "missing 1 required positional argument" in err:
            categories["INIT_MISSING_ARG"].append((current_test, err))
        elif "assert" in err.lower() or "Assert" in err:
            categories["ASSERTION"].append((current_test, err))
        elif "ImportError" in err:
            categories["IMPORT_ERROR"].append((current_test, err))
        elif "TypeError" in err:
            categories["TYPE_ERROR:other"].append((current_test, err))
        else:
            categories["OTHER"].append((test_id, err))

print("=" * 80)
print(f"TOTAL CATEGORIES: {len(categories)}")
print("=" * 80)
for cat in sorted(categories, key=lambda c: -len(categories[c])):
    tests = categories[cat]
    files = set(t.split("::")[0] for t, _ in tests)
    print(f"\n[{len(tests):4d}] {cat}")
    print(f"       Files: {', '.join(sorted(files)[:5])}{'...' if len(files)>5 else ''}")
    if "INIT_KWARG" in cat:
        kws = Counter(kw for _, kw in tests)
        print(f"       Keywords: {dict(kws.most_common(5))}")
    elif len(tests) <= 3:
        for t, e in tests:
            print(f"       {t}: {e[:100]}")
