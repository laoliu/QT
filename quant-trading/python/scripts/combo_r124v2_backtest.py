"""R124v2 组合回测 — CF-001 合规权重 (50/15/5/30).

对比方案:
  R124v2 (CF-001): ML2000=50%, ML1000(Old)=15%, CB=5%, ETF=30%
  R124v1 (原始):   ML2000=40%, ML1000(Old)=10%, CB=25%, ETF=25%
  R116 (基准):     ML2000=35%, ML1000(Old)=15%, CB=25%, ETF=25%

复用 combo_r123_backtest.py 的核心函数。
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
logger = logging.getLogger("combo_r124v2")

NAV_PATHS = {
    "ml_csi2000": ROOT / "output/combo_r115_faa/ml_csi2000_fund/daily_nav.csv",
    "ml_csi1000_old": ROOT / "output/risk_controlled_csi1000_lgb_fund/daily_nav.csv",
    "cb": ROOT / "output/combo_r115_faa/cb_stock_quality/daily_snapshots.csv",
    "etf": ROOT / "output/combo_r115_faa/etf_cross_v3/daily_snapshots.csv",
}

COMBOS = {
    "R124v2_CF001": {
        "ml_csi2000": ("ml_csi2000", 0.50),
        "ml_csi1000": ("ml_csi1000_old", 0.15),
        "cb": ("cb", 0.05),
        "etf": ("etf", 0.30),
    },
    "R124v1": {
        "ml_csi2000": ("ml_csi2000", 0.40),
        "ml_csi1000": ("ml_csi1000_old", 0.10),
        "cb": ("cb", 0.25),
        "etf": ("etf", 0.25),
    },
    "R116_baseline": {
        "ml_csi2000": ("ml_csi2000", 0.35),
        "ml_csi1000": ("ml_csi1000_old", 0.15),
        "cb": ("cb", 0.25),
        "etf": ("etf", 0.25),
    },
}


def main() -> None:
    """入口."""
    t0 = time.time()
    logger.info("=" * 70)
    logger.info("R124v2 组合回测 — CF-001 合规权重验证")
    logger.info("=" * 70)

    # 1. 加载子策略 NAV
    logger.info("Step 1: 加载子策略 NAV...")
    raw_navs: dict[str, pd.Series] = {}
    for name, path in NAV_PATHS.items():
        raw_navs[name] = load_nav(path)
        s = raw_navs[name]
        logger.info("  %s: %d days, %s~%s",
                     name, len(s), s.index[0].date(), s.index[-1].date())

    # 2. 对齐日期
    logger.info("Step 2: 对齐日期...")
    aligned = align_navs(raw_navs)
    n = len(next(iter(aligned.values())))
    d0 = next(iter(aligned.values())).index[0].date()
    d1 = next(iter(aligned.values())).index[-1].date()
    logger.info("  公共: %d天, %s ~ %s", n, d0, d1)

    # 3. 合成组合 NAV
    logger.info("Step 3: 合成组合...")
    combo_navs: dict[str, pd.Series] = {}
    for cname, weights in COMBOS.items():
        combo_navs[cname] = synthesize_combo(aligned, weights)

    # 4. 计算绩效
    logger.info("Step 4: 计算绩效指标...")
    all_results: dict[str, dict] = {}
    for cname, nav in combo_navs.items():
        m = compute_metrics(nav)
        seg = compute_segmented_metrics(nav)
        st = statistical_tests(nav, n_strategies=10)
        all_results[cname] = {
            "metrics": m, "segmented": seg, "statistical": st,
        }
        logger.info(
            "  %s: AR=%.1f%%, SR=%.2f, MDD=%.1f%%, Calmar=%.2f",
            cname, m["annualized_return"] * 100, m["sharpe_ratio"],
            m["max_drawdown"] * 100, m["calmar_ratio"],
        )

    # 5. 子策略相关性
    corr = compute_correlation_matrix(aligned)

    # 6. 打印对比表
    _print_comparison(all_results)

    # 7. 保存结果
    _save(all_results, combo_navs, corr, d0, d1, n, t0)
    logger.info("耗时: %.1f秒", time.time() - t0)


def _print_comparison(results: dict[str, dict]) -> None:
    """打印对比表."""
    logger.info("\n" + "=" * 80)
    logger.info("R124v2 组合对比 (CF-001 合规验证)")
    logger.info("=" * 80)
    h = (f"{'Combo':<20} {'AR':>6} {'SR':>5} {'MDD':>7} "
         f"{'Calmar':>7} {'Win':>5} {'MedYR':>7}")
    logger.info(h)
    logger.info("-" * 80)
    for name, res in results.items():
        m = res["metrics"]
        logger.info(
            f"{name:<20} {m['annualized_return']*100:>5.1f}% "
            f"{m['sharpe_ratio']:>5.2f} {m['max_drawdown']*100:>6.1f}% "
            f"{m['calmar_ratio']:>7.2f} {m['win_years']:>5} "
            f"{m['median_yearly_return']*100:>6.1f}%"
        )
    # Post-2022
    logger.info("\n分段 (Post 2022-08):")
    for name, res in results.items():
        seg = res["segmented"]
        if "post_2022aug" in seg:
            p = seg["post_2022aug"]
            logger.info(
                "  %s: AR=%.1f%%, SR=%.2f, MDD=%.1f%%",
                name, p["annualized_return"] * 100,
                p["sharpe_ratio"], p["max_drawdown"] * 100,
            )
    # 统计检验
    logger.info("\n统计检验:")
    for name, res in results.items():
        st = res["statistical"]
        logger.info(
            "  %s: DSR=%.3f(p=%.4f,%s) HLZ=%.3f(%s)",
            name, st["dsr_statistic"], st["dsr_pvalue"],
            "OK" if st["dsr_significant"] else "X",
            st["hlz_t_adj"],
            "OK" if st["hlz_significant"] else "X",
        )
    # 逐年对比
    logger.info("\n逐年收益:")
    for name, res in results.items():
        yr = res["metrics"]["yearly_returns"]
        yrs = " ".join(f"{k}:{v*100:.1f}%" for k, v in sorted(yr.items()))
        logger.info("  %s: %s", name, yrs)


def _save(
    results: dict, combo_navs: dict, corr: pd.DataFrame,
    d0: object, d1: object, n_common: int, t0: float,
) -> None:
    """保存结果."""
    out_dir = ROOT / "output" / "combo_r124v2"
    out_dir.mkdir(parents=True, exist_ok=True)

    # NAV CSV
    for name, nav in combo_navs.items():
        nav_df = pd.DataFrame({"date": nav.index, "nav": nav.values})
        nav_df.to_csv(out_dir / f"{name}_daily_nav.csv", index=False)

    # Summary JSON
    summary = {
        "timestamp": pd.Timestamp.now().isoformat(),
        "common_period": f"{d0} ~ {d1}",
        "common_days": n_common,
        "combos": results,
        "correlation_matrix": corr.round(4).to_dict(),
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    logger.info("结果已保存: %s", out_dir)


if __name__ == "__main__":
    main()
