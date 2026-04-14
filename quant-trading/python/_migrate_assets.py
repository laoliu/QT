"""Migrate configs/, tests/, and .env templates from AlphQ to QT."""
import os
import shutil

ALPHQ = r"D:\work\AlphQ"
QT_PY = r"D:\work\QT\quant-trading\python"

stats = {"copied": 0, "skipped": 0, "errors": []}


def copy_tree(src_dir, dst_dir, label):
    """Copy a directory tree, skipping files that already exist."""
    for dirpath, _, filenames in os.walk(src_dir):
        rel = os.path.relpath(dirpath, src_dir)
        dst_sub = os.path.join(dst_dir, rel) if rel != "." else dst_dir
        for f in filenames:
            # skip __pycache__, .pyc, etc.
            if "__pycache__" in dirpath or f.endswith(".pyc"):
                continue
            src = os.path.join(dirpath, f)
            dst = os.path.join(dst_sub, f)
            if os.path.exists(dst):
                stats["skipped"] += 1
                continue
            os.makedirs(dst_sub, exist_ok=True)
            try:
                shutil.copy2(src, dst)
                stats["copied"] += 1
            except Exception as e:
                stats["errors"].append((src, str(e)))
    print(f"  {label}: copied={stats['copied']}, skipped(existing)={stats['skipped']}")
    stats["copied"] = 0
    stats["skipped"] = 0


# 1. configs/
print("=== configs/ ===")
copy_tree(
    os.path.join(ALPHQ, "configs"),
    os.path.join(QT_PY, "configs"),
    "configs",
)

# 2. tests/
print("\n=== tests/ ===")
copy_tree(
    os.path.join(ALPHQ, "tests"),
    os.path.join(QT_PY, "tests"),
    "tests",
)

# 3. .env templates
print("\n=== .env templates ===")
env_files = [".env.example", ".env.paper.example", ".env.research.example"]
for ef in env_files:
    src = os.path.join(ALPHQ, ef)
    dst = os.path.join(QT_PY, ef)
    if not os.path.exists(src):
        print(f"  {ef}: not found in AlphQ, skip")
        continue
    if os.path.exists(dst):
        print(f"  {ef}: already exists, skip")
        continue
    shutil.copy2(src, dst)
    print(f"  {ef}: copied")

# 4. pyproject.toml (for reference)
src_toml = os.path.join(ALPHQ, "pyproject.toml")
dst_toml = os.path.join(QT_PY, "pyproject.alphq.toml")
if os.path.exists(src_toml) and not os.path.exists(dst_toml):
    shutil.copy2(src_toml, dst_toml)
    print(f"\n  pyproject.toml -> pyproject.alphq.toml (reference copy)")

if stats["errors"]:
    print(f"\nErrors: {len(stats['errors'])}")
    for p, e in stats["errors"][:10]:
        print(f"  {p}: {e}")
else:
    print("\nDone, no errors.")
