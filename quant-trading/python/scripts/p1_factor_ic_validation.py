"""R125 P1 因子 IC/IR 验证 — CSI2000 历史面板.

验证 5 个 P1 因子在 CSI2000 universe 的月频 Rank IC / ICIR:
  1. IVOL (特异波动率)       — Ang et al. (2006), IC预期 5-7%
  2. Overnight Return (隔夜收益) — Lou, Polk & Skouras (2019), IC预期 3-5%
  3. Return Skewness (收益偏度)  — Bali, Cakici & Whitelaw (2011), IC预期 3-5%
  4. 52-Week High (52周新高比率) — George & Hwang (2004), IC预期 2-4%
  5. Piotroski F-Score (财务健康) — Piotroski (2000), IC预期 3-5%

复用 P0 验证脚本的数据加载 + IC 计算框架。
输出: output/factor_ic_p1_validation.json
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
sys.path.insert(0, str(ROOT / "scripts"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("p1_factor_ic")

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

# ── 常量 ──
START = "2015-01-05"
END = "2026-02-28"
FORWARD_DAYS = [5, 10, 20]
SEG_CUTOFF = pd.Timestamp("2022-08-01")
IC_THRESHOLD = 0.02
ICIR_THRESHOLD = 0.3


# ── P1 因子计算 ──

def compute_factor_ivol(df: pd.DataFrame) -> pd.Series:
    """特异波动率 (IVOL) — Ang et al. (2006).

    公式: IVOL = std(r_i - beta_i * r_mkt) over 20d
    简化实现: 先计算市场日收益(CSI2000等权), 再回归取残差std
    低 IVOL → 高未来收益 (低波动异象)
    """
    # 个股日收益
    ret = df.groupby("symbol")["close"].transform(lambda s: s.pct_change())
    df = df.copy()
    df["_ret"] = ret

    # 市场日收益 — 截面等权均值
    mkt_ret = df.groupby("datetime")["_ret"].transform("mean")
    df["_mkt"] = mkt_ret

    # 残差 = ret - beta * mkt (简化: 直接用 ret - mkt 作为残差近似)
    # 严格版需滚动 OLS, 但截面中 beta ≈ 1 的近似在小盘股中合理
    df["_resid"] = df["_ret"] - df["_mkt"]

    # 滚动 20 日残差标准差
    ivol = df.groupby("symbol")["_resid"].transform(
        lambda s: s.rolling(20, min_periods=10).std()
    )
    # 低 IVOL → 高收益, 取负号使高分 = 好
    return -ivol


def compute_factor_overnight(df: pd.DataFrame) -> pd.Series:
    """隔夜收益率 — Lou, Polk & Skouras (2019).

    公式: ON_ret = open_t / pre_close_t - 1
    滚动 20 日均值作为因子值
    A 股散户偏好开盘交易, 高隔夜收益 → 散户追捧 → 未来低收益
    取负号: 低隔夜收益 = 高分
    """
    if "pre_close" not in df.columns:
        return pd.Series(np.nan, index=df.index, name="overnight")

    pre_close = df["pre_close"].replace(0, np.nan)
    on_ret = df["open"] / pre_close - 1.0

    # 滚动 20 日均值
    on_avg = on_ret.groupby(df["symbol"]).transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    return -on_avg  # 负号: 低隔夜收益 = 好信号


def compute_factor_skewness(df: pd.DataFrame) -> pd.Series:
    """收益偏度 — Bali, Cakici & Whitelaw (2011).

    公式: skew(daily_returns, 20d)
    经济学: 投资者过度偏好正偏度(彩票效应),
    高偏度股票被高估 → 未来低收益
    取负号: 低偏度 = 高分
    """
    ret = df.groupby("symbol")["close"].transform(lambda s: s.pct_change())
    skew = ret.groupby(df["symbol"]).transform(
        lambda s: s.rolling(20, min_periods=15).skew()
    )
    return -skew  # 负号: 低偏度 = 好


def compute_factor_52wk_high(df: pd.DataFrame) -> pd.Series:
    """52 周新高比率 — George & Hwang (2004).

    公式: close / max(high, 252d)
    经济学: 锚定偏差 — 股价接近52周新高时, 投资者反应不足,
    实际上接近新高意味着积极信息, 未来收益更高
    """
    high_252 = df.groupby("symbol")["high"].transform(
        lambda s: s.rolling(252, min_periods=120).max()
    )
    ratio = df["close"] / high_252.replace(0, np.nan)
    return ratio


def compute_factor_fscore(df: pd.DataFrame) -> pd.Series:
    """Piotroski F-Score — Piotroski (2000) J. Accounting Research.

    9 维二值评分 (0/1), 总分 0-9:
    盈利性 (4项):
      F1: ROA > 0
      F2: CFO > 0 (cfps > 0 近似)
      F3: ΔROA > 0
      F4: CFO > NI (应计质量, cfps > eps)
    杠杆/流动性 (3项):
      F5: Δ(debt_to_assets) < 0 (去杠杆)
      F6: Δ(current_ratio) > 0
      F7: 未发新股 (数据受限, 默认=1)
    运营效率 (2项):
      F8: Δ(grossprofit_margin) > 0
      F9: Δ(asset_turnover) > 0 (近似: Δ(profit_to_gr) > 0)
    """
    score = pd.Series(0.0, index=df.index)
    n_valid = pd.Series(0, index=df.index)

    # F1: ROA > 0
    if "roa" in df.columns:
        f1 = (df["roa"] > 0).astype(float)
        f1[df["roa"].isna()] = np.nan
        score = score + f1.fillna(0)
        n_valid = n_valid + f1.notna().astype(int)

    # F2: CFO > 0 (cfps > 0)
    if "cfps" in df.columns:
        f2 = (df["cfps"] > 0).astype(float)
        f2[df["cfps"].isna()] = np.nan
        score = score + f2.fillna(0)
        n_valid = n_valid + f2.notna().astype(int)

    # F3: ΔROA > 0
    if "roa" in df.columns:
        prev_roa = df.groupby("symbol")["roa"].shift(60)
        f3 = (df["roa"] > prev_roa).astype(float)
        f3[(df["roa"].isna()) | (prev_roa.isna())] = np.nan
        score = score + f3.fillna(0)
        n_valid = n_valid + f3.notna().astype(int)

    # F4: CFO > NI (cfps > eps)
    if "cfps" in df.columns and "eps" in df.columns:
        f4 = (df["cfps"] > df["eps"]).astype(float)
        mask = df["cfps"].isna() | df["eps"].isna()
        f4[mask] = np.nan
        score = score + f4.fillna(0)
        n_valid = n_valid + f4.notna().astype(int)

    # F5: Δ(debt_to_assets) < 0
    if "debt_to_assets" in df.columns:
        prev_d = df.groupby("symbol")["debt_to_assets"].shift(60)
        f5 = (df["debt_to_assets"] < prev_d).astype(float)
        f5[(df["debt_to_assets"].isna()) | (prev_d.isna())] = np.nan
        score = score + f5.fillna(0)
        n_valid = n_valid + f5.notna().astype(int)

    # F6: Δ(current_ratio) > 0
    if "current_ratio" in df.columns:
        prev_cr = df.groupby("symbol")["current_ratio"].shift(60)
        f6 = (df["current_ratio"] > prev_cr).astype(float)
        f6[(df["current_ratio"].isna()) | (prev_cr.isna())] = np.nan
        score = score + f6.fillna(0)
        n_valid = n_valid + f6.notna().astype(int)

    # F7: 未发新股 (无数据, 默认=1)
    score = score + 1.0
    n_valid = n_valid + 1

    # F8: Δ(grossprofit_margin) > 0
    if "grossprofit_margin" in df.columns:
        prev_gm = df.groupby("symbol")["grossprofit_margin"].shift(60)
        f8 = (df["grossprofit_margin"] > prev_gm).astype(float)
        f8_mask = df["grossprofit_margin"].isna() | prev_gm.isna()
        f8[f8_mask] = np.nan
        score = score + f8.fillna(0)
        n_valid = n_valid + f8.notna().astype(int)

    # F9: Δ(profit_to_gr) > 0 (利润/营收比 近似资产周转)
    if "profit_to_gr" in df.columns:
        prev_pg = df.groupby("symbol")["profit_to_gr"].shift(60)
        f9 = (df["profit_to_gr"] > prev_pg).astype(float)
        f9_mask = df["profit_to_gr"].isna() | prev_pg.isna()
        f9[f9_mask] = np.nan
        score = score + f9.fillna(0)
        n_valid = n_valid + f9.notna().astype(int)

    # 至少 4 项有效才输出
    score[n_valid < 4] = np.nan
    return score


# ── 输出函数 ──

def _print_summary(results: dict[str, Any]) -> None:
    """打印汇总表."""
    logger.info("\n" + "=" * 80)
    logger.info("P1 因子 IC/IR 汇总 (CSI2000, %s ~ %s)", START, END)
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


def _save_results(results: dict[str, Any], elapsed: float) -> None:
    """保存 JSON 结果."""
    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    output = {
        "analysis_type": "P1_factor_IC_validation",
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
    out_path = out_dir / "factor_ic_p1_validation.json"
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(output, fp, indent=2, ensure_ascii=False, default=str)
    logger.info("结果已保存: %s", out_path)


def main() -> None:
    """入口."""
    t0 = time.time()
    logger.info("=" * 70)
    logger.info("R125 P1 因子 IC/IR 验证 — CSI2000 历史面板")
    logger.info("=" * 70)

    # 1. 加载数据
    logger.info("Step 1: 加载全市场日线数据 %s ~ %s ...", START, END)
    df = load_daily_data(START, END)
    logger.info("  全市场: %d 行, %d 股票", len(df), df["symbol"].nunique())

    # 2. 筛选 CSI2000
    logger.info("Step 2: 筛选 CSI2000 universe...")
    df = filter_universe(df, "csi2000")
    logger.info("  CSI2000: %d 行, %d 股票", len(df), df["symbol"].nunique())

    # 3. 加载财务数据 (PIT) — F-Score 需要
    logger.info("Step 3: 加载 fina_indicator (PIT)...")
    cache_dir = ROOT / "data" / "cache" / "equity_tushare"
    symbols = df["symbol"].unique().tolist()
    fina = _load_fina_indicator_bulk(cache_dir, symbols)
    df = _pit_merge_fina(df, fina)
    fina_cols = [
        "roa", "cfps", "eps", "debt_to_assets", "current_ratio",
        "grossprofit_margin", "profit_to_gr",
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

    # 5. 计算 P1 因子
    logger.info("Step 5: 计算 5 个 P1 因子...")
    factors = {
        "ivol": compute_factor_ivol,
        "overnight": compute_factor_overnight,
        "skewness": compute_factor_skewness,
        "wk52_high": compute_factor_52wk_high,
        "fscore": compute_factor_fscore,
    }
    for fname, func in factors.items():
        logger.info("  计算 %s ...", fname)
        df[fname] = func(df)
        valid = df[fname].notna().sum()
        logger.info("    %s: %d 有效值 (%.1f%%)",
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
