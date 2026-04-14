"""合并所有KB版本的passed因子到统一知识库。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

all_passed = {}
for vdir in sorted(ROOT.joinpath("output").glob("auto_mining*")):
    kb = vdir / "mutation_kb.json"
    if not kb.exists():
        continue
    try:
        data = json.loads(kb.read_text(encoding="utf-8"))
    except Exception:
        continue
    for e in data:
        if (
            e.get("passed")
            and e.get("ic_mean", 0) >= 0.03
            and e.get("icir", 0) >= 0.40
        ):
            expr = e["expression"]
            if expr not in all_passed or e["icir"] > all_passed[expr]["icir"]:
                all_passed[expr] = e

merged = sorted(all_passed.values(), key=lambda x: x.get("icir", 0), reverse=True)
out_dir = ROOT / "output" / "auto_mining_merged"
out_dir.mkdir(exist_ok=True)
out_path = out_dir / "mutation_kb.json"
out_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

print(f"Total unique passed factors: {len(merged)}")
for e in merged[:10]:
    print(f"  {e['name']}: IC={e['ic_mean']:.4f} ICIR={e['icir']:.3f} | {e['expression'][:70]}")
