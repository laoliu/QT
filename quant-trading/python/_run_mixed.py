"""Quick smoke test: run mixed strategy and print formatted results."""
import json, os, sys, subprocess, time

here = os.path.dirname(os.path.abspath(__file__))
env = os.environ.copy()
env["QUANT_CONFIG"] = json.dumps({
    "action": "screen",
    "strategy": "mixed",
    "top_n": 8,
    "risk_profile": "moderate",
})

start = time.time()
proc = subprocess.run(
    [sys.executable, os.path.join(here, "scripts", "investment_planner.py")],
    capture_output=True, text=True, env=env, cwd=here, timeout=300,
)
elapsed = time.time() - start

if proc.returncode != 0:
    print(f"FAILED (exit={proc.returncode})")
    print(proc.stderr[-2000:] if proc.stderr else "(no stderr)")
    sys.exit(1)

d = json.loads(proc.stdout)

print(f"=== Mixed 策略试跑 ({elapsed:.1f}s) ===")
print(f"策略: {d.get('strategy','?')} | 风险: {d.get('riskProfile','?')}")
print(f"宏观周期: {d.get('macroRegime','?')} - {d.get('macroDescription','')}")
print(f"涨停池: {d.get('lianbanPoolCount',0)} | 热股池: {d.get('hotStocksCount',0)}")
pg = d.get("positionGuidance", {})
if pg:
    print(f"仓位指导: scale={pg.get('positionScale')}, 最大单仓={pg.get('maxSinglePositionPct')}, 最大持仓={pg.get('maxPositions')}")
    print(f"  调整说明: {pg.get('regimeAdjustment','')}")
print(f"\n候选数: {d.get('candidateCount',0)}")
for i, c in enumerate(d.get("candidates", []), 1):
    tags = []
    if c.get("lianban"):
        lb = c["lianban"]
        tags.append(f"连板{lb['board_count']}(质量{lb.get('quality_score',0):.0f})")
    if c.get("smartMoney"):
        sm = c["smartMoney"]
        tags.append(f"机构{sm['score']}")
    if c.get("sentiment"):
        st = c["sentiment"]
        tags.append(f"舆情{st['score']:.0f}({st.get('level','')})")
    if c.get("analystConsensus"):
        ac = c["analystConsensus"]
        tags.append(f"分析师{ac['buy']}/{ac['total']}看好")
    if c.get("earningsWarning"):
        tags.append(f"财报{c['earningsWarning']}")
    tag_str = " | ".join(tags) if tags else "-"
    reasons = c.get("reasons", [])[:6]
    print(f"  [{i}] {c['symbol']} {c['name']} score={c['score']} reasons={len(c.get('reasons',[]))}")
    print(f"       标签: {tag_str}")
    print(f"       理由: {', '.join(reasons)}")

print("\n=== 集成覆盖 ===")
cands = d.get("candidates", [])
print(f"  sentiment: {any(c.get('sentiment') for c in cands)}")
print(f"  smartMoney: {any(c.get('smartMoney') for c in cands)}")
print(f"  lianban: {any(c.get('lianban') for c in cands)}")
print(f"  analyst: {any(c.get('analystConsensus') for c in cands)}")
print(f"  positionGuidance: {bool(pg)}")
print(f"  earningsWarning: {any(c.get('earningsWarning') for c in cands)}")
print("\nDone.")
