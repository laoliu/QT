"""Comprehensive migration integrity check — AlphQ → QT."""
import ast
import importlib
import json
import os
import sys

sys.path.insert(0, ".")

ALPHQ = r"D:\work\AlphQ\src\aipha"
QT = r"D:\work\QT\quant-trading\python\aipha"
ALPHQ_SCRIPTS = r"D:\work\AlphQ\scripts"
QT_SCRIPTS = r"D:\work\QT\quant-trading\python\scripts"
ALPHQ_CONFIGS = r"D:\work\AlphQ\configs"
QT_CONFIGS = r"D:\work\QT\quant-trading\python\configs"
ALPHQ_TESTS = r"D:\work\AlphQ\tests"
QT_TESTS = r"D:\work\QT\quant-trading\python\tests"

try:
    import yaml
except ImportError:
    yaml = None

results = {}

# ═══════════════════════════════════════════════════════════
# 1. Python source file diff (aipha/)
# ═══════════════════════════════════════════════════════════
def collect_py(root):
    out = set()
    for dp, _, fns in os.walk(root):
        rel = os.path.relpath(dp, root).replace("\\", "/")
        if rel == ".":
            rel = ""
        for f in fns:
            if f.endswith(".py"):
                out.add(f"{rel}/{f}" if rel else f)
    return out

alphq_py = collect_py(ALPHQ)
qt_py = collect_py(QT)
missing_py = sorted(alphq_py - qt_py)
results["1_source_files"] = {
    "alphq": len(alphq_py),
    "qt": len(qt_py),
    "missing": len(missing_py),
    "missing_files": missing_py[:20],
}

# ═══════════════════════════════════════════════════════════
# 2. Module import verification (all aipha.*)
# ═══════════════════════════════════════════════════════════
import_ok = 0
import_fail = 0
import_errors = []
for dp, _, fns in os.walk("aipha"):
    rel = os.path.relpath(dp, ".").replace("\\", "/")
    pkg = rel.replace("/", ".")
    for f in sorted(fns):
        if not f.endswith(".py") or f.startswith("__"):
            continue
        mod = f"{pkg}.{f[:-3]}"
        try:
            importlib.import_module(mod)
            import_ok += 1
        except Exception as e:
            import_fail += 1
            import_errors.append(f"{mod}: {type(e).__name__}: {str(e)[:100]}")

results["2_imports"] = {
    "ok": import_ok,
    "fail": import_fail,
    "total": import_ok + import_fail,
    "errors": import_errors[:20],
}

# ═══════════════════════════════════════════════════════════
# 3. Configs integrity
# ═══════════════════════════════════════════════════════════
def collect_all(root):
    out = set()
    for dp, _, fns in os.walk(root):
        rel = os.path.relpath(dp, root).replace("\\", "/")
        if rel == ".":
            rel = ""
        for f in fns:
            out.add(f"{rel}/{f}" if rel else f)
    return out

alphq_cfg = collect_all(ALPHQ_CONFIGS)
qt_cfg = collect_all(QT_CONFIGS)
missing_cfg = sorted(alphq_cfg - qt_cfg)

cfg_parse_ok = 0
cfg_parse_fail = 0
cfg_errors = []
for dp, _, fns in os.walk(QT_CONFIGS):
    for f in fns:
        p = os.path.join(dp, f)
        try:
            if f.endswith(".json"):
                with open(p, "r", encoding="utf-8") as fh:
                    json.load(fh)
                cfg_parse_ok += 1
            elif f.endswith((".yaml", ".yml")) and yaml:
                with open(p, "r", encoding="utf-8") as fh:
                    yaml.safe_load(fh)
                cfg_parse_ok += 1
        except Exception as e:
            cfg_parse_fail += 1
            cfg_errors.append(f"{p}: {str(e)[:80]}")

results["3_configs"] = {
    "alphq": len(alphq_cfg),
    "qt": len(qt_cfg),
    "missing": len(missing_cfg),
    "parse_ok": cfg_parse_ok,
    "parse_fail": cfg_parse_fail,
    "errors": cfg_errors[:10],
}

# ═══════════════════════════════════════════════════════════
# 4. Tests integrity
# ═══════════════════════════════════════════════════════════
alphq_tests = collect_all(ALPHQ_TESTS)
qt_tests = collect_all(QT_TESTS)
missing_tests = sorted(alphq_tests - qt_tests)
# Only count .py files
alphq_tests_py = {f for f in alphq_tests if f.endswith(".py")}
qt_tests_py = {f for f in qt_tests if f.endswith(".py")}
missing_tests_py = sorted(alphq_tests_py - qt_tests_py)

test_syntax_ok = 0
test_syntax_fail = 0
test_errors = []
for dp, _, fns in os.walk(QT_TESTS):
    for f in fns:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dp, f)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                ast.parse(fh.read(), p)
            test_syntax_ok += 1
        except Exception as e:
            test_syntax_fail += 1
            test_errors.append(f"{f}: {str(e)[:80]}")

results["4_tests"] = {
    "alphq_py": len(alphq_tests_py),
    "qt_py": len(qt_tests_py),
    "missing_py": len(missing_tests_py),
    "syntax_ok": test_syntax_ok,
    "syntax_fail": test_syntax_fail,
    "errors": test_errors[:10],
}

# ═══════════════════════════════════════════════════════════
# 5. Scripts integrity
# ═══════════════════════════════════════════════════════════
alphq_scripts = {f for f in os.listdir(ALPHQ_SCRIPTS)
                 if f.endswith(".py") and os.path.isfile(os.path.join(ALPHQ_SCRIPTS, f))}
qt_scripts = {f for f in os.listdir(QT_SCRIPTS)
              if f.endswith(".py") and os.path.isfile(os.path.join(QT_SCRIPTS, f))}
missing_scripts = sorted(alphq_scripts - qt_scripts)

script_syntax_ok = 0
script_syntax_fail = 0
script_errors = []
for f in sorted(qt_scripts):
    p = os.path.join(QT_SCRIPTS, f)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            ast.parse(fh.read(), p)
        script_syntax_ok += 1
    except Exception as e:
        script_syntax_fail += 1
        script_errors.append(f"{f}: {str(e)[:80]}")

results["5_scripts"] = {
    "alphq": len(alphq_scripts),
    "qt": len(qt_scripts),
    "missing": len(missing_scripts),
    "missing_files": missing_scripts[:30],
    "syntax_ok": script_syntax_ok,
    "syntax_fail": script_syntax_fail,
    "errors": script_errors[:10],
}

# ═══════════════════════════════════════════════════════════
# 6. .env templates
# ═══════════════════════════════════════════════════════════
env_files = [".env.example", ".env.paper.example", ".env.research.example"]
env_status = {}
for ef in env_files:
    qt_path = os.path.join(r"D:\work\QT\quant-trading\python", ef)
    env_status[ef] = "OK" if os.path.exists(qt_path) else "MISSING"
results["6_env_templates"] = env_status

# ═══════════════════════════════════════════════════════════
# 7. Strategy registry check
# ═══════════════════════════════════════════════════════════
try:
    import aipha.strategy.register_all
    from aipha.strategy.registry import StrategyRegistry
    reg = StrategyRegistry()
    results["7_strategy_registry"] = {
        "registered": len(reg._strategies),
        "status": "OK",
    }
except Exception as e:
    results["7_strategy_registry"] = {"status": f"FAIL: {e}"}

# ═══════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════
print("=" * 65)
print("         AlphQ → QT Migration Integrity Report")
print("=" * 65)

r = results["1_source_files"]
status = "✅ PASS" if r["missing"] == 0 else f"❌ {r['missing']} MISSING"
print(f"\n1. Python Source (aipha/):  AlphQ={r['alphq']}  QT={r['qt']}  Missing={r['missing']}  {status}")
if r["missing_files"]:
    for f in r["missing_files"]:
        print(f"     ⚠ {f}")

r = results["2_imports"]
status = "✅ PASS" if r["fail"] == 0 else f"❌ {r['fail']} FAILED"
print(f"\n2. Module Imports:          OK={r['ok']} / {r['total']}  {status}")
if r["errors"]:
    for e in r["errors"][:5]:
        print(f"     ⚠ {e}")

r = results["3_configs"]
status = "✅ PASS" if r["missing"] == 0 and r["parse_fail"] == 0 else "⚠ ISSUES"
print(f"\n3. Configs:                 AlphQ={r['alphq']}  QT={r['qt']}  Missing={r['missing']}  Parse: {r['parse_ok']}/{r['parse_ok']+r['parse_fail']}  {status}")
if r["errors"]:
    for e in r["errors"][:5]:
        print(f"     ⚠ {e}")

r = results["4_tests"]
status = "✅ PASS" if r["missing_py"] == 0 and r["syntax_fail"] == 0 else "⚠ ISSUES"
print(f"\n4. Tests:                   AlphQ={r['alphq_py']}py  QT={r['qt_py']}py  Missing={r['missing_py']}  Syntax: {r['syntax_ok']}/{r['syntax_ok']+r['syntax_fail']}  {status}")
if r["errors"]:
    for e in r["errors"][:5]:
        print(f"     ⚠ {e}")

r = results["5_scripts"]
print(f"\n5. Scripts:                 AlphQ={r['alphq']}  QT={r['qt']}  Not-copied={r['missing']}  Syntax: {r['syntax_ok']}/{r['syntax_ok']+r['syntax_fail']}")
if r["missing_files"]:
    print(f"     (Intentionally excluded: {r['missing']} temp/diag/eval files)")

r = results["6_env_templates"]
env_ok = all(v == "OK" for v in r.values())
status = "✅ PASS" if env_ok else "❌ MISSING"
print(f"\n6. Env Templates:           {r}  {status}")

r = results["7_strategy_registry"]
status = "✅ PASS" if r.get("status") == "OK" else f"❌ {r.get('status')}"
print(f"\n7. Strategy Registry:       {r.get('registered', '?')} strategies  {status}")

print("\n" + "=" * 65)
all_pass = (
    results["1_source_files"]["missing"] == 0
    and results["2_imports"]["fail"] == 0
    and results["3_configs"]["parse_fail"] == 0
    and results["4_tests"]["syntax_fail"] == 0
    and results["5_scripts"]["syntax_fail"] == 0
    and env_ok
    and results["7_strategy_registry"].get("status") == "OK"
)
print(f"  OVERALL: {'✅ ALL CHECKS PASSED' if all_pass else '⚠ SOME ISSUES — see above'}")
print("=" * 65)
