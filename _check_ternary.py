"""Check for specific ?/\" patterns in all untracked TS files."""
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

# Count specific patterns
pattern_counts = {}
for f in untracked:
    text = open(f, 'r', encoding='utf-8').read()
    for i, line in enumerate(text.split('\n')):
        stripped = line.lstrip()
        if stripped.startswith('//') or stripped.startswith('*'):
            continue
        # Ternary: x >= 0 ?? "+" : ""  (should be ?)
        if re.search(r'\?\?\s*"[^"]*"\s*:', line):
            key = 'ternary_??_str_colon'
            pattern_counts.setdefault(key, []).append((f, i+1, line.rstrip()[:120]))
        # Ternary: x ?? `template` :
        if re.search(r'\?\?\s*`[^`]*`\s*:', line):
            key = 'ternary_??_tmpl_colon'
            pattern_counts.setdefault(key, []).append((f, i+1, line.rstrip()[:120]))
        # case ?word? :
        if re.search(r'case\s+\?[^?\n]+\?\s*:', line):
            key = 'case_?word?'
            pattern_counts.setdefault(key, []).append((f, i+1, line.rstrip()[:120]))
        # "object? as const
        if re.search(r'"object\?\s+as\s+const', line):
            key = 'object?_const'
            pattern_counts.setdefault(key, []).append((f, i+1, line.rstrip()[:120]))
        # Opening ? for string: = ?text" or : ?text"
        if re.search(r'(?:return|===?|!==?|error:)\s+\?[^?\n]+?"', line):
            key = 'open_?_close_"'
            pattern_counts.setdefault(key, []).append((f, i+1, line.rstrip()[:120]))
        # "text? followed by ; , ) ] }
        if re.search(r'"[^"?\n]+?\?[;,\)\]\}]', line):
            key = 'close_?_punc'
            pattern_counts.setdefault(key, []).append((f, i+1, line.rstrip()[:120]))

for key in sorted(pattern_counts.keys()):
    items = pattern_counts[key]
    print(f"\n=== {key} ({len(items)} occurrences) ===")
    for f, lineno, content in items[:5]:
        print(f"  {f}:{lineno}: {content}")
    if len(items) > 5:
        print(f"  ... and {len(items)-5} more")
