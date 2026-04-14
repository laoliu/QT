"""Verify imports of all aipha packages — find broken modules."""
import os
import sys
import importlib

sys.path.insert(0, ".")

AIPHA = "aipha"
base = os.path.join(".", AIPHA)

ok_count = 0
fail_count = 0
failures = []

for dirpath, _, filenames in os.walk(base):
    rel = os.path.relpath(dirpath, ".").replace("\\", "/")
    pkg = rel.replace("/", ".")
    for f in sorted(filenames):
        if not f.endswith(".py") or f.startswith("__"):
            continue
        mod_name = f"{pkg}.{f[:-3]}"
        try:
            importlib.import_module(mod_name)
            ok_count += 1
        except Exception as e:
            fail_count += 1
            failures.append((mod_name, type(e).__name__, str(e)[:150]))

total = ok_count + fail_count
print(f"OK: {ok_count} / {total}")
if failures:
    print(f"FAILED: {fail_count}")
    from collections import defaultdict
    by_err = defaultdict(list)
    for mod, etype, msg in failures:
        by_err[(etype, msg)].append(mod)
    for (etype, msg), mods in sorted(by_err.items(), key=lambda x: -len(x[1])):
        print(f"\n  [{etype}] ({len(mods)} modules) {msg}")
        for m in mods[:5]:
            print(f"    - {m}")
        if len(mods) > 5:
            print(f"    ... and {len(mods)-5} more")
