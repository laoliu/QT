"""R124 P0 因子 IC/IR 验证 — CSI2000 历史面板.

验证 5 个 P0 因子在 CSI2000 universe 的月频 Rank IC / ICIR:
  1. Gross Profitability (GP/TA)
  2. 3-Month Reversal
  3. Value+Profitability Composite
  4. Amihud Illiquidity
  5. Earnings Acceleration

数据源: data/cache/equity_tushare/ (OHLCV + daily_basic + fina_indicator)
方法: 向量化 DataFrame 计算 → 月频截面 Rank IC → 分段验证
输出: output/factor_ic_p0_validation.json
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
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("p0_factor_ic")

# 复用 train_ml_factor 的数据加载函数
sys.path.insert(0, str(ROOT / "scripts"))
from train_ml_factor import (  # noqa: E402
    load_daily_data,
    filter_universe,
    _load_fina_indicator_bulk,
    _pit_merge_fina,
)

# ── 常量 ──
START = "2015-01-05"
END = "2026-02-28"
FORWARD_DAYS = [5, 10, 20]
SEG_CUTOFF = pd.Timestamp("2022-08-01")
IC_THRESHOLD = 0.02
ICIR_THRESHOLD = 0.3


def compute_factor_gp_ta(df: pd.DataFrame) -> pd.Series:
    """GP/TA = grossprofit_margin / 100 (已是比率).

    fina_indicator 的 grossprofit_margin 是百分比形式(如 35.2 = 35.2%)，
    转为比率即为 GP/TA 的近似。
    """
    if "grossprofit_margin" in df.columns:
        return df["grossprofit_margin"] / 100.0
    return pd.Series(np.nan, index=df.index, name="gp_ta")


def compute_factor_reversal_3m(df: pd.DataFrame) -> pd.Series:
    """3M Reversal = -R_{t-60:t-5}.

    skip 最近 5 天避免微观结构噪声。
    """
    # 过去 60 天收益 = close / close.shift(60) - 1
    # 过去 5 天收益 = close / close.shift(5) - 1
    # 中间段收益 ≈ (1+R60) / (1+R5) - 1
    r60 = df.groupby("symbol")["close"].transform(
        lambda s: s / s.shift(60) - 1
    )
    r5 = df.groupby("symbol")["close"].transform(
        lambda s: s / s.shift(5) - 1
    )
    mid_ret = (1 + r60) / (1 + r5) - 1
    return -mid_ret  # 反转: 取负号


def compute_factor_value_prof(df: pd.DataFrame) -> pd.Series:
    """Value+Profitability 6-metric composite.

    使用可用的 proxy 指标:
      - GP/TA → grossprofit_margin
      - OP/TA → netprofit_margin (近似)
      - ROE
      - 1/PE  (EP = earnings/price)
      - 1/PB  (BP = book/price)
      - EPS/Price
    每个指标截面排名后等权平均。
    """
    metrics: dict[str, pd.Series] = {}
    if "grossprofit_margin" in df.columns:
        metrics["gp_margin"] = df["grossprofit_margin"]
    if "netprofit_margin" in df.columns:
        metrics["np_margin"] = df["netprofit_margin"]
    if "roe" in df.columns:
        metrics["roe"] = df["roe"]
    if "pe_ttm" in df.columns:
        pe = df["pe_ttm"].replace(0, np.nan)
        metrics["ep"] = 1.0 / pe
    if "pb" in df.columns:
        pb = df["pb"].replace(0, np.nan)
        metrics["bp"] = 1.0 / pb
    if "eps" in df.columns and "close" in df.columns:
        close = df["close"].replace(0, np.nan)
        metrics["eps_price"] = df["eps"] / close
    if not metrics:
        return pd.Series(np.nan, index=df.index)
    # 截面排名
    ranks = pd.DataFrame(index=df.index)
    for name, vals in metrics.items():
        ranks[name] = vals.groupby(df["datetime"]).rank(pct=True)
    return ranks.mean(axis=1)


def compute_factor_amihud(df: pd.DataFrame) -> pd.Series:
    """Amihud ILLIQ = mean(|ret|/amount) over 20d.

    amount 单位为千元, 需要 > 0 过滤。
    """
    daily_ret = df.groupby("symbol")["close"].transform(
        lambda s: s.pct_change()
    )
    # |return| / amount
    amt = df["amount"].replace(0, np.nan) if "amount" in df.columns else None
    if amt is None:
        return pd.Series(np.nan, index=df.index)
    ratio = daily_ret.abs() / amt
    illiq = ratio.groupby(df["symbol"]).transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    return illiq


def compute_factor_earnings_accel(df: pd.DataFrame) -> pd.Series:
    """Earnings Acceleration = ΔGrowth.

    使用 dt_netprofit_yoy 的变化率作为盈利加速的近似:
    EA = dt_netprofit_yoy_t - dt_netprofit_yoy_{t-60}
    (约一个季度的变化)
    """
    if "dt_netprofit_yoy" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    prev = df.groupby("symbol")["dt_netprofit_yoy"].transform(
        lambda s: s.shift(60)
    )
    return df["dt_netprofit_yoy"] - prev


def compute_monthly_ic(
    df: pd.DataFrame, factor_col: str, fwd_col: str,
) -> list[dict[str, Any]]:
    """计算月频截面 Rank IC.

    对每个月末的截面数据计算 Spearman 秩相关。
    """
    records: list[dict[str, Any]] = []
    df_valid = df[[factor_col, fwd_col, "datetime"]].dropna()
    if df_valid.empty:
        return records
    df_valid = df_valid.copy()
    df_valid["ym"] = df_valid["datetime"].dt.to_period("M")
    for ym, grp in df_valid.groupby("ym"):
        if len(grp) < 30:  # 至少 30 只股票
            continue
        fv = grp[factor_col].values
        rv = grp[fwd_col].values
        # 检查方差
        if np.std(fv) < 1e-12 or np.std(rv) < 1e-12:
            continue
        ic, pval = sp_stats.spearmanr(fv, rv)
        records.append({
            "period": str(ym),
            "date": grp["datetime"].max().strftime("%Y-%m-%d"),
            "ic": float(ic) if np.isfinite(ic) else 0.0,
            "pval": float(pval) if np.isfinite(pval) else 1.0,
            "n_stocks": len(grp),
        })
    return records


def segment_ic_stats(
    ic_records: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """分段统计: full / pre_2022 / post_2022."""
    def _stats(recs: list[dict]) -> dict[str, float]:
        if not recs:
            return {"ic_mean": 0, "ic_std": 0, "icir": 0,
                    "ic_pos_rate": 0, "n_months": 0, "t_stat": 0,
                    "p_value": 1.0}
        ics = [r["ic"] for r in recs]
        n = len(ics)
        mean_ic = float(np.mean(ics))
        std_ic = float(np.std(ics, ddof=1)) if n > 1 else 0.0
        icir = mean_ic / std_ic if std_ic > 1e-12 else 0.0
        pos_rate = sum(1 for x in ics if x > 0) / n
        t_val = mean_ic / (std_ic / np.sqrt(n)) if std_ic > 0 else 0.0
        p_val = 2 * (1 - sp_stats.t.cdf(abs(t_val), df=max(n - 1, 1)))
        return {
            "ic_mean": round(mean_ic, 4),
            "ic_std": round(std_ic, 4),
            "icir": round(icir, 3),
            "ic_pos_rate": round(pos_rate, 3),
            "n_months": n,
            "t_stat": round(float(t_val), 3),
            "p_value": round(float(p_val), 4),
        }

    cutoff_str = SEG_CUTOFF.strftime("%Y-%m")
    pre = [r for r in ic_records if r["period"] < cutoff_str]
    post = [r for r in ic_records if r["period"] >= cutoff_str]
    return {
        "full": _stats(ic_records),
        "pre_2022aug": _stats(pre),
        "post_2022aug": _stats(post),
    }


def _print_summary(results: dict[str, Any]) -> None:
    """打印汇总表."""
    logger.info("\n" + "=" * 80)
    logger.info("P0 因子 IC/IR 汇总 (CSI2000, %s ~ %s)", START, END)
    logger.info("=" * 80)
    header = (f"{'Factor':<18} {'Fwd':>5} {'IC':>7} {'ICIR':>6} "
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
                f"{fname:<18} {fwd_key:>5} {f['ic_mean']:>7.4f} "
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


def _save_results(
    results: dict[str, Any], elapsed: float,
) -> None:
    """保存 JSON 结果."""
    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    output = {
        "analysis_type": "P0_factor_IC_validation",
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
    # 生成 passed/failed 分类 (基于 fwd_5d)
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
        "passed": passed,
        "marginal": marginal,
        "failed": failed,
    }
    out_path = out_dir / "factor_ic_p0_validation.json"
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(output, fp, indent=2, ensure_ascii=False, default=str)
    logger.info("结果已保存: %s", out_path)


def main() -> None:
    """入口."""
    t0 = time.time()
    logger.info("=" * 70)
    logger.info("R124 P0 因子 IC/IR 验证 — CSI2000 历史面板")
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
    # 前向填充 (limit 60 天 ≈ 1 季度)
    fina_cols = ["grossprofit_margin", "netprofit_margin", "roe",
                 "eps", "bps", "dt_netprofit_yoy"]
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

    # 5. 计算因子
    logger.info("Step 5: 计算 5 个 P0 因子...")
    factors = {
        "gp_ta": compute_factor_gp_ta,
        "reversal_3m": compute_factor_reversal_3m,
        "value_prof": compute_factor_value_prof,
        "amihud": compute_factor_amihud,
        "earnings_accel": compute_factor_earnings_accel,
    }
    for fname, func in factors.items():
        df[fname] = func(df)
        valid = df[fname].notna().sum()
        logger.info("  %s: %d 有效值 (%.1f%%)",
                     fname, valid, 100 * valid / len(df))

    # 6. 月频 IC 计算
    logger.info("Step 6: 计算月频 Rank IC...")
    results: dict[str, Any] = {}
    for fname in factors:
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
