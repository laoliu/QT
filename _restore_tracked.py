"""Find the latest version of each lost file from remote dev branches.
For each file, pick the branch with the latest commit date.
Then restore the file from that branch.
"""
import subprocess, json, os

os.chdir(r'D:\work\QT')

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    return r.stdout.strip()

# Get all dev branch names
branches_raw = run('git branch -r --format="%(refname:short)" | findstr "dev/f61fffb2"')
branches = [b.strip() for b in branches_raw.split('\n') if b.strip()]

# Get existing tracked files
existing = set(run('git ls-tree --name-only -r HEAD -- quant-trading/').split('\n'))

# Get currently modified files (already have changes)
currently_modified = set(run('git diff --name-only HEAD -- quant-trading/').split('\n'))

# For each missing file, find all branches that modify it, pick latest
file_branch_map = {}  # file -> (branch, commit_date)

for branch in branches:
    # Get commit date
    date = run(f'git log -1 --format=%ai {branch} 2>nul')
    if not date:
        continue
    
    # Get files changed in this branch vs HEAD
    changed = run(f'git diff --name-only HEAD {branch} -- quant-trading/ 2>nul')
    if not changed:
        continue
    
    for f in changed.split('\n'):
        f = f.strip()
        if not f:
            continue
        # Only restore files that exist in HEAD (tracked) AND are NOT currently modified
        if f in existing and f not in currently_modified:
            if f not in file_branch_map or date > file_branch_map[f][1]:
                file_branch_map[f] = (branch, date)

print(f"Files to restore: {len(file_branch_map)}")
print()

# Group by branch for efficient checkout
branch_files = {}
for f, (branch, _) in sorted(file_branch_map.items()):
    branch_files.setdefault(branch, []).append(f)

for branch, files in sorted(branch_files.items()):
    print(f"\n--- {branch} ({len(files)} files) ---")
    for f in sorted(files):
        print(f"  {f}")

# Now do the actual restore
print("\n\n=== RESTORING ===\n")
restored = 0
for branch, files in branch_files.items():
    for f in files:
        result = subprocess.run(
            f'git checkout {branch} -- "{f}"',
            capture_output=True, text=True, shell=True
        )
        if result.returncode == 0:
            restored += 1
        else:
            print(f"  FAIL: {f} from {branch}: {result.stderr.strip()}")

print(f"\nRestored {restored} files from dev branches")
