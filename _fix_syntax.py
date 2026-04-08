"""Fix ?/\" swap corruption in untracked TS files.
The previous fix scripts swapped ? and \" in wrong contexts.
This script fixes specific patterns using TS syntax rules.
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
    
    # Fix 1: import from ?xxx" → from "xxx"
    text = re.sub(r'from\s+\?([^"]+)"', r'from "\1"', text)
    
    # Fix 2: ?? → "" for empty string returns (return ??;)
    text = re.sub(r'return\s+\?\?;', 'return "";', text)
    
    # Fix 3: identifier" followed by ?: or : in TS declarations → identifier?:
    # Pattern: (private|public|protected|readonly) word": type
    text = re.sub(r'((?:private|protected|public|readonly)\s+\w+)"(\s*[?]?\s*:)', r'\1?\2', text)
    
    # Fix 4: function parameter word": type → word?: type
    # Only when inside parens and before a type
    text = re.sub(r'(\(\s*\w+)"(\s*[?]?\s*:)', r'\1?\2', text)
    text = re.sub(r'(,\s*\w+)"(\s*[?]?\s*:)', r'\1?\2', text)
    
    # Fix 5: interface/type member word": type → word?: type
    # When at start of line (indented), word": followed by any type annotation
    text = re.sub(r'^(\s+\w+)"(\s*:\s*(?:string|number|boolean|any|unknown|void|null|undefined|Record|Array|Map|Set|Promise|Date|Buffer|readonly|typeof|\(|{|\[))', 
                  r'\1?\2', text, flags=re.MULTILINE)
    # Also for word": NamedType or word": OtherInterface["key"]
    text = re.sub(r'^(\s+\w+)"(\s*:\s*[A-Z]\w*)', r'\1?\2', text, flags=re.MULTILINE)
    # Also word": (... (function types)
    text = re.sub(r'^(\s+\w+)"(\s*:\s*\()', r'\1?\2', text, flags=re.MULTILINE)

    # Fix 6: expr ?" value → expr ?? value (nullish coalescing)
    # Pattern: ) ?" or ] ?" or word ?"
    text = re.sub(r'([\w\)\]\}])\s+\?"(\s)', r'\1 ??\2', text)
    
    # Fix 7: config.prop ?" fallback (nullish with string after)
    # e.g., cfg.dataDir ?" path.join → cfg.dataDir ?? path.join
    text = re.sub(r'(\.\w+)\s+\?"(\s)', r'\1 ??\2', text)
    
    # Fix 8: value ?" "string" → value ?? "string"  (nullish before string literal)
    text = re.sub(r'([\w\)\]\}])\s+\?\s*"([^"]+)"', r'\1 ?? "\2"', text)
    
    # Fix 9: ?? expr : expr (was ternary ? expr : expr, got double?)
    # This is trickier - ?? followed by : is wrong (should be ? for ternary)
    # Pattern: value ?? expr : expr  → value ? expr : expr
    # But we must not break real ?? expressions
    # Only fix when followed by `: ` which indicates ternary
    
    if text != original:
        changes = sum(1 for a, b in zip(original.split('\n'), text.split('\n')) if a != b)
        open(f, 'w', encoding='utf-8', newline='').write(text)
        total += changes
        if changes > 3:
            print(f"  {f}: {changes} lines fixed")

print(f"\nTotal: {total} lines fixed across {len(untracked)} files")
