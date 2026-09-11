"""Categorize test failures from qt_fails5.txt."""
import re
from collections import Counter

with open(r"c:\temp\qt_fails5.txt", encoding="utf-8") as f:
    lines = f.readlines()

failed = [l.strip() for l in lines if l.strip().startswith("FAILED")]
print(f"Total FAILED lines: {len(failed)}")

cats = Counter()
samples = {}  # cat -> sample error line

for i, l in enumerate(lines):
    ls = l.strip()
    if ls.startswith("FAILED"):
        err = lines[i - 1].strip() if i > 0 else ""
        cat = None

        if "AssertionError" in err or "assert " in err or "AssertionError" in err:
            cat = "ASSERTION"
        elif "'str' object has no attribute" in err:
            m = re.search(r"has no attribute '(\w+)'", err)
            cat = f"str.{m.group(1) if m else '?'}"
        elif "'list' object has no attribute" in err:
            m = re.search(r"has no attribute '(\w+)'", err)
            cat = f"list.{m.group(1) if m else '?'}"
        elif "TypeError" in err:
            if "delta_exposure" in err:
                cat = "delta_exposure_args"
            elif "_on_bar_update_state" in err:
                cat = "on_bar_update_state_args"
            elif "missing" in err and "required" in err:
                fn = re.search(r"(\w+)\(\) missing", err)
                cat = f"MISSING_ARG.{fn.group(1) if fn else '?'}"
            elif "unexpected keyword" in err:
                kw = re.search(r"unexpected keyword argument '(\w+)'", err)
                cat = f"UNEXPECTED_KW.{kw.group(1) if kw else '?'}"
            elif "takes" in err and "positional" in err:
                fn = re.search(r"(\w+)\(\) takes", err)
                cat = f"POSITIONAL.{fn.group(1) if fn else '?'}"
            else:
                cat = f"TypeError_other"
        elif "AttributeError" in err:
            m_attr = re.search(r"has no attribute '(\w+)'", err)
            m_obj = re.search(r"'(\w+)' object", err)
            cat = f"ATTR.{m_obj.group(1) if m_obj else '?'}.{m_attr.group(1) if m_attr else '?'}"
        elif "KeyError" in err:
            cat = "KeyError"
        elif "ValueError" in err:
            cat = "ValueError"
        elif "NotImplementedError" in err:
            cat = "NotImplementedError"
        else:
            cat = "OTHER"

        if cat:
            cats[cat] += 1
            if cat not in samples:
                samples[cat] = (ls[:100], err[:120])

print()
for cat, count in cats.most_common(50):
    test_sample, err_sample = samples[cat]
    print(f"{count:4d}  {cat}")
    print(f"      test: {test_sample}")
    print(f"      err:  {err_sample}")
    print()
