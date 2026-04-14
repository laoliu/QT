"""R126 组合回测 — P1 因子增强 CSI2000 对比.

对比:
  R126_P1:  用 ml_csi2000_lgb_p1 (6因子增强) 替换原 CSI2000 成分
  R124v2:   基准 (ml_csi2000_lgb_fund)

权重均为 CF-001: ML2000=50%, ML1000(old)=15%, CB=5%, ETF=30%
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from combo_r123_backtest import (  # noqa: E402
    load_nav,
    align_navs,
    synthesize_combo,
    compute_metrics,
    compute_segmented_metrics,
    compute_correlation_matrix,
    statistical_tests,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("combo_r126")

# 子策略 NAV 路径
NAV_PATHS_BASELINE = {
    "ml_csi2000": ROOT / "output/combo_r115_faa/ml_csi2000_fund/daily_nav.csv",
    "ml_csi1000_old": ROOT / "output/risk_controlled_csi1000_lgb_fund/daily_nav.csv",
    "cb": ROOT / "output/combo_r115_faa/cb_stock_quality/daily_snapshots.csv",
    "etf": ROOT / "output/combo_r115_faa/etf_cross_v3/daily_snapshots.csv",
}

NAV_PATHS_P1 = {
    "ml_csi2000": ROOT / "output/risk_controlled_csi2000_lgb_p1/daily_nav.csv",
    "ml_csi1000_old": ROOT / "output/risk_controlled_csi1000_lgb_fund/daily_nav.csv",
    "cb": ROOT / "output/combo_r115_faa/cb_stock_quality/daily_snapshots.csv",
    "etf": ROOT / "output/combo_r115_faa/etf_cross_v3/daily_snapshots.csv",
}

W_CF001 = {
    "ml_csi2000": ("ml_csi2000", 0.50),
    "ml_csi1000": ("ml_csi1000_old", 0.15),
    "cb": ("cb", 0.05),
    "etf": ("etf", 0.30),
}


def main() -> None:
    """入口."""
    t0 = time.time()
    logger.info("=" * 70)
    logger.info("R126 组合回测 — P1 因子增强 CSI2000 对比")
    logger.info("=" * 70)

    # 1. 加载子策略 NAV
    configs = {
        "R124v2_baseline": NAV_PATHS_BASELINE,
        "R126_P1": NAV_PATHS_P1,
    }

    all_results: dict[str, dict] = {}
    combo_navs: dict[str, pd.Series] = {}
    for combo_name, nav_paths in configs.items():
        logger.info("\n--- %s ---", combo_name)
        raw: dict[str, pd.Series] = {}
        for name, path in nav_paths.items():
            if not path.exists():
                logger.error("  NAV 文件不存在: %s", path)
                return
            raw[name] = load_nav(path)
            s = raw[name]
            logger.info("  %s: %d days, %s~%s",
                         name, len(s), s.index[0].date(), s.index[-1].date())

        aligned = align_navs(raw)
        nav = synthesize_combo(aligned, W_CF001)
        combo_navs[combo_name] = nav
        m = compute_metrics(nav)
        seg = compute_segmented_metrics(nav)
        st = statistical_tests(nav, n_strategies=10)
        all_results[combo_name] = {
            "metrics": m, "segmented": seg, "statistical": st,
        }
        logger.info(
            "  >> AR=%.1f%%, SR=%.2f, MDD=%.1f%%, Calmar=%.2f",
            m["annualized_return"] * 100, m["sharpe_ratio"],
            m["max_drawdown"] * 100, m["calmar_ratio"],
        )

    # 2. 对比总结
    logger.info("\n" + "=" * 80)
    logger.info("%-20s %8s %8s %8s %8s %8s",
                "", "AR", "SR", "MDD", "Calmar", "PostAR")
    logger.info("-" * 72)
    for cname, res in all_results.items():
        m = res["metrics"]
        post = res["segmented"].get("post_reform", {})
        post_ar = post.get("annualized_return", 0) * 100 if post else 0
        logger.info(
            "%-20s %7.1f%% %8.2f %7.1f%% %8.2f %7.1f%%",
            cname, m["annualized_return"] * 100, m["sharpe_ratio"],
            m["max_drawdown"] * 100, m["calmar_ratio"], post_ar,
        )

    # 3. 保存
    out_dir = ROOT / "output" / "combo_r126"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for cname, res in all_results.items():
        summary[cname] = res["metrics"]
        nav_s = combo_navs[cname]
        nav_df = pd.DataFrame({"date": nav_s.index, "nav": nav_s.values})
        nav_df.to_csv(out_dir / f"{cname}_daily_nav.csv", index=False)

    with open(out_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info("\n结果已保存: %s", out_dir)
    logger.info("耗时: %.1f秒", time.time() - t0)


if __name__ == "__main__":
    main()
