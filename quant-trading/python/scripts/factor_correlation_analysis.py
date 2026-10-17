"""R125 偏相关分析: Amihud vs IVOL 独立性验证.

目的: 确认两个最强因子 (Amihud IC=8.57%, IVOL IC=8.64%) 是否提供独立alpha,
还是仅因流动性/波动率维度重叠而冗余。

方法: 计算 6 个通过因子的截面相关矩阵 + 控制变量偏相关。
输出: output/factor_correlation_analysis.json
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
from scipy import stats as sp_stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("factor_corr")

from train_ml_factor import load_daily_data, filter_universe
from train_ml_factor import _load_fina_indicator_bulk, _pit_merge_fina
from p0_factor_ic_validation import compute_factor_amihud, compute_factor_reversal_3m
from p0_factor_ic_validation import compute_factor_value_prof
from p1_factor_ic_validation import compute_factor_ivol, compute_factor_skewness
from p1_factor_ic_validation import compute_factor_52wk_high

START, END = "2015-01-05", "2026-02-28"


def partial_corr(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> float:
    """偏相关: corr(x, y | z)."""
    mask = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    x, y, z = x[mask], y[mask], z[mask]
    if len(x) < 30:
        return np.nan
    # 回归残差法
    from numpy.polynomial.polynomial import polyfit
    cx = np.polyfit(z, x, 1)
    cy = np.polyfit(z, y, 1)
    rx = x - np.polyval(cx, z)
    ry = y - np.polyval(cy, z)
    return float(np.corrcoef(rx, ry)[0, 1])


def main() -> None:
    t0 = time.time()
    logger.info("=" * 60)
    logger.info("R125 因子偏相关分析")
    logger.info("=" * 60)

    # 1. 加载数据
    df = load_daily_data(START, END)
    df = filter_universe(df, "csi2000")
    cache_dir = ROOT / "data" / "cache" / "equity_tushare"
    symbols = df["symbol"].unique().tolist()
    fina = _load_fina_indicator_bulk(cache_dir, symbols)
    df = _pit_merge_fina(df, fina)
    for col in ["grossprofit_margin", "netprofit_margin", "roe",
                 "eps", "bps", "dt_netprofit_yoy"]:
        if col in df.columns:
            df[col] = df.groupby("symbol")[col].ffill(limit=60)
    df = df.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    logger.info("数据: %d 行, %d 股票", len(df), df["symbol"].nunique())

    # 2. 计算 6 个通过因子
    logger.info("计算 6 个因子...")
    df["amihud"] = compute_factor_amihud(df)
    df["ivol"] = compute_factor_ivol(df)
    df["skewness"] = compute_factor_skewness(df)
    df["wk52_high"] = compute_factor_52wk_high(df)
    df["reversal_3m"] = compute_factor_reversal_3m(df)
    df["value_prof"] = compute_factor_value_prof(df)

    factor_names = ["amihud", "ivol", "skewness", "wk52_high",
                    "reversal_3m", "value_prof"]

    # 3. 截面相关矩阵 (月末快照)
    logger.info("计算截面相关矩阵...")
    df["ym"] = df["datetime"].dt.to_period("M")
    monthly_corrs = []
    for _, grp in df.groupby("ym"):
        sub = grp[factor_names].dropna()
        if len(sub) < 50:
            continue
        # 截面 rank 相关
        ranked = sub.rank(pct=True)
        c = ranked.corr(method="spearman").values
        monthly_corrs.append(c)

    avg_corr = np.nanmean(monthly_corrs, axis=0)
    corr_df = pd.DataFrame(avg_corr, index=factor_names, columns=factor_names)
    logger.info("\n截面平均 Spearman 相关矩阵:")
    for i, f1 in enumerate(factor_names):
        row = " ".join(f"{avg_corr[i,j]:>7.3f}" for j in range(len(factor_names)))
        logger.info("  %12s: %s", f1, row)

    # 4. Amihud vs IVOL 偏相关 (控制 log_mv)
    logger.info("\n偏相关分析 (Amihud vs IVOL | log_mv)...")
    if "circ_mv" in df.columns:
        df["log_mv"] = np.log(df["circ_mv"].clip(lower=1))
    else:
        df["log_mv"] = np.log(df["total_mv"].clip(lower=1)) if "total_mv" in df.columns else np.nan

    partial_results = {}
    # 月度偏相关
    pcorr_list = []
    for _, grp in df.groupby("ym"):
        pc = partial_corr(
            grp["amihud"].values, grp["ivol"].values, grp["log_mv"].values
        )
        if np.isfinite(pc):
            pcorr_list.append(pc)

    if pcorr_list:
        mean_pc = np.mean(pcorr_list)
        std_pc = np.std(pcorr_list, ddof=1)
        t_pc = mean_pc / (std_pc / np.sqrt(len(pcorr_list)))
        logger.info("  Amihud vs IVOL | log_mv: partial_corr=%.4f ± %.4f, t=%.2f, n=%d",
                     mean_pc, std_pc, t_pc, len(pcorr_list))
        partial_results["amihud_vs_ivol_ctrl_logmv"] = {
            "partial_corr": round(mean_pc, 4),
            "std": round(std_pc, 4),
            "t_stat": round(t_pc, 2),
            "n_months": len(pcorr_list),
        }

    # 5. 增量 IC: 正交化后的 Amihud 对未来收益的 IC
    logger.info("\n增量 IC 分析 (Amihud 正交化 IVOL 后)...")
    df["fwd_5d"] = df.groupby("symbol")["close"].transform(
        lambda s: s.shift(-5) / s - 1.0
    )
    resid_ic_list = []
    raw_ic_list = []
    for _, grp in df.groupby("ym"):
        sub = grp[["amihud", "ivol", "fwd_5d"]].dropna()
        if len(sub) < 50:
            continue
        # 原始 Amihud IC
        raw_ic, _ = sp_stats.spearmanr(sub["amihud"], sub["fwd_5d"])
        raw_ic_list.append(raw_ic)
        # 正交化: Amihud 的 IVOL 残差
        c = np.polyfit(sub["ivol"].values, sub["amihud"].values, 1)
        resid = sub["amihud"].values - np.polyval(c, sub["ivol"].values)
        r_ic, _ = sp_stats.spearmanr(resid, sub["fwd_5d"])
        resid_ic_list.append(r_ic)

    if resid_ic_list:
        raw_mean = np.mean(raw_ic_list)
        resid_mean = np.mean(resid_ic_list)
        retention = resid_mean / raw_mean if abs(raw_mean) > 1e-6 else 0
        logger.info("  Amihud 原始 IC: %.4f", raw_mean)
        logger.info("  Amihud 正交化(|IVOL) IC: %.4f", resid_mean)
        logger.info("  IC 保留率: %.1f%%", retention * 100)
        partial_results["amihud_incremental_ic"] = {
            "raw_ic": round(raw_mean, 4),
            "orthogonal_ic": round(resid_mean, 4),
            "retention_pct": round(retention * 100, 1),
        }

    # 6. 保存结果
    output = {
        "analysis": "factor_correlation_and_partial_analysis",
        "factors": factor_names,
        "correlation_matrix": {
            f1: {f2: round(float(corr_df.loc[f1, f2]), 4)
                 for f2 in factor_names}
            for f1 in factor_names
        },
        "partial_correlations": partial_results,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    out_path = ROOT / "output" / "factor_correlation_analysis.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info("结果已保存: %s", out_path)
    logger.info("耗时: %.1f 秒", time.time() - t0)


if __name__ == "__main__":
    main()
