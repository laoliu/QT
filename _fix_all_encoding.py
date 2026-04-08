"""
Fix systematically corrupted Chinese string literals in .ts files.

Pattern: "garbled_chinese_text?  (where ? should be closing ")
The last byte of the Chinese text was corrupted, turning the closing " into ?
Fix: replace ? with " to restore valid syntax.
"""
import os
import re

root = r'D:\work\QT\quant-trading\src'
# Also check index.ts
extra_files = [r'D:\work\QT\quant-trading\index.ts']

total_fixes = 0
fixed_files = []

def fix_file(fpath):
    global total_fixes
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original = content
    
    # Pattern: opening " followed by CJK/garbled chars, ending with ? then delimiter
    # The ? should be replaced with "
    # We need to be careful not to match legitimate question marks in strings
    
    # Match: "CJK_chars?" where ? is followed by ), }, ;, ,, space+}, etc.
    # This means the string was: "chinese_text" but the " got corrupted to ?
    
    # Strategy: find all string-like patterns where a ? immediately precedes
    # what would be outside a string (comma, semicolon, closing paren/brace, etc.)
    # and the content between the opening " and the ? contains CJK characters
    
    def replacer(m):
        return m.group(1) + '"' + m.group(2)
    
    # Pattern explanation:
    # ("  - opening quote (captured in group 1 along with everything up to ?)
    # [^"]*  - any chars that aren't quotes (the garbled Chinese)
    # [\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u2000-\u206f\u00c0-\u024f]  - at least one CJK/special char
    # [^"]*  - more non-quote chars
    # )\?   - the corrupted ? that should be "
    # ([,;\s})>\]\[]|$)  - delimiter after the string
    
    # Simple approach: find "...?" where ... contains high-byte chars and ? is at string boundary
    pattern = r'("(?:[^"]*[\u4e00-\u9fff\uff00-\uffef\u2190-\u27bf\u2600-\u26ff\u2700-\u27bf\U0001f300-\U0001f9ff])[^"]*?)\?([,;\s})>\]:\|])'
    
    new_content = re.sub(pattern, replacer, content)
    
    # Also handle: "garbled?) pattern (closing paren after ?)  
    # And "garbled?` pattern (template literal)
    pattern2 = r'("(?:[^"]*[\u4e00-\u9fff\uff00-\uffef\u2190-\u27bf\u2600-\u26ff\u2700-\u27bf\U0001f300-\U0001f9ff])[^"]*?)\?(\))'
    new_content = re.sub(pattern2, replacer, new_content)
    
    if new_content != original:
        fixes = len(re.findall(pattern, content)) + len(re.findall(pattern2, content))
        total_fixes += fixes
        fixed_files.append((fpath, fixes))
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    return False

for dirpath, dirs, files in os.walk(root):
    for fn in files:
        if not fn.endswith('.ts'):
            continue
        fpath = os.path.join(dirpath, fn)
        fix_file(fpath)

for fpath in extra_files:
    if os.path.exists(fpath):
        fix_file(fpath)

print(f"\nFixed {total_fixes} broken string(s) in {len(fixed_files)} file(s):\n")
for fpath, count in fixed_files:
    relpath = os.path.relpath(fpath, r'D:\work\QT')
    print(f"  {relpath}: {count} fix(es)")
