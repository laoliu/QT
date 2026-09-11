"""Diagnose all test failures using JUnit XML - fixed."""
import subprocess, sys, xml.etree.ElementTree as ET
from collections import Counter, defaultdict
import re

ignores = [
    "test_adaptive_weight", "test_cb_enhanced_factors", "test_cb_pricing_bias",
    "test_equity_rotation_base", "test_index_enhance", "test_ml_predict",
    "test_regime_gate", "test_registry",
]
xml_path = r"c:\temp\qt_test_results.xml"
cmd = [sys.executable, "-m", "pytest", "tests/strategy/"]
for ig in ignores:
    cmd += [f"--ignore=tests/strategy/{ig}.py"]
cmd += ["--tb=no", f"--junitxml={xml_path}"]

result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

tree = ET.parse(xml_path)
root = tree.getroot()

categories = defaultdict(list)

for tc in root.iter("testcase"):
    classname = tc.get("classname", "")
    name = tc.get("name", "")
    test_id = f"{classname}::{name}"
    
    failure = tc.find("failure")
    error = tc.find("error")
    
    # Use is not None checks instead of truthiness
    if failure is not None:
        elem = failure
    elif error is not None:
        elem = error
    else:
        continue
    
    msg = elem.get("message", "")
    
    if "got an unexpected keyword argument" in msg:
        m2 = re.search(r"(\w+)\.__init__\(\) got an unexpected keyword argument '(\w+)'", msg)
        if m2:
            categories[f"INIT_KWARG:{m2.group(1)}"].append((test_id, m2.group(2)))
        else:
            categories["INIT_KWARG:other"].append((test_id, msg[:100]))
    elif "'list' object has no attribute 'get'" in msg:
        categories["LIST_NOT_DICT"].append((test_id, msg[:100]))
    elif "has no attribute '_extra_filter'" in msg:
        categories["ATTR:_extra_filter"].append((test_id, msg[:100]))
    elif "has no attribute '_price_cache'" in msg:
        categories["ATTR:_price_cache"].append((test_id, msg[:100]))
    elif "has no attribute '_min_positions'" in msg:
        categories["ATTR:_min_positions"].append((test_id, msg[:100]))
    elif "has no attribute '_update_exposure'" in msg:
        categories["ATTR:_update_exposure"].append((test_id, msg[:100]))
    elif "has no attribute '_filter_candidates'" in msg:
        categories["ATTR:_filter_candidates"].append((test_id, msg[:100]))
    elif "has no attribute '_compute_bb_deviation'" in msg:
        categories["ATTR:_compute_bb_deviation"].append((test_id, msg[:100]))
    elif "has no attribute '_update_history'" in msg:
        categories["ATTR:_update_history"].append((test_id, msg[:100]))
    elif "delta_exposure" in msg:
        categories["DELTA_EXPOSURE"].append((test_id, msg[:100]))
    elif "ParamSpec" in msg:
        categories["PARAMSPEC"].append((test_id, msg[:100]))
    elif "has no attribute" in msg:
        m2 = re.search(r"'(\w+)' object has no attribute '(\w+)'", msg)
        if m2:
            categories[f"ATTR:{m2.group(1)}.{m2.group(2)}"].append((test_id, msg[:100]))
        else:
            categories["ATTR:other"].append((test_id, msg[:100]))
    elif "未注册" in msg:
        categories["REGISTRY"].append((test_id, msg[:100]))
    elif "in []" in msg:
        categories["REGISTRY:in_empty_list"].append((test_id, msg[:100]))
    elif "missing 1 required positional argument" in msg:
        categories["MISSING_POS_ARG"].append((test_id, msg[:100]))
    elif msg.startswith("assert"):
        categories["ASSERTION"].append((test_id, msg[:100]))
    elif "AssertionError" in msg:
        categories["ASSERTION"].append((test_id, msg[:100]))
    elif "TypeError" in msg:
        categories["TYPE_ERROR"].append((test_id, msg[:100]))
    else:
        categories["OTHER"].append((test_id, msg[:100]))

print("=" * 80)
total = sum(len(v) for v in categories.values())
print(f"TOTAL CATEGORIZED: {total}  (expected ~994)")
print("=" * 80)
for cat in sorted(categories, key=lambda c: -len(categories[c])):
    tests = categories[cat]
    files = sorted(set(t.split("::")[0].split(".")[-1] for t, _ in tests))
    print(f"\n[{len(tests):4d}] {cat}")
    print(f"       Files({len(files)}): {', '.join(files[:8])}{'...' if len(files) > 8 else ''}")
    if "INIT_KWARG" in cat:
        kws = Counter(kw for _, kw in tests)
        print(f"       Keywords: {dict(kws.most_common(10))}")
    seen = set()
    for t, e in tests[:3]:
        k = e[:60]
        if k not in seen:
            print(f"       eg: {e[:120]}")
            seen.add(k)
