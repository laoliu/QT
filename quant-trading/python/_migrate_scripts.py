"""Migrate core AlphQ scripts to QT, excluding temp/diag/one-off files."""
import os
import re
import shutil

ALPHQ_SCRIPTS = r"D:\work\AlphQ\scripts"
QT_SCRIPTS = r"D:\work\QT\quant-trading\python\scripts"

# Patterns to EXCLUDE (temp, diagnostic, one-off eval)
EXCLUDE_RE = re.compile(r"^(_tmp|_diag|_combo_r|eval_r1\d|_seed|_analyze_diversity)")

copied = 0
skipped_exist = 0
skipped_exclude = 0

for f in sorted(os.listdir(ALPHQ_SCRIPTS)):
    src = os.path.join(ALPHQ_SCRIPTS, f)
    if not os.path.isfile(src):
        continue
    if EXCLUDE_RE.match(f):
        skipped_exclude += 1
        continue
    dst = os.path.join(QT_SCRIPTS, f)
    if os.path.exists(dst):
        skipped_exist += 1
        continue
    shutil.copy2(src, dst)
    copied += 1

print(f"Copied: {copied}")
print(f"Skipped (already exists): {skipped_exist}")
print(f"Skipped (excluded pattern): {skipped_exclude}")
