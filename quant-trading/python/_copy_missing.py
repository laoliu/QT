"""Copy all missing AlphQ files into QT aipha package."""
import os
import shutil

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
print(f"Missing: {len(missing)} files")

copied = 0
errors = []
for relpath in missing:
    src = os.path.join(ALPHQ, relpath.replace("/", os.sep))
    dst = os.path.join(QT, relpath.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        shutil.copy2(src, dst)
        copied += 1
    except Exception as e:
        errors.append((relpath, str(e)))

print(f"Copied: {copied}")
if errors:
    print(f"Errors: {len(errors)}")
    for p, e in errors:
        print(f"  {p}: {e}")
