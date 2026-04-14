"""R127 P2 因子 IC/IR 验证 — CSI2000 历史面板.

验证 5 个 P2 基本面/质量因子在 CSI2000 universe 的月频 Rank IC / ICIR:
  1. SUE (标准化意外盈利)      — Ball & Brown 1968, IC预期 3-5%
  2. Accruals (应计质量)       — Sloan 1996, IC预期 2-4%
  3. CFTP (现金流收益率)       — Lakonishok 1994, IC预期 3-5%
  4. ROE Momentum (ROE变化)   — Novy-Marx, IC预期 2-4%
  5. Turnover Stability (换手稳定性) — IC预期 2-3%

复用 P0 验证脚本的数据加载 + IC 计算框架。
输出: output/factor_ic_p2_validation.json
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("p2_factor_ic")

from train_ml_factor import (  # noqa: E402
    load_daily_data,
    filter_universe,
    _load_fina_indicator_bulk,
    _pit_merge_fina,
)
from p0_factor_ic_validation import (  # noqa: E402
    compute_monthly_ic,
    segment_ic_stats,
)
from aipha.factors.p2_factors_ml import add_p2_factors  # noqa: E402

# ── 常量 ──
START = "2015-01-05"
END = "2026-02-28"
FORWARD_DAYS = [5, 10, 20]
SEG_CUTOFF = pd.Timestamp("2022-08-01")
IC_THRESHOLD = 0.02
ICIR_THRESHOLD = 0.3

# P2 原始因子名 (不含 cs_rank_ 前缀)
P2_RAW_NAMES = ["sue", "accruals", "cftp", "roe_momentum", "turnover_stability"]


def _print_summary(results: dict[str, Any]) -> None:
    """打印汇总表."""
    logger.info("\n" + "=" * 80)
    logger.info("P2 基本面因子 IC/IR 汇总 (CSI2000, %s ~ %s)", START, END)
    logger.info("=" * 80)
    header = (f"{'Factor':<22} {'Fwd':>5} {'IC':>7} {'ICIR':>6} "
              f"{'IC+%':>6} {'t':>6} {'p':>6} {'Pass':>5}")
    logger.info(header)
    logger.info("-" * 80)
    for fname, fwd_dict in results.items():
        for fwd_key, seg in fwd_dict.items():
            f = seg["full"]
            ic_ok = abs(f["ic_mean"]) >= IC_THRESHOLD
            ir_ok = abs(f["icir"]) >= ICIR_THRESHOLD
            passed = "YES" if (ic_ok and ir_ok) else "NO"
            logger.info(
                f"{fname:<22} {fwd_key:>5} {f['ic_mean']:>7.4f} "
                f"{f['icir']:>6.3f} {f['ic_pos_rate']*100:>5.1f}% "
                f"{f['t_stat']:>6.2f} {f['p_value']:>6.4f} {passed:>5}"
            )
    # 分段对比 (post-2022)
    logger.info("\n分段对比 (Post 2022-08, fwd=5d):")
    for fname, fwd_dict in results.items():
        if "fwd_5d" in fwd_dict:
            post = fwd_dict["fwd_5d"].get("post_2022aug", {})
            if post.get("n_months", 0) > 0:
                logger.info(
                    "  %s: IC=%.4f, ICIR=%.3f, n=%d",
                    fname, post["ic_mean"], post["icir"],
                    post["n_months"],
                )


def _save_results(results: dict[str, Any], elapsed: float) -> None:
    """保存 JSON 结果."""
    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    output = {
        "analysis_type": "P2_factor_IC_validation",
        "universe": "csi2000",
        "period": f"{START} ~ {END}",
        "segment_cutoff": str(SEG_CUTOFF.date()),
        "forward_days": FORWARD_DAYS,
        "ic_threshold": IC_THRESHOLD,
        "icir_threshold": ICIR_THRESHOLD,
        "elapsed_seconds": round(elapsed, 1),
        "results": results,
        "summary": {},
    }
    passed, marginal, failed = [], [], []
    for fname, fwd_dict in results.items():
        if "fwd_5d" not in fwd_dict:
            continue
        f = fwd_dict["fwd_5d"]["full"]
        ic_ok = abs(f["ic_mean"]) >= IC_THRESHOLD
        ir_ok = abs(f["icir"]) >= ICIR_THRESHOLD
        if ic_ok and ir_ok:
            passed.append(fname)
        elif ic_ok or ir_ok:
            marginal.append(fname)
        else:
            failed.append(fname)
    output["summary"] = {
        "passed": passed, "marginal": marginal, "failed": failed,
    }
    out_path = out_dir / "factor_ic_p2_validation.json"
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(output, fp, indent=2, ensure_ascii=False, default=str)
    logger.info("结果已保存: %s", out_path)


def main() -> None:
    """入口."""
    t0 = time.time()
    logger.info("=" * 70)
    logger.info("R127 P2 因子 IC/IR 验证 — CSI2000 历史面板")
    logger.info("=" * 70)

    # 1. 加载数据
    logger.info("Step 1: 加载全市场日线数据 %s ~ %s ...", START, END)
    df = load_daily_data(START, END)
    logger.info("  全市场: %d 行, %d 股票", len(df), df["symbol"].nunique())

    # 2. 筛选 CSI2000
    logger.info("Step 2: 筛选 CSI2000 universe...")
    df = filter_universe(df, "csi2000")
    logger.info("  CSI2000: %d 行, %d 股票", len(df), df["symbol"].nunique())

    # 3. 加载财务数据 (PIT)
    logger.info("Step 3: 加载 fina_indicator (PIT)...")
    cache_dir = ROOT / "data" / "cache" / "equity_tushare"
    symbols = df["symbol"].unique().tolist()
    fina = _load_fina_indicator_bulk(cache_dir, symbols)
    df = _pit_merge_fina(df, fina)
    fina_cols = [
        "roe", "cfps", "eps", "dt_netprofit_yoy",
        "roa", "grossprofit_margin",
    ]
    for col in fina_cols:
        if col in df.columns:
            df[col] = df.groupby("symbol")[col].ffill(limit=60)
    df = df.sort_values(["symbol", "datetime"]).reset_index(drop=True)

    # 4. 计算前瞻收益
    logger.info("Step 4: 计算前瞻收益 (forward days: %s)...", FORWARD_DAYS)
    for fd in FORWARD_DAYS:
        col = f"fwd_{fd}d"
        df[col] = df.groupby("symbol")["close"].transform(
            lambda s: s.shift(-fd) / s - 1.0
        )

    # 5. 计算 P2 因子 (使用模块函数)
    logger.info("Step 5: 计算 5 个 P2 基本面因子...")
    n_cols = add_p2_factors(df)
    logger.info("  P2 模块返回 %d 列", n_cols)
    for fname in P2_RAW_NAMES:
        if fname in df.columns:
            valid = df[fname].notna().sum()
            logger.info("    %s: %d 有效值 (%.1f%%)",
                         fname, valid, 100 * valid / len(df))

    # 6. 月频 IC 计算
    logger.info("Step 6: 计算月频 Rank IC...")
    results: dict[str, Any] = {}
    for fname in P2_RAW_NAMES:
        if fname not in df.columns:
            logger.warning("  %s 未生成, 跳过", fname)
            continue
        results[fname] = {}
        for fd in FORWARD_DAYS:
            fwd_col = f"fwd_{fd}d"
            ic_recs = compute_monthly_ic(df, fname, fwd_col)
            seg = segment_ic_stats(ic_recs)
            results[fname][f"fwd_{fd}d"] = seg
            full = seg["full"]
            logger.info(
                "  %s (T+%d): IC=%.4f, ICIR=%.3f, IC+%%=%.1f%%, "
                "t=%.2f, p=%.4f, n=%d",
                fname, fd, full["ic_mean"], full["icir"],
                full["ic_pos_rate"] * 100, full["t_stat"],
                full["p_value"], full["n_months"],
            )

    # 7. 输出汇总
    _print_summary(results)
    _save_results(results, time.time() - t0)
    logger.info("耗时: %.1f 秒", time.time() - t0)


if __name__ == "__main__":
    main()
