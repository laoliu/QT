"""Tiered Combo V3 组合回测 — 4组件NAV合成 + 完整评估。

组合配置:
  ML CSI2000 V3b (40%) + CB stock_quality_v4b (20%)
  + CSI300 >=200B (10%) + ETF FAA V5 (30%)

输出: output/tiered_combo_v3/ 下组合指标 + 相关性 + 分段验证
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("combo_tiered_v3")

# ── NAV paths ──
NAV_PATHS = {
    "ml_csi2000": ROOT / "output/ml_csi2000_ensemble_v3b_master_v2/daily_snapshots.csv",
    "cb_v4b": ROOT / "output/cb_stock_quality_v4b_balanced_8y/daily_snapshots.csv",
    "csi300": ROOT / "output/ml_csi300_ge200b_ensemble_v1/daily_snapshots.csv",
    "etf": ROOT / "output/etf_cross_v5_11y/daily_snapshots.csv",
}

# ── Weight scheme ──
WEIGHTS = {
    "ml_csi2000": 0.40,
    "cb_v4b": 0.20,
    "csi300": 0.10,
    "etf": 0.30,
}

INITIAL_CAPITAL = 10_000_000


def load_nav(path: Path) -> pd.Series:
    """Load NAV from daily_snapshots.csv."""
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date")
    return df.set_index("date")["nav"]


def align_navs(navs: dict[str, pd.Series]) -> dict[str, pd.Series]:
    """Align to common date range."""
    common_dates = None
    for s in navs.values():
        idx = set(s.index)
        common_dates = idx if common_dates is None else common_dates & idx
    common_sorted = sorted(common_dates)
    logger.info(
        "Common date range: %s ~ %s (%d days)",
        common_sorted[0].strftime("%Y-%m-%d"),
        common_sorted[-1].strftime("%Y-%m-%d"),
        len(common_sorted),
    )
    return {k: v.loc[common_sorted] for k, v in navs.items()}


def synthesize_combo(
    navs: dict[str, pd.Series],
    weights: dict[str, float],
    initial_capital: float = INITIAL_CAPITAL,
) -> pd.Series:
    """Fixed-weight NAV combination."""
    combo_nav = None
    for name, w in weights.items():
        nav = navs[name]
        norm = nav / nav.iloc[0] * w * initial_capital
        combo_nav = norm if combo_nav is None else combo_nav + norm
    return combo_nav


def compute_metrics(nav: pd.Series) -> dict[str, Any]:
    """Full metrics suite."""
    rets = nav.pct_change().dropna()
    n_days = len(rets)
    n_years = n_days / 244.0
    cum = nav.iloc[-1] / nav.iloc[0]
    ar = cum ** (1 / max(n_years, 0.25)) - 1
    ar_arith = float(rets.mean()) * 244
    vol = float(rets.std()) * np.sqrt(244)
    sr = (ar_arith - 0.02) / vol if vol > 0 else 0.0
    peak = nav.cummax()
    dd = (peak - nav) / peak
    mdd = float(dd.max())
    calmar = float(ar) / mdd if mdd > 0 else 0.0
    # CVaR 95%
    sorted_rets = np.sort(rets.values)
    cutoff = int(len(sorted_rets) * 0.05)
    cvar_95 = -float(sorted_rets[:max(cutoff, 1)].mean()) if cutoff > 0 else 0.0
    # Yearly returns
    yearly: dict[str, float] = {}
    for yr, grp in rets.groupby(rets.index.year):
        yearly[str(yr)] = round(float((1 + grp).prod() - 1), 4)
    win_years = sum(1 for v in yearly.values() if v > 0)
    median_yr = float(np.median(list(yearly.values()))) if yearly else 0.0
    # MDD recovery
    dd_peak_date = dd.idxmax()
    recovery_days = 0
    if mdd > 0:
        post = nav[nav.index >= dd_peak_date]
        recovered = post[post >= peak[dd_peak_date]]
        if not recovered.empty:
            mask = (nav.index >= dd_peak_date) & (nav.index <= recovered.index[0])
            recovery_days = int(mask.sum())
    return {
        "annualized_return": round(float(ar), 4),
        "volatility": round(vol, 4),
        "sharpe_ratio": round(sr, 3),
        "max_drawdown": round(mdd, 4),
        "calmar_ratio": round(calmar, 3),
        "cvar_95": round(cvar_95, 4),
        "mdd_recovery_days": recovery_days,
        "n_years": round(n_years, 2),
        "total_return": round(float(cum - 1), 4),
        "yearly_returns": yearly,
        "win_years": f"{win_years}/{len(yearly)}",
        "median_yearly_return": round(median_yr, 4),
    }


def compute_segmented(nav: pd.Series) -> dict[str, Any]:
    """Full + pre/post 2022-08 segmented analysis."""
    cutoff = pd.Timestamp("2022-08-01")
    result = {"full": compute_metrics(nav)}
    pre = nav[nav.index < cutoff]
    if len(pre) > 60:
        result["pre_2022aug"] = compute_metrics(pre)
    post = nav[nav.index >= cutoff]
    if len(post) > 60:
        result["post_2022aug"] = compute_metrics(post)
    # Exclude best year
    rets = nav.pct_change().dropna()
    yearly = {}
    for yr, grp in rets.groupby(rets.index.year):
        yearly[yr] = float((1 + grp).prod() - 1)
    if yearly:
        best_yr = max(yearly, key=yearly.get)
        excl_rets = rets[rets.index.year != best_yr]
        if len(excl_rets) > 60:
            excl_nav = (1 + excl_rets).cumprod()
            excl_nav = excl_nav / excl_nav.iloc[0] * nav.iloc[0]
            result["excl_best_year"] = compute_metrics(excl_nav)
            result["excl_best_year"]["excluded_year"] = int(best_yr)
    return result


def compute_correlation_matrix(
    navs: dict[str, pd.Series],
) -> pd.DataFrame:
    """Daily return correlation matrix."""
    rets_df = pd.DataFrame(
        {name: nav.pct_change().dropna() for name, nav in navs.items()}
    ).dropna()
    return rets_df.corr()


def compute_risk_contribution(
    navs: dict[str, pd.Series],
    weights: dict[str, float],
) -> dict[str, float]:
    """Euler risk contribution per component."""
    rets_df = pd.DataFrame(
        {name: nav.pct_change().dropna() for name, nav in navs.items()}
    ).dropna()
    names = list(weights.keys())
    w = np.array([weights[n] for n in names])
    cov = rets_df[names].cov().values * 244
    port_vol = np.sqrt(w @ cov @ w)
    marginal = cov @ w
    rc = w * marginal / port_vol
    rc_pct = rc / rc.sum()
    return {n: round(float(rc_pct[i]), 4) for i, n in enumerate(names)}


def main() -> None:
    """Main entry."""
    output_dir = ROOT / "output" / "tiered_combo_v3"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load NAVs
    logger.info("Loading component NAVs...")
    raw_navs = {}
    for name, path in NAV_PATHS.items():
        if not path.exists():
            logger.error("Missing: %s", path)
            sys.exit(1)
        raw_navs[name] = load_nav(path)
        logger.info("  %s: %d days", name, len(raw_navs[name]))

    # 2. Align
    navs = align_navs(raw_navs)

    # 3. Synthesize combo
    combo_nav = synthesize_combo(navs, WEIGHTS)
    logger.info("Combo NAV: %.0f -> %.0f", combo_nav.iloc[0], combo_nav.iloc[-1])

    # 4. Compute metrics
    seg = compute_segmented(combo_nav)
    full = seg["full"]
    logger.info("=== FULL PERIOD ===")
    for k, v in full.items():
        if k != "yearly_returns":
            logger.info("  %s: %s", k, v)
    logger.info("  yearly: %s", full["yearly_returns"])

    if "post_2022aug" in seg:
        logger.info("=== POST 2022-08 ===")
        p = seg["post_2022aug"]
        logger.info("  AR=%.2f%% SR=%.3f MDD=%.2f%%",
                     p["annualized_return"] * 100, p["sharpe_ratio"],
                     p["max_drawdown"] * 100)

    # 5. Component metrics
    comp_metrics = {}
    for name, nav in navs.items():
        comp_metrics[name] = compute_metrics(nav)

    # 6. Correlation matrix
    corr = compute_correlation_matrix(navs)
    logger.info("=== CORRELATION MATRIX ===")
    logger.info("\n%s", corr.to_string())

    # 7. Risk contribution
    rc = compute_risk_contribution(navs, WEIGHTS)
    logger.info("=== RISK CONTRIBUTION ===")
    for name, pct in rc.items():
        logger.info("  %s: %.1f%%", name, pct * 100)

    # 8. Threshold checks
    logger.info("=== THRESHOLD CHECKS ===")
    checks = {
        "AR >= 20%": full["annualized_return"] >= 0.20,
        "SR >= 0.8": full["sharpe_ratio"] >= 0.8,
        "MDD <= 30%": full["max_drawdown"] <= 0.30,
        "Calmar >= 0.6": full["calmar_ratio"] >= 0.6,
        "CVaR95 < 3%": full["cvar_95"] < 0.03,
        "Max pair corr < 0.75": float(corr.where(
            np.triu(np.ones(corr.shape), k=1).astype(bool)
        ).max().max()) < 0.75,
    }
    all_pass = True
    for desc, ok in checks.items():
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        logger.info("  [%s] %s", status, desc)

    # 9. Save results
    result = {
        "profile": "tiered_combo_v3",
        "weights": WEIGHTS,
        "initial_capital": INITIAL_CAPITAL,
        "combo_metrics": seg,
        "component_metrics": comp_metrics,
        "correlation_matrix": corr.to_dict(),
        "risk_contribution": rc,
        "threshold_checks": {k: bool(v) for k, v in checks.items()},
        "all_pass": all_pass,
    }
    with open(output_dir / "combo_summary.json", "w") as f:
        json.dump(result, f, indent=2, default=str)

    # Save combo NAV
    combo_df = pd.DataFrame({
        "date": combo_nav.index.strftime("%Y-%m-%d"),
        "nav": combo_nav.values,
    })
    combo_df.to_csv(output_dir / "combo_daily_nav.csv", index=False)

    verdict = "ALL PASS" if all_pass else "SOME FAILED"
    logger.info("=== VERDICT: %s ===", verdict)


if __name__ == "__main__":
    main()
