"""Find all damaged lines in untracked files and show context for manual fix."""
import re, os

os.chdir(r'D:\work\QT')

files = os.popen('git status --porcelain').readlines()
untracked = []
for line in files:
    line = line.rstrip()
    if line.startswith('?? ') and (line[3:].endswith('.ts') or line[3:].endswith('.py')):
        f = line[3:]
        if os.path.exists(f) and not os.path.basename(f).startswith('_'):
            untracked.append(f)

for f in untracked:
    text = open(f, 'r', encoding='utf-8').read()
    lines = text.split('\n')
    issues = []
    for i, line in enumerate(lines):
        # Detect damaged lines: numbers where Chinese should be
        # 1. "— NNNN" in comments/descriptions
        if re.search(r'— \d{4,}', line):
            issues.append((i+1, line.rstrip()))
        # 2. Lines with functionally meaningless digit sequences in doc context
        elif re.search(r'^\s*\*\s.*\d{4,}', line) and not re.search(r'(port|version|year|20\d\d|\d+_\d+|lookback|0x|limit)', line, re.I):
            # But check it's not a real number (like counts, ports, etc.)
            nums = re.findall(r'\b\d{4,}\b', line)
            for n in nums:
                # If the number is adjacent to Chinese or in a description context, it's likely damaged
                if re.search(r'[\u4e00-\u9fff]\d{4,}|' + n + r'[\u4e00-\u9fff]', line):
                    issues.append((i+1, line.rstrip()))
                    break
        # 3. String with [\u4e00-\u9fff]NNNN[\u4e00-\u9fff] pattern
        elif re.search(r'[\u4e00-\u9fff]\d{4,}[\u4e00-\u9fff]', line):
            issues.append((i+1, line.rstrip()))
        # 4. Lines in 功能 section with "- NNNN" pattern
        elif re.search(r'^\s*\*?\s*-\s+\d{4,}', line):
            issues.append((i+1, line.rstrip()))
        # 5. Description value that's ALL digits (tool description corrupted)
        elif re.search(r'description:\s*["`]\d{6,}', line):
            issues.append((i+1, line.rstrip()))
    
    if issues:
        print(f'\n=== {f} ({len(issues)} damaged lines) ===')
        for ln, content in issues:
            print(f'  L{ln}: {content[:120]}')
