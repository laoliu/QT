"""Identify exact missing Config fields by analyzing test fixtures."""
import subprocess, sys, xml.etree.ElementTree as ET, re
from collections import defaultdict

ignores = [
    "test_adaptive_weight", "test_cb_enhanced_factors", "test_cb_pricing_bias",
    "test_equity_rotation_base", "test_index_enhance", "test_ml_predict",
    "test_regime_gate", "test_registry",
]
xml_path = r"c:\temp\qt_test_results2.xml"
cmd = [sys.executable, "-m", "pytest", "tests/strategy/"]
for ig in ignores:
    cmd += [f"--ignore=tests/strategy/{ig}.py"]
cmd += ["--tb=no", f"--junitxml={xml_path}"]
subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

tree = ET.parse(xml_path)
root = tree.getroot()

# Collect INIT_KWARG errors grouped by Config class
config_missing = defaultdict(set)  # ConfigClass -> set of missing fields
strategy_missing = defaultdict(set)  # StrategyClass -> set of missing fields
other_missing = defaultdict(set)  # class -> set of missing fields

for tc in root.iter("testcase"):
    failure = tc.find("failure")
    error = tc.find("error")
    elem = failure if failure is not None else error if error is not None else None
    if elem is None:
        continue
    msg = elem.get("message", "")
    
    m = re.search(r"(\w+)\.__init__\(\) got an unexpected keyword argument '(\w+)'", msg)
    if m:
        cls, kwarg = m.group(1), m.group(2)
        if cls.endswith("Config"):
            config_missing[cls].add(kwarg)
        elif cls.endswith("Strategy"):
            strategy_missing[cls].add(kwarg)
        else:
            other_missing[cls].add(kwarg)
    
    # Also check for function kwarg errors
    m2 = re.search(r"(\w+)\(\) got an unexpected keyword argument '(\w+)'", msg)
    if m2 and not m:
        other_missing[m2.group(1)].add(m2.group(2))

print("=== CONFIG CLASSES MISSING FIELDS ===")
for cls, fields in sorted(config_missing.items()):
    print(f"  {cls}: {sorted(fields)}")

print("\n=== STRATEGY CLASSES MISSING FIELDS ===")
for cls, fields in sorted(strategy_missing.items()):
    print(f"  {cls}: {sorted(fields)}")

print("\n=== OTHER MISSING FIELDS ===")
for cls, fields in sorted(other_missing.items()):
    print(f"  {cls}: {sorted(fields)}")
