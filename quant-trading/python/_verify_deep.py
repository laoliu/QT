"""全量模块深度扫描"""
import os, sys, importlib
base = r"D:\work\QT\quant-trading\python\aipha"
sys.path.insert(0, r"D:\work\QT\quant-trading\python")
ok = fail = 0
fails = []
for root, dirs, files in os.walk(base):
    dirs[:] = [d for d in dirs if d not in ("__pycache__", "cache", "configs")]
    for f in sorted(files):
        if f.endswith(".py") and f != "__init__.py":
            rel = os.path.relpath(os.path.join(root, f), r"D:\work\QT\quant-trading\python")
            mod = rel.replace(os.sep, ".")[:-3]
            try:
                importlib.import_module(mod)
                ok += 1
            except Exception as e:
                fails.append((mod, str(e)[:80]))
                fail += 1
for m, e in fails:
    print(f"  FAIL {m}: {e}")
print(f"\n{ok} OK / {fail} FAIL / {ok+fail} TOTAL")
