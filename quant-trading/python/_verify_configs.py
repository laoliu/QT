"""Verify all YAML/JSON config files are parseable."""
import json
import os

try:
    import yaml
except ImportError:
    yaml = None
    print("WARNING: pyyaml not installed, skipping YAML validation")

config_dir = os.path.join(".", "configs")
ok = 0
fail = 0
failures = []

for dirpath, _, filenames in os.walk(config_dir):
    for f in sorted(filenames):
        path = os.path.join(dirpath, f)
        try:
            if f.endswith(".json"):
                with open(path, "r", encoding="utf-8") as fh:
                    json.load(fh)
                ok += 1
            elif f.endswith((".yaml", ".yml")) and yaml:
                with open(path, "r", encoding="utf-8") as fh:
                    yaml.safe_load(fh)
                ok += 1
            else:
                continue
        except Exception as e:
            fail += 1
            failures.append((path, str(e)[:100]))

print(f"Config OK: {ok} / {ok + fail}")
if failures:
    print(f"Parse errors: {fail}")
    for p, e in failures[:20]:
        print(f"  {p}: {e}")
