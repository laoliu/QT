import os, importlib, sys
sys.path.insert(0, '.')
strategy_dir = 'aipha/strategy'
files = sorted(f[:-3] for f in os.listdir(strategy_dir) if f.endswith('.py') and not f.startswith('__'))
ok, fail = [], []
for f in files:
    mod = f'aipha.strategy.{f}'
    try:
        importlib.import_module(mod)
        ok.append(f)
    except Exception as e:
        fail.append((f, str(e)[:120]))
print(f'OK: {len(ok)} / {len(ok)+len(fail)}')
if fail:
    print('FAILED:')
    for name, err in fail:
        print(f'  {name}: {err}')
