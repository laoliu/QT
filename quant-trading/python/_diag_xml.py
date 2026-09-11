"""Parse JUnit XML and categorize test failures."""
import re
import xml.etree.ElementTree as ET
from collections import Counter

tree = ET.parse(r"c:\temp\qt5.xml")
root = tree.getroot()

cats = Counter()
samples = {}
test_to_cat = {}

for tc in root.iter("testcase"):
    fail = tc.find("failure")
    err = tc.find("error")
    elem = fail or err
    if elem is None:
        continue
    
    msg = elem.get("message", "")
    name = f"{tc.get('classname')}::{tc.get('name')}"
    cat = None

    # Categorize
    if "'str' object has no attribute" in msg:
        m = re.search(r"has no attribute '(\w+)'", msg)
        cat = f"str.{m.group(1) if m else '?'}"
    elif "'list' object has no attribute" in msg:
        m = re.search(r"has no attribute '(\w+)'", msg)
        cat = f"list.{m.group(1) if m else '?'}"
    elif "'NoneType' object" in msg:
        cat = "NoneType"
    elif "missing" in msg and "required" in msg and "positional" in msg:
        fn = re.search(r"(\w+)\(\) missing \d+ required positional argument", msg)
        arg = re.search(r"argument: '(\w+)'", msg)
        cat = f"MISSING_ARG.{fn.group(1) if fn else '?'}.{arg.group(1) if arg else '?'}"
    elif "unexpected keyword argument" in msg:
        kw = re.search(r"unexpected keyword argument '(\w+)'", msg)
        cat = f"UNEXPECTED_KW.{kw.group(1) if kw else '?'}"
    elif "takes" in msg and "positional" in msg:
        fn = re.search(r"(\w+)\(\) takes", msg)
        cat = f"POSITIONAL.{fn.group(1) if fn else '?'}"
    elif "has no attribute" in msg:
        m_obj = re.search(r"'(\w+)' object has no attribute '(\w+)'", msg)
        if m_obj:
            cat = f"ATTR.{m_obj.group(1)}.{m_obj.group(2)}"
        else:
            cat = f"ATTR_OTHER"
    elif "not callable" in msg:
        cat = "NOT_CALLABLE"
    elif "KeyError" in msg:
        cat = "KeyError"
    elif "ValueError" in msg:
        cat = "ValueError"
    elif "NotImplementedError" in msg:
        cat = "NotImplementedError"
    elif "assert " in msg or "AssertionError" in msg or elem.tag == "failure":
        cat = "ASSERTION"
    else:
        cat = "OTHER"

    cats[cat] += 1
    test_to_cat[name] = cat
    if cat not in samples:
        samples[cat] = (name, msg[:200])

print(f"Total categorized: {sum(cats.values())}")
print()
for cat, count in cats.most_common(60):
    name_s, msg_s = samples[cat]
    print(f"{count:4d}  {cat}")
    print(f"      sample: {name_s}")
    print(f"      msg:    {msg_s[:150]}")
    print()

# Also show per-file failure counts for top failing files
file_cats = Counter()
for name, cat in test_to_cat.items():
    file = name.split("::")[0].split(".")[-1] if "::" in name else name
    file_cats[file] += 1

print("\n--- Top failing test classes ---")
for cls, count in file_cats.most_common(20):
    print(f"  {count:4d}  {cls}")
