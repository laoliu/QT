import subprocess, sys, re
r = subprocess.run(
    [sys.executable, '-m', 'pytest', 'tests/strategy/test_v22_bmf.py', '-q', '--tb=line', '--no-header'],
    capture_output=True, text=True, encoding='utf-8', errors='replace'
)
out = r.stdout + r.stderr
for line in out.splitlines():
    if 'FAILED' in line or 'ERROR' in line:
        print(repr(line[:120]))
