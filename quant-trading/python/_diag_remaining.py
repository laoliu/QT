"""Re-diagnose after fixes."""
import xml.etree.ElementTree as ET, re
from collections import Counter, defaultdict

tree = ET.parse(r"c:\temp\qt_test_results3.xml")
root = tree.getroot()

categories = defaultdict(list)
for tc in root.iter("testcase"):
    cn = tc.get("classname", ""); nm = tc.get("name", ""); tid = f"{cn}::{nm}"
    f = tc.find("failure"); e = tc.find("error")
    elem = f if f is not None else e if e is not None else None
    if elem is None: continue
    msg = elem.get("message", "")
    
    if "got an unexpected keyword argument" in msg:
        m2 = re.search(r"(\w+)(?:\.__init__)?\(\) got an unexpected keyword argument '(\w+)'", msg)
        if m2: categories[f"INIT_KWARG:{m2.group(1)}"].append((tid, m2.group(2)))
        else: categories["INIT_KWARG:other"].append((tid, msg[:100]))
    elif "'list' object has no attribute 'get'" in msg:
        categories["LIST_NOT_DICT"].append((tid, msg[:100]))
    elif "has no attribute" in msg:
        m2 = re.search(r"'(\w+)' object has no attribute '(\w+)'", msg)
        if m2: categories[f"ATTR:{m2.group(1)}.{m2.group(2)}"].append((tid, msg[:100]))
        else:
            m3 = re.search(r"type object '(\w+)' has no attribute '(\w+)'", msg)
            if m3: categories[f"CLASSATTR:{m3.group(1)}.{m3.group(2)}"].append((tid, msg[:100]))
            else: categories["ATTR:other"].append((tid, msg[:100]))
    elif "未注册" in msg: categories["REGISTRY"].append((tid, msg[:100]))
    elif "in []" in msg and "assert" in msg: categories["REGISTRY:in_empty"].append((tid, msg[:100]))
    elif "missing 1 required positional argument" in msg: categories["MISSING_POS_ARG"].append((tid, msg[:100]))
    elif "ParamSpec" in msg: categories["PARAMSPEC"].append((tid, msg[:100]))
    elif msg.startswith("assert"): categories["ASSERTION"].append((tid, msg[:100]))
    elif "AssertionError" in msg: categories["ASSERTION"].append((tid, msg[:100]))
    elif "TypeError" in msg: categories["TYPE_ERROR"].append((tid, msg[:100]))
    else: categories["OTHER"].append((tid, msg[:100]))

total = sum(len(v) for v in categories.values())
print(f"TOTAL: {total}")
for cat in sorted(categories, key=lambda c: -len(categories[c])):
    tests = categories[cat]
    files = sorted(set(t.split("::")[1] if "::" in t else t for t, _ in tests))
    print(f"\n[{len(tests):4d}] {cat}")
    if "INIT_KWARG" in cat:
        kws = Counter(kw for _, kw in tests)
        print(f"       Keywords: {dict(kws.most_common(8))}")
    for t, e in tests[:2]:
        print(f"       eg: {e[:120]}")
