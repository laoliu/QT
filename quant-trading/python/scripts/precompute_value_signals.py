"""Precompute value factor signals for backtest OOS replay.

Uses the top value/quality factors identified by eval_value_factors.py,
ICIR-weighted per cross-section.

Usage:
  python scripts/precompute_value_signals.py [--out output/value_signals.parquet]
"""
import argparse
import importlib.util
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Import from sibling script
_tmf_spec = importlib.util.spec_from_file_location(
    "train_ml_factor", ROOT / "scripts" / "train_ml_factor.py"
)
_tmf = importlib.util.module_from_spec(_tmf_spec)
_tmf_spec.loader.exec_module(_tmf)
load_daily_data = _tmf.load_daily_data
filter_universe = _tmf.filter_universe
_load_fina_indicator_bulk = _tmf._load_fina_indicator_bulk
_pit_merge_fina = _tmf._pit_merge_fina

# Reuse factor computation from eval script
_evf_spec = importlib.util.spec_from_file_location(
    "eval_value_factors", ROOT / "scripts" / "eval_value_factors.py"
)
_evf = importlib.util.module_from_spec(_evf_spec)
_evf_spec.loader.exec_module(_evf)
compute_value_factors = _evf.compute_value_factors

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

CACHE_DIR = ROOT / "data" / "cache" / "equity_tushare"

# Top factors and their ICIR(20d) weights from eval
TOP_FACTORS = {
    "reversal_60d": 0.447,
    "bp": 0.358,
    "reversal_120d": 0.336,
    "ep": 0.283,
    "lynch_param": 0.259,
    "gross_margin_stability": 0.265,
    "garp_score": 0.195,
}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--universe", default="csi_all")
    p.add_argument("--start", default="2015-01-05")
    p.add_argument("--end", default="2026-03-31")
    p.add_argument("--out", default="output/value_signals.parquet")
    return p.parse_args()


def main():
    args = parse_args()
    t0 = time.time()

    # Load data
    logger.info("Loading daily data...")
    daily = load_daily_data(args.start, args.end)
    daily = filter_universe(daily, args.universe)
    symbols = daily["symbol"].unique().tolist()
    logger.info("Loaded %d rows, %d symbols", len(daily), len(symbols))

    # Load & merge fina
    fina = _load_fina_indicator_bulk(CACHE_DIR, symbols)
    merged = _pit_merge_fina(daily, fina)

    # Compute factors
    merged, factor_names = compute_value_factors(merged)

    # ICIR-weighted combination
    weights = {f: w for f, w in TOP_FACTORS.items() if f in factor_names}
    w_sum = sum(weights.values())
    logger.info("Combining %d factors, weights: %s", len(weights), weights)

    combo = pd.Series(0.0, index=merged.index)
    for f, w in weights.items():
        vals = merged[f].fillna(0.5)  # neutral fill
        combo += vals * (w / w_sum)
    merged["pred_score"] = combo

    # Output
    out = merged[["datetime", "symbol", "pred_score"]].dropna()
    out = out.rename(columns={"datetime": "datetime"})
    out.to_parquet(args.out, index=False)
    elapsed = time.time() - t0
    logger.info(
        "Saved %d rows (%d days, %d symbols) to %s (%.1fs)",
        len(out), out["datetime"].nunique(), out["symbol"].nunique(),
        args.out, elapsed,
    )


if __name__ == "__main__":
    main()
