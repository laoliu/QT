"""Fix remaining garbled characters in TS files."""
import os

fixes = {
    '鈫?': '→',
    '鉁?': '✅',
    '鈿?': '⚠️',
    '鈮?': '≈',
    '请提侀': '请提供',
    '已提了': '已提交',
    '笀': '笔',
    '190004': '暂无分红数据',
}

root = r'D:\work\QT\quant-trading\src'
total = 0
for dirpath, dirs, files in os.walk(root):
    for fn in files:
        if not fn.endswith('.ts'):
            continue
        fp = os.path.join(dirpath, fn)
        text = open(fp, 'r', encoding='utf-8').read()
        old = text
        for bad, good in fixes.items():
            text = text.replace(bad, good)
        if text != old:
            open(fp, 'w', encoding='utf-8', newline='').write(text)
            changes = sum(1 for a, b in zip(old.split('\n'), text.split('\n')) if a != b)
            total += changes
            print(f"  {os.path.relpath(fp, r'D:\work\QT')}: {changes} lines")

print(f"\nTotal: {total} lines fixed")
