import subprocess, sys
cmd = [sys.executable, '-m', 'pytest', 'tests/strategy/test_adaptive_multifactor.py', '--tb=line']
r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
for line in r.stdout.splitlines():
    print(repr(line[:200]))
