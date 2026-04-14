"""Train MASTER Transformer model for stock selection.

Usage:
    python scripts/train_master.py --universe csi2000 --gpu 0
    python scripts/train_master.py --universe csi2000 --d-model 128 --epochs 20
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from aipha.models.master_model import MASTERTrainer

logger = logging.getLogger("train_master")


def parse_args():
    p = argparse.ArgumentParser(description="Train MASTER Transformer")
    p.add_argument("--universe", default="csi2000", choices=["csi300", "csi500", "csi2000"])
    p.add_argument("--gpu", type=int, default=0)
    p.add_argument("--d-model", type=int, default=256)
    p.add_argument("--t-nhead", type=int, default=4)
    p.add_argument("--s-nhead", type=int, default=2)
    p.add_argument("--dropout", type=float, default=0.5)
    p.add_argument("--beta", type=float, default=5.0)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--patience", type=int, default=5)
    p.add_argument("--seq-len", type=int, default=8)
    p.add_argument("--forward-days", type=int, default=5)
    p.add_argument("--train-years", type=int, default=3)
    p.add_argument("--predict-months", type=int, default=3)
    p.add_argument("--output-dir", type=str, default=None)
    p.add_argument("--start", type=str, default="2017-01-01", help="Data start date")
    p.add_argument("--end", type=str, default="2025-12-31", help="Data end date")
    return p.parse_args()


def compute_market_info(dates_index, cache_dir="data/cache/index_ref/index_daily"):
    """Compute 63-dim market info for each trading date.

    For 3 indices, each contributes 21 features:
      1 daily_return + 5 windows × (ret_mean, ret_std, amt_mean, amt_std) = 21
    Total: 3 × 21 = 63
    """
    index_codes = {
        "csi300": "000300_SH",
        "csi500": "000905_SH",
        "csi1000": "000852_SH",
    }
    windows = [5, 10, 20, 30, 60]
    all_market = {}  # date_str -> np.array(63,)

    index_data = {}
    for name, code in index_codes.items():
        fpath = os.path.join(cache_dir, f"index_daily_{code}.parquet")
        if not os.path.exists(fpath):
            logger.warning(f"Index file not found: {fpath}")
            continue
        idf = pd.read_parquet(fpath)
        idf["trade_date"] = pd.to_datetime(idf["trade_date"], format="%Y%m%d")
        idf = idf.sort_values("trade_date").set_index("trade_date")
        idf["daily_return"] = idf["close"].pct_change()
        idf["amount_f"] = idf["amount"].astype(float)
        index_data[name] = idf

    if not index_data:
        logger.error("No index data loaded! Market info will be zeros.")
        d_market = 63
        for dt in dates_index:
            dt_str = dt if isinstance(dt, str) else pd.Timestamp(dt).strftime("%Y-%m-%d")
            all_market[dt_str] = np.zeros(d_market, dtype=np.float32)
        return all_market, d_market

    for dt in dates_index:
        dt_ts = pd.Timestamp(dt)
        dt_str = dt_ts.strftime("%Y-%m-%d")
        features = []
        for name in ["csi300", "csi500", "csi1000"]:
            idf = index_data.get(name)
            if idf is None:
                features.extend([0.0] * 21)
                continue
            loc = idf.index.get_indexer([dt_ts], method="ffill")[0]
            if loc < 0 or loc < 60:
                features.extend([0.0] * 21)
                continue
            features.append(float(idf["daily_return"].iloc[loc]))
            for w in windows:
                start_idx = max(0, loc - w + 1)
                ret_slice = idf["daily_return"].iloc[start_idx: loc + 1].values
                amt_slice = idf["amount_f"].iloc[start_idx: loc + 1].values
                features.append(float(np.nanmean(ret_slice)))
                features.append(float(np.nanstd(ret_slice)))
                amt_norm = amt_slice / (amt_slice[-1] + 1e-12) if len(amt_slice) > 0 else [0.0]
                features.append(float(np.nanmean(amt_norm)))
                features.append(float(np.nanstd(amt_norm)))
        all_market[dt_str] = np.array(features, dtype=np.float32)

    d_market = len(next(iter(all_market.values())))
    logger.info(f"Market info: {len(all_market)} dates, {d_market} features")
    return all_market, d_market


def build_feature_store(df, feature_cols, label_col="fwd_return", seq_len=8):
    """Convert panel DataFrame to per-symbol temporal arrays.

    Returns:
        feature_store: {symbol: np.ndarray(n_dates, n_features)}
        label_store: {(date_str, symbol): float}
        sym_date_idx: {symbol: {date_str: int}}
    """
    feature_store = {}
    label_store = {}
    sym_date_idx = {}

    df = df.sort_values(["symbol", "datetime"])
    for sym, grp in df.groupby("symbol"):
        grp = grp.sort_values("datetime")
        dates = grp["datetime"].dt.strftime("%Y-%m-%d").tolist()
        feats = grp[feature_cols].values.astype(np.float32)
        # Replace NaN/Inf with 0
        feats = np.nan_to_num(feats, nan=0.0, posinf=3.0, neginf=-3.0)
        feature_store[sym] = feats
        date_idx_map = {d: i for i, d in enumerate(dates)}
        sym_date_idx[sym] = date_idx_map
        labels = grp[label_col].values
        for i, d in enumerate(dates):
            label_store[(d, sym)] = float(labels[i])

    logger.info(
        f"Feature store: {len(feature_store)} symbols, "
        f"{len(feature_cols)} features, "
        f"{len(label_store)} label entries"
    )
    return feature_store, label_store, sym_date_idx


def generate_wf_splits(dates, train_years=3, predict_months=3,
                        purge_days=10, forward_days=5):
    """Generate Walk-Forward splits matching existing pipeline."""
    train_len = train_years * 244
    test_len = predict_months * 20
    purge = max(purge_days, forward_days + 5)
    n = len(dates)
    splits = []
    start = 0
    while start + train_len + purge + test_len <= n:
        train_dates = dates[start: start + train_len]
        test_start = start + train_len + purge
        test_dates = dates[test_start: test_start + test_len]
        # Convert to str
        train_strs = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in train_dates]
        test_strs = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in test_dates]
        splits.append((train_strs, test_strs))
        start += test_len
    logger.info(f"WF splits: {len(splits)} folds, train={train_len}d, test={test_len}d")
    return splits


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logger.info(f"MASTER training: universe={args.universe}, gpu={args.gpu}")

    # ---- 1. Load data using existing pipeline ----
    # Add scripts dir to path for import
    scripts_dir = str(PROJECT_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from train_ml_factor import load_daily_data, filter_universe, compute_features

    universe_map = {"csi300": "000300.SZ", "csi500": "000905.SZ", "csi2000": "399303.SZ"}
    start_date = args.start
    end_date = args.end
    logger.info(f"Loading data: {start_date} ~ {end_date}")

    # Feature cache to avoid recomputing Alpha158 (~3M rows × 158 factors)
    cache_dir = PROJECT_ROOT / "data" / "cache" / "features"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"alpha158_{args.universe}_{start_date}_{end_date}.parquet"

    if cache_path.exists():
        logger.info(f"Loading cached features from {cache_path}")
        df = pd.read_parquet(cache_path)
        df["datetime"] = pd.to_datetime(df["datetime"])
        logger.info(f"Cached features loaded: {len(df)} rows, {len(df.columns)} columns")
    else:
        df = load_daily_data(start_date, end_date)
        df = filter_universe(df, args.universe)
        df = compute_features(df, deep_fundamental=True, behavioral=True)
        # Save cache
        logger.info(f"Saving feature cache to {cache_path}")
        df.to_parquet(cache_path, index=False)
        logger.info(f"Feature cache saved: {len(df)} rows")

    # ---- 2. Prepare labels ----
    fwd_days = args.forward_days
    df = df.sort_values(["symbol", "datetime"])
    df["fwd_return"] = df.groupby("symbol")["close"].transform(
        lambda x: x.shift(-1 - fwd_days) / x.shift(-1) - 1
    )

    # ---- 3. Alpha158 feature columns ----
    from aipha.factors.alpha158 import get_alpha158_factor_names
    alpha158_names = get_alpha158_factor_names()
    alpha158_cols = [c for c in alpha158_names if c in df.columns]
    n_alpha = len(alpha158_cols)
    logger.info(f"Alpha158 features: {n_alpha}")

    # ---- 4. Market info ----
    dates_all = sorted(df["datetime"].unique())
    market_store, d_market = compute_market_info(dates_all)
    logger.info(f"Market features: {d_market}")

    # ---- 5. Build feature store ----
    feature_store, label_store, sym_date_idx = build_feature_store(
        df, alpha158_cols, label_col="fwd_return", seq_len=args.seq_len
    )

    # ---- 6. WF splits ----
    date_strs = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates_all]
    splits = generate_wf_splits(
        dates_all, train_years=args.train_years,
        predict_months=args.predict_months, forward_days=fwd_days,
    )

    # ---- 7. Train ----
    config = {
        "d_feat": n_alpha, "d_model": args.d_model, "d_market": d_market,
        "t_nhead": args.t_nhead, "s_nhead": args.s_nhead,
        "dropout": args.dropout, "beta": args.beta, "lr": args.lr,
        "n_epochs": args.epochs, "patience": args.patience,
        "seq_len": args.seq_len, "gpu": args.gpu,
    }
    full_data = {
        "feature_store": feature_store, "market_store": market_store,
        "label_store": label_store, "sym_date_idx": sym_date_idx,
    }
    trainer = MASTERTrainer(config)
    t0 = time.time()
    all_preds = trainer.walk_forward(full_data, date_strs, splits)
    elapsed = time.time() - t0
    logger.info(f"Training complete: {len(all_preds)} predictions, {elapsed:.0f}s")

    # ---- 8. Save & evaluate ----
    output_dir = args.output_dir or f"models/ml_{args.universe}_master"
    os.makedirs(output_dir, exist_ok=True)

    oos_df = pd.DataFrame(all_preds, columns=["datetime", "symbol", "pred_score", "fwd_return"])
    oos_df["datetime"] = pd.to_datetime(oos_df["datetime"])
    oos_df.to_parquet(os.path.join(output_dir, "oos_predictions.parquet"), index=False)
    logger.info(f"OOS predictions saved: {len(oos_df)} rows -> {output_dir}")

    # Compute IC metrics
    daily_ics = trainer._compute_daily_ic(all_preds)
    mean_ic = np.nanmean(daily_ics)
    ic_std = np.nanstd(daily_ics)
    icir = mean_ic / (ic_std + 1e-8)
    ic_pos = np.mean([1 for x in daily_ics if x > 0]) / max(len(daily_ics), 1)

    report = {
        "model": "MASTER", "universe": args.universe, "d_model": args.d_model,
        "wf_folds": len(splits), "n_predictions": len(oos_df),
        "mean_ic": round(mean_ic, 4), "ic_std": round(ic_std, 4),
        "icir": round(icir, 4), "ic_positive_pct": round(ic_pos * 100, 1),
    }
    with open(os.path.join(output_dir, "evaluation_report.json"), "w") as f:
        json.dump(report, f, indent=2)

    logger.info("=" * 60)
    logger.info(f"MASTER | Universe: {args.universe}")
    logger.info(f"WF Folds: {len(splits)}")
    logger.info(f"Mean IC: {mean_ic:.4f}")
    logger.info(f"IC IR:   {icir:.4f}")
    logger.info(f"IC>0 %%:  {ic_pos * 100:.1f}%%")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
