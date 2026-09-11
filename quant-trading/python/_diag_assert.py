"""Analyze ASSERTION failures to find patterns."""
import re
import xml.etree.ElementTree as ET
from collections import Counter

tree = ET.parse(r"c:\temp\qt8.xml")
root = tree.getroot()

assertion_patterns = Counter()
assertion_samples = {}

for tc in root.iter("testcase"):
    fail = tc.find("failure")
    if fail is None:
        continue
    
    msg = fail.get("message", "") or ""
    text = fail.text or ""
    combined = msg + " " + text
    name = f"{tc.get('classname')}::{tc.get('name')}"

    # Skip non-assertion failures
    if any(kw in combined for kw in ["KeyError", "TypeError", "ValueError", "AttributeError", "'NoneType'"]):
        continue

    # Categorize assertion patterns
    pat = None
    
    if "assert [] ==" in combined or "assert [] !=" in combined or "result == []" in combined:
        pat = "EMPTY_LIST"
    elif "assert 0 ==" in combined or "len(result) == 0" in combined or "len(intents) == 0" in combined:
        pat = "ZERO_LENGTH"
    elif "assert 0.0 ==" in combined:
        pat = "ZERO_FLOAT"
    elif "not in" in combined and "register" in combined.lower():
        pat = "NOT_REGISTERED"
    elif "assert False" in combined or "not True" in combined:
        pat = "ASSERT_FALSE"
    elif "assert None" in combined:
        pat = "ASSERT_NONE"
    elif "weight" in combined.lower() and ("==" in combined or "!=" in combined):
        pat = "WEIGHT_MISMATCH"
    elif "config" in combined.lower() and ("==" in combined or "!=" in combined):
        pat = "CONFIG_MISMATCH"
    elif "assert " in combined and " == " in combined:
        # Extract the values
        m = re.search(r"assert\s+([\d.e+-]+)\s*==\s*([\d.e+-]+)", combined)
        if m:
            pat = f"VALUE_MISMATCH"
        else:
            pat = "EQUALITY_MISMATCH"
    elif "assert " in combined and " > " in combined:
        pat = "COMPARISON"
    elif "assert " in combined and " in " in combined:
        pat = "MEMBERSHIP"
    elif "isinstance" in combined:
        pat = "ISINSTANCE"
    elif "assert len" in combined:
        pat = "LENGTH"
    else:
        pat = "OTHER_ASSERT"

    assertion_patterns[pat] += 1
    if pat not in assertion_samples:
        assertion_samples[pat] = (name, combined[:200])

print(f"Total assertion failures analyzed: {sum(assertion_patterns.values())}")
print()
for pat, count in assertion_patterns.most_common(30):
    name_s, msg_s = assertion_samples[pat]
    print(f"{count:4d}  {pat}")
    print(f"      sample: {name_s}")
    print(f"      msg:    {msg_s[:160]}")
    print()
