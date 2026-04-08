"""Fix ?? that should be ? (ternary, not nullish) in untracked TS files.
Pattern: expr ?? "something" : "other"  should be  expr ? "something" : "other"
"""
import re, os

os.chdir(r'D:\work\QT')

files = os.popen('git status --porcelain').readlines()
untracked = []
for line in files:
    line = line.rstrip()
    if line.startswith('?? ') and line[3:].endswith('.ts'):
        f = line[3:]
        if os.path.exists(f) and not os.path.basename(f).startswith('_'):
            untracked.append(f)

total = 0
for f in untracked:
    text = open(f, 'r', encoding='utf-8').read()
    original = text
    
    # Key insight: ?? "val" : is always wrong (ternary, not nullish)
    # Nullish coalescing cannot have a : branch
    text = re.sub(r'\?\?\s*"([^"]*)"\s*:\s*"', r'? "\1" : "', text)
    text = re.sub(r'\?\?\s*"([^"]*)"\s*:\s*`', r'? "\1" : `', text)
    text = re.sub(r'\?\?\s*`([^`]*)`\s*:', r'? `\1` :', text)
    # ?? "word" : identifier
    text = re.sub(r'\?\?\s*"([^"]*)"\s*:\s*(\w)', r'? "\1" : \2', text)
    
    if text != original:
        changes = sum(1 for a, b in zip(original.split('\n'), text.split('\n')) if a != b)
        if changes > 0:
            open(f, 'w', encoding='utf-8', newline='').write(text)
            total += changes
            print(f"  {f}: {changes} lines")

print(f"\nTotal: {total} lines")
