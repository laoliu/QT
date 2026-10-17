"""Value/Quality factor evaluation inspired by famous A-share value investors.

Factor design sources:
  007 (Southern U): Lynch param, OCF quality, low leverage, DCF+DY anchor
  管我财: Deep value (EP/BP), high DY, contrarian, value trap screening

Usage:
  python scripts/eval_value_factors.py [--universe csi_all] [--start 2015-01-05]
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import importlib.util

# Import from sibling script via importlib
_tmf_spec = importlib.util.spec_from_file_location(
    "train_ml_factor", ROOT / "scripts" / "train_ml_factor.py"
)
_tmf = importlib.util.module_from_spec(_tmf_spec)
_tmf_spec.loader.exec_module(_tmf)

load_daily_data = _tmf.load_daily_data
filter_universe = _tmf.filter_universe
_load_fina_indicator_bulk = _tmf._load_fina_indicator_bulk
_pit_merge_fina = _tmf._pit_merge_fina

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

CACHE_DIR = ROOT / "data" / "cache" / "equity_tushare"

# Factor list: name → description → economic rationale
FACTOR_DEFS = {
    # === 007 style: Lynch + DCF + Quality ===
    "ep": "Earnings yield (1/PE_TTM) - 007: DCF anchor",
    "bp": "Book yield (1/PB) - Graham deep value",
    "dv_yield": "Dividend yield TTM - 007: DY>=8% buy signal",
    "lynch_param": "(g + d) / PE - 007: Lynch>=2.0 threshold",
    "ocf_quality": "CFPS/EPS - 007: OCF/NI>=1 quality gate",
    "low_leverage": "-debt_to_assets - 007: <50% gate",
    # === 管我财 style: Deep value + Contrarian ===
    "roe_quality": "ROE / debt_ratio - quality-adjusted profitability",
    "accruals": "-(EPS-CFPS)/|EPS| - low accrual = high quality",
    "reversal_60d": "-return_60d - contrarian mean reversion",
    "reversal_120d": "-return_120d - longer contrarian",
    # === Combined: Value + Quality composite ===
    "gross_margin_stability": "-std(GM, 4Q) - 007: stable moat",
    "composite_value": "EP+BP+DY blend - multi-anchor value",
    "garp_score": "ROE/PE - Growth at reasonable price",
    "piotroski_lite": "Simplified F-Score quality proxy",
}


# ── Factor definitions ──

def _load_fina_pit(symbols: list[str]) -> pd.DataFrame:
    """Load PIT-aligned fina_indicator data for given symbols."""
    return _load_fina_indicator_bulk(CACHE_DIR, symbols)


def _load_cashflow_pit(symbols: list[str]) -> pd.DataFrame:
    """Load PIT-aligned cashflow data for given symbols."""
    parts: list[pd.DataFrame] = []
    for sym in symbols:
        fname = f"cashflow_{sym.replace('.', '_')}.parquet"
        fpath = CACHE_DIR / fname
        if not fpath.exists():
            continue
        try:
            part = pd.read_parquet(fpath)
            keep_cols = ["ts_code", "ann_date", "end_date",
                         "n_cashflow_act", "n_cashflow_inv_act"]
            part = part[[c for c in keep_cols if c in part.columns]].copy()
            part["symbol"] = sym
            parts.append(part)
        except Exception:
            continue
    if not parts:
        return pd.DataFrame()
    cf = pd.concat(parts, ignore_index=True)
    for c in ["n_cashflow_act", "n_cashflow_inv_act"]:
        if c in cf.columns:
            cf[c] = pd.to_numeric(cf[c], errors="coerce")
    cf["ann_date"] = pd.to_datetime(cf["ann_date"], format="%Y%m%d", errors="coerce")
    cf = cf.dropna(subset=["ann_date"])
    cf = cf.sort_values(["symbol", "ann_date", "end_date"])
    cf = cf.drop_duplicates(subset=["symbol", "ann_date"], keep="last")
    return cf


def _merge_fina_to_daily(
    daily: pd.DataFrame, fina: pd.DataFrame
) -> pd.DataFrame:
    """Merge quarterly fina data to daily panel via PIT forward-fill."""
    return _pit_merge_fina(daily, fina)


def compute_value_factors(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all value/quality factors on merged daily+fina panel.

    Returns DataFrame with original columns plus factor columns.
    All factors are cross-sectionally rank-normalized to [0, 1].
    """
    eps = 1e-8
    result = df.copy()

    # --- 007 style factors ---

    # EP: Earnings yield = 1 / PE_TTM (higher = cheaper)
    pe = result["pe_ttm"].replace(0, np.nan)
    result["ep_raw"] = 1.0 / pe.clip(lower=1.0)  # clip PE>1 to avoid negative

    # BP: Book yield = 1 / PB (higher = cheaper)
    pb_val = result["pb"].replace(0, np.nan)
    result["bp_raw"] = 1.0 / pb_val.clip(lower=0.1)

    # Dividend yield TTM
    result["dv_yield_raw"] = result.get("dv_ttm", pd.Series(0, index=result.index))
    result["dv_yield_raw"] = result["dv_yield_raw"].fillna(0).clip(lower=0)

    # Lynch parameter: (g + d) / PE
    # g = dt_netprofit_yoy (扣非净利增长率%), d = dv_ttm (股息率%)
    if "dt_netprofit_yoy" in result.columns:
        g = result["dt_netprofit_yoy"].clip(-100, 200).fillna(0)
        d = result["dv_yield_raw"]
        pe_clip = pe.clip(lower=3.0)  # avoid extreme PE
        result["lynch_param_raw"] = (g + d) / pe_clip
    else:
        result["lynch_param_raw"] = np.nan

    # OCF Quality: CFPS / EPS (>1 means cash earnings > accrual earnings)
    if "cfps" in result.columns and "eps" in result.columns:
        eps_v = result["eps"].replace(0, np.nan)
        result["ocf_quality_raw"] = (
            result["cfps"] / (eps_v.abs() + eps)
        ).clip(-5, 5)
    else:
        result["ocf_quality_raw"] = np.nan

    # Low leverage: -debt_to_assets (higher = less leveraged)
    if "debt_to_assets" in result.columns:
        result["low_leverage_raw"] = -result["debt_to_assets"].fillna(60)
    else:
        result["low_leverage_raw"] = np.nan

    # --- 管我财 style factors ---

    # ROE quality: ROE / debt_ratio (profitability per unit leverage)
    if "roe" in result.columns and "debt_to_assets" in result.columns:
        da = result["debt_to_assets"].clip(lower=10) / 100.0
        result["roe_quality_raw"] = result["roe"].fillna(0) / (da + eps)
    else:
        result["roe_quality_raw"] = np.nan

    # Accruals: -(EPS - CFPS) / |EPS| (low accruals = high quality)
    if "cfps" in result.columns and "eps" in result.columns:
        abs_eps = result["eps"].abs() + eps
        result["accruals_raw"] = -(result["eps"] - result["cfps"]) / abs_eps
        result["accruals_raw"] = result["accruals_raw"].clip(-5, 5)
    else:
        result["accruals_raw"] = np.nan

    # Contrarian reversal: -return over past 60/120 days
    for w in [60, 120]:
        col = f"reversal_{w}d_raw"
        ret = result.groupby("symbol")["close"].pct_change(w)
        result[col] = -ret

    # --- Combined factors ---

    # Gross margin stability: -rolling std of GM (stable moat)
    if "grossprofit_margin" in result.columns:
        gm_std = result.groupby("symbol")["grossprofit_margin"].transform(
            lambda x: x.rolling(60, min_periods=20).std()
        )
        result["gross_margin_stability_raw"] = -gm_std
    else:
        result["gross_margin_stability_raw"] = np.nan

    # GARP: ROE / PE (growth at reasonable price)
    if "roe" in result.columns:
        result["garp_score_raw"] = result["roe"].fillna(0) / pe_clip
    else:
        result["garp_score_raw"] = np.nan

    # Piotroski F-Score lite (simplified 0-5 score)
    if "roe" in result.columns:
        score = pd.Series(0.0, index=result.index)
        score += (result.get("roe", 0) > 0).astype(float)
        if "cfps" in result.columns:
            score += (result["cfps"] > 0).astype(float)
        if "ocf_quality_raw" in result.columns:
            score += (result["ocf_quality_raw"] > 1.0).astype(float)
        if "debt_to_assets" in result.columns:
            # leverage decreasing (approx: below 50%)
            score += (result["debt_to_assets"] < 50).astype(float)
        if "grossprofit_margin" in result.columns:
            score += (result["grossprofit_margin"] > 20).astype(float)
        result["piotroski_lite_raw"] = score
    else:
        result["piotroski_lite_raw"] = np.nan

    # Composite value: equal-weight blend of EP + BP + DY ranks
    raw_cols = ["ep_raw", "bp_raw", "dv_yield_raw"]
    for c in raw_cols:
        if c not in result.columns:
            result[c] = np.nan

    # --- Cross-sectional rank normalization ---
    factor_raw_cols = [c for c in result.columns if c.endswith("_raw")]
    factor_names = []
    for raw_col in factor_raw_cols:
        fname = raw_col.replace("_raw", "")
        result[fname] = result.groupby("datetime")[raw_col].rank(pct=True)
        factor_names.append(fname)
        logger.info("Factor %s: non-null %.1f%%",
                     fname, 100 * result[fname].notna().mean())

    # Composite value = mean of EP, BP, DY ranks
    result["composite_value"] = result[["ep", "bp", "dv_yield"]].mean(axis=1)
    if "composite_value" not in factor_names:
        factor_names.append("composite_value")

    logger.info("Computed %d value/quality factors", len(factor_names))
    return result, factor_names


def evaluate_factor_ic(
    df: pd.DataFrame, factor_cols: list[str], fwd_days: list[int]
) -> pd.DataFrame:
    """Compute IC, ICIR, quantile returns for each factor.

    Returns summary DataFrame with one row per factor×fwd_period.
    """
    records = []
    for fwd in fwd_days:
        # Forward return
        fwd_col = f"fwd_ret_{fwd}d"
        df[fwd_col] = df.groupby("symbol")["close"].pct_change(fwd).shift(-fwd)

        for fcol in factor_cols:
            valid = df[[fcol, fwd_col, "datetime"]].dropna()
            if len(valid) < 100:
                continue

            # IC: per-date Spearman rank correlation
            ic_by_date = valid.groupby("datetime").apply(
                lambda g: g[fcol].corr(g[fwd_col], method="spearman")
                if len(g) > 20 else np.nan,
                include_groups=False,
            ).dropna()

            if len(ic_by_date) < 20:
                continue

            ic_mean = ic_by_date.mean()
            ic_std = ic_by_date.std()
            icir = ic_mean / ic_std if ic_std > 1e-10 else 0

            # Quantile returns (5 buckets)
            def _qret(g):
                if len(g) < 50:
                    return {}
                g = g.dropna(subset=[fcol, fwd_col])
                g["q"] = pd.qcut(g[fcol], 5, labels=False, duplicates="drop")
                return g.groupby("q")[fwd_col].mean().to_dict()

            q_samples = valid.groupby("datetime").apply(
                _qret, include_groups=False
            )
            q_means = pd.DataFrame(q_samples.tolist()).mean()
            ls_ret = q_means.get(4, 0) - q_means.get(0, 0) if len(q_means) >= 5 else 0

            # Monotonicity: Spearman corr of quantile → return
            if len(q_means) >= 3:
                from scipy.stats import spearmanr
                mono_r, _ = spearmanr(range(len(q_means)), q_means.values)
            else:
                mono_r = 0

            records.append({
                "factor": fcol,
                "fwd_days": fwd,
                "ic_mean": round(ic_mean, 5),
                "ic_std": round(ic_std, 5),
                "icir": round(icir, 3),
                "ls_return_ann": round(ls_ret * 252 / fwd, 4),
                "monotonicity": round(mono_r, 3),
                "n_dates": len(ic_by_date),
                "q1_ret": round(q_means.get(0, 0), 5),
                "q5_ret": round(q_means.get(4, 0), 5),
            })
            logger.info(
                "  %s fwd=%dd: IC=%.4f ICIR=%.3f LS_AR=%.1f%% mono=%.2f",
                fcol, fwd, ic_mean, icir, ls_ret * 252 / fwd * 100, mono_r,
            )

    return pd.DataFrame(records)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Value factor evaluation")
    p.add_argument("--universe", default="csi_all")
    p.add_argument("--start", default="2015-01-05")
    p.add_argument("--end", default="2026-03-31")
    p.add_argument("--fwd", default="5,10,20", help="Forward return periods")
    p.add_argument("--out", default="output/value_factor_eval")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    fwd_days = [int(x) for x in args.fwd.split(",")]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Load daily data
    logger.info("Loading daily data %s ~ %s ...", args.start, args.end)
    daily = load_daily_data(args.start, args.end)
    logger.info("Raw daily: %d rows, %d symbols", len(daily), daily["symbol"].nunique())

    # Step 2: Filter universe
    daily = filter_universe(daily, args.universe)
    symbols = daily["symbol"].unique().tolist()
    logger.info("After universe filter: %d rows, %d symbols", len(daily), len(symbols))

    # Step 3: Load financial data (PIT aligned)
    logger.info("Loading fina_indicator for %d symbols...", len(symbols))
    fina = _load_fina_pit(symbols)
    logger.info("Fina records: %d", len(fina))

    # Step 4: PIT merge
    logger.info("PIT merging fina to daily...")
    merged = _merge_fina_to_daily(daily, fina)

    # Step 5: Compute factors
    logger.info("Computing value/quality factors...")
    merged, factor_names = compute_value_factors(merged)
    logger.info("Factor columns: %s", factor_names)

    # Step 6: Evaluate IC
    logger.info("Evaluating IC for %d factors x %s fwd periods...",
                len(factor_names), fwd_days)
    ic_results = evaluate_factor_ic(merged, factor_names, fwd_days)

    # Step 7: Save results
    ic_results.to_csv(out_dir / "ic_results.csv", index=False)
    logger.info("IC results saved to %s", out_dir / "ic_results.csv")

    # Print summary table
    print("\n" + "=" * 80)
    print("VALUE/QUALITY FACTOR IC EVALUATION SUMMARY")
    print("=" * 80)
    # Pivot: factor x fwd_days → ICIR
    if not ic_results.empty:
        pivot = ic_results.pivot_table(
            index="factor", columns="fwd_days",
            values=["icir", "ic_mean", "ls_return_ann", "monotonicity"],
        )
        print(pivot.to_string())

        # Rank factors by average |ICIR|
        avg_icir = ic_results.groupby("factor")["icir"].apply(
            lambda x: x.abs().mean()
        ).sort_values(ascending=False)
        print("\n--- Factor ranking by avg |ICIR| ---")
        for fname, v in avg_icir.items():
            print(f"  {fname:<25s} |ICIR|={v:.3f}")

        # Save factor definitions + results as JSON
        summary = {
            "factors": {
                name: FACTOR_DEFS.get(name, "")
                for name in factor_names
            },
            "ic_results": ic_results.to_dict(orient="records"),
            "ranking": {str(k): round(v, 4) for k, v in avg_icir.items()},
            "data_range": f"{args.start} ~ {args.end}",
            "universe": args.universe,
            "n_symbols": len(symbols),
        }
        with open(out_dir / "factor_summary.json", "w") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

    logger.info("Done. Results in %s", out_dir)


if __name__ == "__main__":
    main()
