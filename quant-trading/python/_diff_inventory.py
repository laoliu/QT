"""Compare AlphQ vs QT aipha packages — find missing modules."""
import os

ALPHQ = r"D:\work\AlphQ\src\aipha"
QT = r"D:\work\QT\quant-trading\python\aipha"

def collect(root):
    result = set()
    for dirpath, _, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel == ".":
            rel = ""
        for f in filenames:
            if f.endswith(".py"):
                path = f"{rel}/{f}" if rel else f
                result.add(path)
    return result

alphq = collect(ALPHQ)
qt = collect(QT)

missing = sorted(alphq - qt)
extra = sorted(qt - alphq)

# Group by package
from collections import defaultdict
pkg_missing = defaultdict(list)
for p in missing:
    parts = p.split("/")
    pkg = parts[0] if len(parts) > 1 else "(root)"
    pkg_missing[pkg].append(p)

print(f"AlphQ files: {len(alphq)}")
print(f"QT files:    {len(qt)}")
print(f"Missing:     {len(missing)}")
print(f"QT-only:     {len(extra)}")
print()

print("=" * 60)
print("MISSING from QT (in AlphQ but not in QT)")
print("=" * 60)
for pkg in sorted(pkg_missing):
    files = pkg_missing[pkg]
    print(f"\n  {pkg}/ ({len(files)} files)")
    for f in sorted(files):
        print(f"    {f}")

if extra:
    print()
    print("=" * 60)
    print("QT-only (in QT but not in AlphQ)")
    print("=" * 60)
    for f in extra:
        print(f"    {f}")
