"""回填因子挖掘历史数据到 SQLite — 一次性脚本.

将 mutation_kb.json + evaluation_report.json + independent_factors.json
的历史数据导入 MiningStore 4 张表。

用法:
    python scripts/backfill_mining_db.py --kb output/auto_mining_v6/mutation_kb.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aipha.factors.mining_store import MiningStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill")


def backfill_mined_factors(store: MiningStore, kb_path: Path) -> int:
    """回填 mined_factors 表."""
    if not kb_path.exists():
        logger.error("KB 文件不存在: %s", kb_path)
        return 0
    entries = json.loads(kb_path.read_text("utf-8"))
    count = 0
    for e in entries:
        name = e.get("name", "")
        store.upsert_mined_factor({
            "factor_id": name,
            "name": name,
            "expression": e.get("expression", ""),
            "category": e.get("category", "mutation"),
            "method": e.get("method", ""),
            "ic_mean": e.get("ic_mean", 0),
            "icir": e.get("icir", 0),
            "ic_cv": e.get("ic_cv", 0),
            "post_ic": e.get("post_ic", 0),
            "t_stat": 0,
            "fitness": e.get("fitness", 0),
            "passed": e.get("passed", False),
            "failure_reason": e.get("failure_reason", ""),
            "round_id": e.get("round_id", 0),
            "kb_path": str(kb_path),
            "discovered_at": e.get("discovered_at", ""),
        })
        count += 1
    logger.info("mined_factors: 回填 %d 条", count)
    return count


def backfill_eval_run(
    store: MiningStore, report_path: Path, kb_path: Path,
) -> str:
    """回填 eval_runs 表, 返回 eval_id."""
    if not report_path.exists():
        logger.error("报告文件不存在: %s", report_path)
        return ""
    report = json.loads(report_path.read_text("utf-8"))
    cfg = report.get("config", {})
    eval_id = (
        f"backfill_{kb_path.stem}"
        f"_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    store.upsert_eval_run({
        "eval_id": eval_id,
        "kb_path": str(kb_path),
        "universe": cfg.get("universe", "csi2000"),
        "start_date": cfg.get("start", ""),
        "end_date": cfg.get("end", ""),
        "fwd_days": cfg.get("fwd_days", 10),
        "n_passed": report.get("n_passed", 0),
        "n_computed": report.get("n_computed", 0),
        "n_clusters": report.get("n_clusters", 0),
        "n_independent": report.get("n_independent", 0),
        "run_time_s": report.get("run_time_s", 0),
        "config_json": cfg,
    })
    logger.info("eval_runs: 写入 eval_id=%s", eval_id)
    return eval_id


def backfill_eval_factors(
    store: MiningStore, indep_path: Path,
    report_path: Path, eval_id: str,
) -> int:
    """回填 eval_factors 表."""
    if not indep_path.exists():
        logger.error("独立因子文件不存在: %s", indep_path)
        return 0
    evaluations = json.loads(indep_path.read_text("utf-8"))
    # 获取 cluster 信息
    clusters: dict[int, list[str]] = {}
    if report_path.exists():
        report = json.loads(report_path.read_text("utf-8"))
        cd = report.get("cluster_detail", {})
        clusters = {int(k): v for k, v in cd.items()}
    reps = set()
    if report_path.exists():
        report = json.loads(report_path.read_text("utf-8"))
        reps = set(report.get("independent_factors", []))
    name_to_cluster: dict[str, int] = {}
    for cid, members in clusters.items():
        for m in members:
            name_to_cluster[m] = cid
    count = 0
    for ev in evaluations:
        name = ev.get("name", "")
        store.upsert_eval_factor({
            "eval_id": eval_id,
            "factor_name": name,
            "expression": ev.get("expression", ""),
            "kb_icir": ev.get("kb_icir", 0),
            "kb_ic_mean": ev.get("kb_ic_mean", 0),
            "composite_score": ev.get("composite_score", 0),
            "monotonicity": ev.get("quantile", {}).get(
                "monotonicity", 0),
            "avg_turnover": ev.get("turnover", {}).get(
                "avg_turnover", 0),
            "wf_oos_icir": ev.get("walk_forward", {}).get(
                "oos_icir", 0),
            "wf_n_folds": ev.get("walk_forward", {}).get(
                "n_folds", 0),
            "ic_decay_json": ev.get("ic_decay"),
            "quantile_json": ev.get("quantile"),
            "cluster_id": name_to_cluster.get(name, -1),
            "is_representative": name in reps if reps else True,
        })
        count += 1
    logger.info("eval_factors: 回填 %d 条", count)
    return count


def main() -> None:
    p = argparse.ArgumentParser(description="回填因子挖掘历史数据")
    p.add_argument("--kb",
                   default="output/auto_mining_v6/mutation_kb.json")
    p.add_argument("--db", default=None, help="DB 路径 (默认: 项目 DB)")
    args = p.parse_args()

    kb_path = Path(args.kb)
    out_dir = kb_path.parent
    report_path = out_dir / "evaluation_report.json"
    indep_path = out_dir / "independent_factors.json"

    store = MiningStore(db_path=args.db) if args.db else MiningStore()
    logger.info("DB: %s", store.db_path)

    # 1. 回填 mined_factors
    n_mined = backfill_mined_factors(store, kb_path)

    # 2. 回填 eval_runs
    eval_id = ""
    if report_path.exists():
        eval_id = backfill_eval_run(store, report_path, kb_path)

    # 3. 回填 eval_factors
    n_eval = 0
    if eval_id and indep_path.exists():
        n_eval = backfill_eval_factors(
            store, indep_path, report_path, eval_id)

    # 4. 验证
    stats = store.get_dashboard_stats()
    logger.info("回填完成! Dashboard: %s", stats)
    logger.info("  mined=%d, passed=%d, eval_runs=%d, independent=%d",
                stats["total_mined"], stats["total_passed"],
                stats["total_eval_runs"], stats["total_independent"])


if __name__ == "__main__":
    main()
