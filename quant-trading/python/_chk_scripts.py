import ast, os

d = "scripts"
ok = fail = 0
errs = []
for f in sorted(os.listdir(d)):
    if not f.endswith(".py"):
        continue
    p = os.path.join(d, f)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            ast.parse(fh.read(), p)
        ok += 1
    except SyntaxError as e:
        fail += 1
        errs.append((f, str(e)[:80]))
    except Exception as e:
        fail += 1
        errs.append((f, str(e)[:80]))
print(f"Syntax OK: {ok} / {ok + fail}")
for f, e in errs[:10]:
    print(f"  {f}: {e}")
