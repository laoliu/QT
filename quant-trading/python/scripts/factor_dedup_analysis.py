"""因子去冗余分析脚本 (P1)

对训练数据中的全部因子进行冗余分析:
1. 从已训练模型提取 feature importance
2. 计算因子间 Spearman 相关矩阵
3. 识别冗余因子簇 (|ρ| ≥ 0.70)
4. 在每个簇内保留重要性最高的因子
5. 输出精简因子列表

用法:
    python scripts/factor_dedup_analysis.py \
        --model-dir models/ml_csi2000_lgb_20d_r40_mined \
        --threshold 0.70 \
        --output output/factor_dedup_analysis.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("factor_dedup")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="因子去冗余分析")
    p.add_argument("--model-dir", required=True,
                   help="训练好的模型目录 (含 evaluation_report.json)")
    p.add_argument("--threshold", type=float, default=0.70,
                   help="相关系数阈值 (|ρ| >= threshold 视为冗余)")
    p.add_argument("--output", default="output/factor_dedup_analysis.json",
                   help="输出 JSON 路径")
    p.add_argument("--top-n", type=int, default=0,
                   help="仅保留 top N 重要因子 (0=自动)")
    return p.parse_args()


def load_feature_importance(model_dir: Path) -> dict[str, float]:
    """从 evaluation_report.json 提取 feature importance。"""
    report_path = model_dir / "evaluation_report.json"
    if not report_path.exists():
        logger.warning("未找到 evaluation_report.json")
        return {}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    wf = report.get("wf_summary", {})
    return wf.get("top_features", {})


def load_feature_names_from_folds(model_dir: Path) -> list[str]:
    """从 LightGBM 模型文件提取特征名。"""
    fold_file = model_dir / "fold_0.txt"
    if not fold_file.exists():
        return []
    with open(fold_file, "r") as f:
        for line in f:
            if line.startswith("feature_names="):
                return line.strip().split("=", 1)[1].split()
    return []


def compute_correlation_clusters(
    corr_matrix: np.ndarray,
    feature_names: list[str],
    threshold: float,
) -> list[list[str]]:
    """使用 union-find 将 |ρ| >= threshold 的因子归入同一簇。"""
    n = len(feature_names)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if abs(corr_matrix[i, j]) >= threshold:
                union(i, j)

    clusters: dict[int, list[str]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(feature_names[i])

    return [members for members in clusters.values() if len(members) > 1]


def select_best_per_cluster(
    clusters: list[list[str]],
    importance: dict[str, float],
) -> tuple[list[str], list[str]]:
    """每个簇保留重要性最高的因子。

    Returns:
        (keep_list, drop_list)
    """
    keep = []
    drop = []
    for cluster in clusters:
        # 按 importance 排序，最高的保留
        ranked = sorted(
            cluster,
            key=lambda f: importance.get(f, 0),
            reverse=True,
        )
        keep.append(ranked[0])
        drop.extend(ranked[1:])
    return keep, drop


def main() -> None:
    args = parse_args()
    model_dir = Path(args.model_dir)

    # 1) 加载特征名和重要性
    feature_names = load_feature_names_from_folds(model_dir)
    importance = load_feature_importance(model_dir)

    if not feature_names:
        logger.error("无法从模型中提取特征名")
        sys.exit(1)

    logger.info("特征总数: %d", len(feature_names))
    logger.info("有重要性的特征: %d", len(importance))

    # 扩展重要性: 没有记录的特征赋值 0
    for f in feature_names:
        if f not in importance:
            importance[f] = 0.0

    # 2) 计算相关矩阵 (使用模型 OOS predictions 或训练数据)
    # 先尝试从模型 oos_predictions 加载特征值
    oos_path = model_dir / "oos_predictions.parquet"
    if oos_path.exists():
        logger.info("从 OOS predictions 加载数据...")
        oos_df = pd.read_parquet(oos_path)
        avail_cols = [f for f in feature_names if f in oos_df.columns]
        if len(avail_cols) > 10:
            logger.info("可用特征列: %d", len(avail_cols))
            corr = oos_df[avail_cols].corr(method="spearman").values
            feature_names_for_corr = avail_cols
        else:
            logger.warning("OOS 中特征列不足，跳过相关性分析")
            corr = None
            feature_names_for_corr = []
    else:
        logger.warning("无 OOS predictions 文件，跳过相关性分析")
        corr = None
        feature_names_for_corr = []

    # 3) 识别冗余簇
    if corr is not None:
        clusters = compute_correlation_clusters(
            corr, feature_names_for_corr, args.threshold,
        )
        logger.info("冗余簇: %d (阈值 |ρ|≥%.2f)", len(clusters), args.threshold)
        keep, drop = select_best_per_cluster(clusters, importance)
    else:
        clusters = []
        keep = []
        drop = []

    # 4) 按重要性排序保留的因子
    all_keep = [f for f in feature_names if f not in drop]
    all_keep.sort(key=lambda f: importance.get(f, 0), reverse=True)

    if args.top_n > 0:
        final_features = all_keep[: args.top_n]
    else:
        final_features = all_keep

    # 5) 生成报告
    result = {
        "total_features": len(feature_names),
        "redundant_clusters": len(clusters),
        "dropped_features": len(drop),
        "remaining_features": len(final_features),
        "threshold": args.threshold,
        "clusters": [
            {"members": c, "kept": c[0] if c else ""}
            for c in clusters
        ],
        "dropped": sorted(drop),
        "final_features": final_features,
        "top_20_by_importance": [
            {"name": f, "importance": importance.get(f, 0)}
            for f in all_keep[:20]
        ],
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("=" * 50)
    logger.info("去冗余分析完成:")
    logger.info("  总特征: %d", result["total_features"])
    logger.info("  冗余簇: %d", result["redundant_clusters"])
    logger.info("  丢弃: %d", result["dropped_features"])
    logger.info("  保留: %d", result["remaining_features"])
    logger.info("  结果保存: %s", out_path)

    # 打印冗余簇详情
    for i, c in enumerate(clusters[:10]):
        imp_info = [(f, importance.get(f, 0)) for f in c]
        imp_info.sort(key=lambda x: x[1], reverse=True)
        kept = imp_info[0][0]
        dropped = [f for f, _ in imp_info[1:]]
        logger.info(
            "  簇 %d: 保留 %s (imp=%.1f), 丢弃 %d 个",
            i, kept, imp_info[0][1], len(dropped),
        )


if __name__ == "__main__":
    main()


