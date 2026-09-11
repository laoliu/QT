"""Parse JUnit XML and categorize test failures - version 2."""
import re
import xml.etree.ElementTree as ET
from collections import Counter

tree = ET.parse(r"c:\temp\qt8.xml")
root = tree.getroot()

cats = Counter()
samples = {}
test_to_cat = {}

for tc in root.iter("testcase"):
    fail = tc.find("failure")
    err_el = tc.find("error")
    
    if fail is not None:
        msg = fail.get("message", "") or ""
        text = fail.text or ""
    elif err_el is not None:
        msg = err_el.get("message", "") or ""
        text = err_el.text or ""
    else:
        continue
    
    combined = msg + " " + text
    name = f"{tc.get('classname')}::{tc.get('name')}"
    cat = None

    if "'str' object has no attribute" in combined:
        m = re.search(r"has no attribute '(\w+)'", combined)
        cat = f"str.{m.group(1) if m else '?'}"
    elif "'list' object has no attribute" in combined:
        m = re.search(r"has no attribute '(\w+)'", combined)
        cat = f"list.{m.group(1) if m else '?'}"
    elif "'NoneType' object" in combined and "has no attribute" in combined:
        cat = "NoneType_attr"
    elif "missing" in combined and "required" in combined and "positional" in combined:
        fn = re.search(r"(\w+)\(\) missing \d+ required positional argument", combined)
        arg = re.search(r"argument: '(\w+)'", combined)
        cat = f"MISSING_ARG.{fn.group(1) if fn else '?'}.{arg.group(1) if arg else '?'}"
    elif "unexpected keyword argument" in combined:
        kw = re.search(r"unexpected keyword argument '(\w+)'", combined)
        cat = f"UNEXPECTED_KW.{kw.group(1) if kw else '?'}"
    elif "takes" in combined and "positional argument" in combined:
        fn = re.search(r"(\w+)\(\) takes", combined)
        cat = f"POSITIONAL.{fn.group(1) if fn else '?'}"
    elif "has no attribute" in combined:
        m_obj = re.search(r"'(\w+)' object has no attribute '(\w+)'", combined)
        if m_obj:
            cat = f"ATTR.{m_obj.group(1)}.{m_obj.group(2)}"
        else:
            cat = "ATTR_OTHER"
    elif "not callable" in combined:
        cat = "NOT_CALLABLE"
    elif "KeyError" in combined:
        cat = "KeyError"
    elif "ValueError" in combined:
        cat = "ValueError"
    elif "NotImplementedError" in combined:
        cat = "NotImplementedError"
    elif "TypeError" in combined:
        cat = "TypeError_other"
    else:
        cat = "ASSERTION"

    cats[cat] += 1
    test_to_cat[name] = cat
    if cat not in samples:
        samples[cat] = (name, combined[:250])

print(f"Total categorized: {sum(cats.values())}")
print()
for cat, count in cats.most_common(60):
    name_s, msg_s = samples[cat]
    print(f"{count:4d}  {cat}")
    print(f"      sample: {name_s}")
    print(f"      msg:    {msg_s[:180]}")
    print()

# Per test file failure counts
file_counts = Counter()
for name in test_to_cat:
    parts = name.split("::")
    cls = parts[0] if parts else name
    # Extract test file name
    m = re.search(r"test_\w+", cls)
    if m:
        file_counts[m.group(0)] += 1
    else:
        file_counts[cls] += 1

print("\n--- Top failing test files ---")
for f, count in file_counts.most_common(30):
    print(f"  {count:4d}  {f}")
