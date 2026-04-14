"""从 merged KB 中筛选非冗余因子子集。

基于 P0 训练结果的分析，排除与基线 size/liquidity 因子高度冗余的 mined factors。
产出精选 KB 用于 P1 retrain。

用法:
    python scripts/curate_mined_factors.py
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 冗余模式：表达式仅包含 size/liquidity 变量且无新信息
SIZE_LIQUIDITY_PATTERNS = [
    r"^\s*cs_rank\s*\(\s*-?\s*\$amount\s*\)",           # pure amount rank
    r"^\s*cs_rank\s*\(\s*-?\s*\$total_mv\s*\)",         # pure total_mv rank
    r"^\s*cs_rank\s*\(\s*-?\s*\$circ_mv\s*\)",          # pure circ_mv rank
    r"^\s*cs_rank\s*\(\s*-?\s*ts_mean\s*\(\s*\$total_mv",  # smoothed total_mv
    r"^\s*cs_rank\s*\(\s*-?\s*ts_mean\s*\(\s*\$amount",    # smoothed amount
    r"^\s*cs_rank\s*\(\s*safe_div\s*\(\s*1\.0\s*,\s*ts_abs\s*\(\s*\$total_mv",  # inverse mv
    r"^\s*cs_rank\s*\(\s*-?\s*ts_mean\s*\(\s*\$turnover_rate",  # smoothed turnover
]


def is_size_redundant(expression: str) -> bool:
    """判断表达式是否仅是 size/liquidity 变量的简单变换。"""
    for pattern in SIZE_LIQUIDITY_PATTERNS:
        if re.search(pattern, expression, re.IGNORECASE):
            return True
    return False


def main():
    kb_path = ROOT / "output" / "auto_mining_merged" / "mutation_kb.json"
    kb = json.load(open(kb_path, encoding="utf-8"))

    passed = [e for e in kb if e.get("passed")
              and e.get("ic_mean", 0) >= 0.03
              and e.get("icir", 0) >= 0.40]
    passed.sort(key=lambda e: e.get("icir", 0), reverse=True)

    kept = []
    excluded = []
    for entry in passed:
        if is_size_redundant(entry["expression"]):
            excluded.append(entry)
        else:
            kept.append(entry)

    print(f"Total passed: {len(passed)}")
    print(f"Excluded (size/liquidity redundant): {len(excluded)}")
    print(f"Kept (novel signal): {len(kept)}")

    print("\n--- Excluded factors ---")
    for e in excluded[:20]:
        print(f"  {e['name']}: ICIR={e['icir']:.3f}  {e['expression']}")

    print(f"\n--- Top 20 kept factors ---")
    for e in kept[:20]:
        print(f"  {e['name']}: ICIR={e['icir']:.3f}  {e['expression']}")

    # Save curated KB
    out_path = ROOT / "output" / "auto_mining_merged" / "mutation_kb_curated.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(kept, open(out_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"\nCurated KB saved: {out_path} ({len(kept)} factors)")


if __name__ == "__main__":
    main()
