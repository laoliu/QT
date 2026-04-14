"""Quick verification: can all test files be parsed (syntax OK)?"""
import ast
import os
import sys

test_dir = os.path.join(".", "tests")
ok = 0
fail = 0
failures = []

for dirpath, _, filenames in os.walk(test_dir):
    for f in sorted(filenames):
        if not f.endswith(".py"):
            continue
        path = os.path.join(dirpath, f)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                ast.parse(fh.read(), filename=path)
            ok += 1
        except SyntaxError as e:
            fail += 1
            failures.append((path, str(e)[:100]))

print(f"Syntax OK: {ok} / {ok + fail}")
if failures:
    print(f"Syntax errors: {fail}")
    for p, e in failures[:20]:
        print(f"  {p}: {e}")
