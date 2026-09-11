"""Quick re-diagnosis of remaining failures."""
import xml.etree.ElementTree as ET, re
from collections import Counter, defaultdict

tree = ET.parse(r"c:\temp\qt_test_results4.xml")
root = tree.getroot()

# Broad categories
cat = defaultdict(int)
detail = defaultdict(list)

for tc in root.iter("testcase"):
    f = tc.find("failure"); e = tc.find("error")
    elem = f if f is not None else e if e is not None else None
    if elem is None: continue
    msg = elem.get("message", "")
    cn = tc.get("classname", "")
    
    if "got an unexpected keyword argument" in msg:
        m = re.search(r"(\w+)(?:\.\w+)?\(\) got an unexpected keyword argument '(\w+)'", msg)
        cat["INIT_KWARG"] += 1
        if m: detail["INIT_KWARG"].append(f"{m.group(1)}.{m.group(2)}")
    elif "'list' object has no attribute 'get'" in msg:
        cat["LIST_NOT_DICT"] += 1
        detail["LIST_NOT_DICT"].append(cn.split(".")[-1])
    elif "has no attribute" in msg:
        m = re.search(r"'(\w+)' object has no attribute '(\w+)'", msg)
        if m:
            k = f"{m.group(1)}.{m.group(2)}"
            cat[f"ATTR:{k}"] += 1
        else:
            m2 = re.search(r"type object '(\w+)' has no attribute '(\w+)'", msg)
            if m2: cat[f"CLASSATTR:{m2.group(1)}.{m2.group(2)}"] += 1
            else: cat["ATTR:other"] += 1
    elif "未注册" in msg: cat["REGISTRY"] += 1
    elif "in []" in msg and "assert" in msg: cat["REGISTRY:empty"] += 1
    elif "missing 1 required positional argument" in msg:
        m = re.search(r"(\w+)\(\) missing .+ '(\w+)'", msg)
        cat["MISSING_ARG"] += 1
        if m: detail["MISSING_ARG"].append(f"{m.group(1)}.{m.group(2)}")
    elif msg.startswith("assert") or "AssertionError" in msg: cat["ASSERTION"] += 1
    elif "TypeError" in msg:
        cat["TYPE_ERROR"] += 1
        detail["TYPE_ERROR"].append(msg[:80])
    elif "ParamSpec" in msg: cat["PARAMSPEC"] += 1
    else: cat["OTHER"] += 1
    
total = sum(cat.values())
print(f"TOTAL: {total}")
for k in sorted(cat, key=cat.get, reverse=True):
    v = cat[k]
    print(f"  [{v:4d}] {k}")
    if k in detail:
        counted = Counter(detail[k]).most_common(5)
        for item, cnt in counted:
            print(f"          {cnt}x {item}")
