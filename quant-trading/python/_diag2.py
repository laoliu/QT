"""Diagnose all test failures and categorize them by root cause."""
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
cmd += ["-q", "--tb=line", "--no-header"]

result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
output = result.stdout + result.stderr

categories = defaultdict(list)
for line in output.splitlines():
    m = re.match(r"^(FAILED|ERROR) tests/strategy/(\S+?)(?:\s+-\s+(.*))?$", line)
    if not m:
        continue
    test_id = m.group(2)
    err = m.group(3) or ""
    if not err:
        continue

    if "got an unexpected keyword argument" in err:
        m2 = re.search(r"(\w+)\.__init__\(\) got an unexpected keyword argument '(\w+)'", err)
        if m2:
            categories[f"INIT_KWARG:{m2.group(1)}"].append((test_id, m2.group(2)))
        else:
            categories["INIT_KWARG:other"].append((test_id, err))
    elif "'list' object has no attribute 'get'" in err:
        categories["RETURN_TYPE:list_not_dict"].append((test_id, err))
    elif "has no attribute" in err:
        m2 = re.search(r"'(\w+)' object has no attribute '(\w+)'", err)
        if m2:
            key = f"ATTR:{m2.group(1)}.{m2.group(2)}"
            categories[key].append((test_id, err))
        else:
            categories["ATTR:other"].append((test_id, err))
    elif "未注册" in err:
        categories["REGISTRY:not_registered"].append((test_id, err))
    elif "missing 1 required positional argument" in err:
        categories["MISSING_POS_ARG"].append((test_id, err))
    elif "assert" in err.lower():
        categories["ASSERTION"].append((test_id, err))
    elif "TypeError" in err:
        categories["TYPE_ERROR"].append((test_id, err))
    else:
        categories["OTHER"].append((test_id, err))

print("=" * 80)
total = sum(len(v) for v in categories.values())
print(f"TOTAL CATEGORIZED: {total}")
print("=" * 80)
for cat in sorted(categories, key=lambda c: -len(categories[c])):
    tests = categories[cat]
    files = sorted(set(t.split("::")[0] for t, _ in tests))
    print(f"\n[{len(tests):4d}] {cat}")
    print(f"       Files({len(files)}): {', '.join(files[:6])}{'...' if len(files) > 6 else ''}")
    if "INIT_KWARG" in cat:
        kws = Counter(kw for _, kw in tests)
        print(f"       Keywords: {dict(kws.most_common(8))}")
