"""Assess damage to untracked files from multi-pass encoding fix."""
import re, os

os.chdir(r'D:\work\QT')

files = os.popen('git status --porcelain').readlines()
untracked_ts = []
for line in files:
    line = line.rstrip()
    if line.startswith('?? ') and (line[3:].endswith('.ts') or line[3:].endswith('.py')):
        f = line[3:]
        if os.path.exists(f) and not os.path.basename(f).startswith('_'):
            untracked_ts.append(f)

good = []
damaged = []

for f in untracked_ts:
    text = open(f, 'r', encoding='utf-8').read()
    cn_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    
    # Signs of damage: Chinese contexts replaced by numbers
    # Pattern: lines with comments/strings that have isolated numbers where Chinese should be
    damage_signs = 0
    # Look for "— NNNN" pattern (em-dash followed by numbers, was Chinese)
    damage_signs += len(re.findall(r'— \d{4,}', text))
    # Comments with just numbers in description positions
    damage_signs += len(re.findall(r'\* +\d{4,}\n', text))
    # String with isolated multi-digit sequences mixed with Chinese
    damage_signs += len(re.findall(r'[\u4e00-\u9fff]\d{4,}[\u4e00-\u9fff]', text))
    # "功能:" followed by lines with just numbers
    damage_signs += len(re.findall(r'- \d{4,}[/\d]*\n', text))
    
    if damage_signs > 0:
        damaged.append((f, cn_chars, damage_signs))
    else:
        good.append((f, cn_chars))

print(f"Good files (no damage): {len(good)}")
for f, cn in good[:10]:
    print(f"  {f} ({cn} Chinese chars)")
    
print(f"\nDamaged files: {len(damaged)}")
for f, cn, dmg in damaged:
    print(f"  {f} ({cn} Chinese, {dmg} damage signs)")
