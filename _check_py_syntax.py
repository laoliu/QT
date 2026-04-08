"""Check all Python files for syntax errors."""
import ast, os

for root, dirs, files in os.walk(r'D:\work\QT\quant-trading\python'):
    for fn in files:
        if not fn.endswith('.py'):
            continue
        fp = os.path.join(root, fn)
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                source = f.read()
            ast.parse(source)
        except SyntaxError as e:
            print(f'{os.path.relpath(fp, r"D:\work\QT")}:{e.lineno}: {e.msg}')
