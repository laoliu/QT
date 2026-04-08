"""Comprehensive fix for ?/" swap damage in untracked TS files.

Core patterns found:
1. case ?word?: → case "word":  (switch case labels)
2. "word? as const → "word" as const (closing quote became ?)
3. as ?type" → as "type" (opening quote became ?)
4. ?? "expr" : "expr" → ? "expr" : "expr" (ternary lost a ?)
5. Various ?/\" swaps at string boundaries
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

total_changes = 0
total_files = 0

for f in untracked:
    text = open(f, 'r', encoding='utf-8').read()
    original = text

    # === 1. case ?word?: → case "word": ===
    text = re.sub(r'case\s+\?([^?\n]+?)\?\s*:', r'case "\1":', text)

    # === 2. "word? as const → "word" as const ===
    text = re.sub(r'"(\w+)\?\s+as\s+const', r'"\1" as const', text)

    # === 3. Closing " became ? before ; , ) ] } ===
    text = re.sub(r'"([^"?\n]+?)\?(\s*[;,\)\]\}])', r'"\1"\2', text)

    # === 4. return ?value" → return "value" ===
    text = re.sub(r'(return\s+)\?([^?\n]+?)"', r'\1"\2"', text)
    
    # === 5. === ?value" or !== ?value" → === "value" ===
    text = re.sub(r'(===?\s+)\?([^?\n]{1,40}?)"', r'\1"\2"', text)
    text = re.sub(r'(!==?\s+)\?([^?\n]{1,40}?)"', r'\1"\2"', text)

    # === 6. as ?type" → as "type" ===
    text = re.sub(r'as\s+\?([^?\n]{1,60}?)"', r'as "\1"', text)

    # === 7. Ternary: ?? "val": "val" → ? "val" : "val" ===
    text = re.sub(r'\?\?\s*"([^"]*?)"\s*:\s*"', r'? "\1" : "', text)
    
    # === 8. Ternary: ?? `template` : → ? `template` : ===
    text = re.sub(r'\?\?\s*(`[^`]*?`)\s*:', r'? \1 :', text)

    # === 9. Ternary: ?? expr : "val → ? expr : "val ===
    text = re.sub(r'(\w)\s+\?\?\s+"([^"]+)"\s*:\s*"', r'\1 ? "\2" : "', text)
    
    # === 10. Ternary: ?? true/false/null/num : ===
    text = re.sub(r'\?\?\s*(true|false|null|undefined|0|1)\s*:', r'? \1 :', text)
    
    # === 11. Ternary: ?? identifier : "string ===
    text = re.sub(r'\?\?\s*([\w.]+(?:\([^)]*\))?)\s*:\s*"', r'? \1 : "', text)
    text = re.sub(r'\?\?\s*([\w.]+(?:\([^)]*\))?)\s*:\s*`', r'? \1 : `', text)

    # === 12. { error: ?message" } → { error: "message" } ===
    text = re.sub(r'(error:\s+)\?([^?\n]+?)"', r'\1"\2"', text)

    # === 13. Ternary: expr " "value" : → expr ? "value" : ===
    # A standalone " between expression and string literal in ternary
    text = re.sub(r'(\w)\s+"\s+"([^"]*?)"\s*:\s*"', r'\1 ? "\2" : "', text)
    text = re.sub(r'(\))\s+"\s+"([^"]*?)"\s*:\s*"', r'\1 ? "\2" : "', text)
    
    # === 14. Ternary: "word?: " → "word" : " (closing quote before colon) ===
    text = re.sub(r'"([^"?\n]+?)\?\s*:\s*"', r'"\1" : "', text)
    
    # === 15. Ternary: "word?: expr → "word" : expr ===
    text = re.sub(r'"([^"?\n]+?)\?\s*:\s*`', r'"\1" : `', text)
    
    # === 16. Bare digit-sequences used as object keys: 0607: → "0607": ===
    # These are destroyed Chinese that became bare numbers acting as keys
    text = re.sub(r'^(\s+)(\d{4,8})(\s*:)', r'\1"\2"\3', text, flags=re.MULTILINE)

    # === 17. Bare Chinese fragments used as keys: 近似匹配: → "近似匹配": ===
    # Keys that have mixed Chinese + digits/latin
    text = re.sub(r'^(\s+)([\u4e00-\u9fff\w]*[\u4e00-\u9fff][\u4e00-\u9fff\w]*)(\s*:)(?!\s*$)', 
                  r'\1"\2"\3', text, flags=re.MULTILINE)

    # === 18. ā prefix (garbled) in identifiers within strings ===
    # ā is a garbled character, often in key positions
    text = re.sub(r'^(\s+)(ā[^:,\n]+?)(\s*[:,])', r'\1"\2"\3', text, flags=re.MULTILINE)

    # === 19. Standalone " that should be ? in ternary before { ===
    # Pattern: expr \n " { → expr \n ? {
    text = re.sub(r'^(\s+)" \{', r'\1? {', text, flags=re.MULTILINE)
    
    # === 20. word.prop\n " { or .prop\n" { (ternary before object) ===
    # Already handled by 19 since it's on start of line

    if text != original:
        changes = sum(1 for a, b in zip(original.split('\n'), text.split('\n')) if a != b)
        if changes > 0:
            open(f, 'w', encoding='utf-8', newline='').write(text)
            total_changes += changes
            total_files += 1
            if changes > 2:
                print(f"  {f}: {changes} lines")

print(f"\nTotal: {total_changes} lines fixed in {total_files} files")
