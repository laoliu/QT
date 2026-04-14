"""R125 因子滚动 IC 衰减监控.

监控 6 个验证通过因子的 Post-2022 滚动 IC 趋势,
检测 alpha 衰减 (McLean & Pontiff 2016: 近3年 SR 不低于全量期 40%).

输出: output/factor_rolling_ic_monitor.json + 控制台摘要
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("factor_decay")

from train_ml_factor import load_daily_data, filter_universe
from train_ml_factor import _load_fina_indicator_bulk, _pit_merge_fina
from p0_factor_ic_validation import (
    compute_factor_amihud, compute_factor_reversal_3m,
    compute_factor_value_prof, compute_monthly_ic,
)
from p1_factor_ic_validation import (
    compute_factor_ivol, compute_factor_skewness,
    compute_factor_52wk_high,
)

START, END = "2015-01-05", "2026-02-28"
FWD_DAYS = 5
DECAY_THRESHOLD = 0.40  # McLean & Pontiff: 近3年 / 全量期 >= 40%


def compute_rolling_ic(
    df: pd.DataFrame, factor_col: str, fwd_col: str,
    window_months: int = 12,
) -> pd.DataFrame:
    """计算滚动窗口 IC.

    Returns DataFrame with columns: [ym, ic, cum_ic, rolling_ic].
    """
    monthly = compute_monthly_ic(df, factor_col, fwd_col)
    mdf = pd.DataFrame(monthly)
    if mdf.empty:
        return mdf
    mdf["ym"] = pd.to_datetime(mdf["period"].astype(str))
    mdf = mdf.sort_values("ym")
    mdf["cum_ic"] = mdf["ic"].expanding().mean()
    mdf["rolling_ic"] = mdf["ic"].rolling(window_months, min_periods=6).mean()
    return mdf


def check_decay(full_ic: float, recent_ic: float) -> dict:
    """检查因子衰减.

    McLean & Pontiff: 近3年 IC / 全量期 IC >= 40%
    """
    if abs(full_ic) < 1e-6:
        return {"ratio": 0.0, "passed": False, "label": "INVALID"}
    ratio = recent_ic / full_ic
    passed = ratio >= DECAY_THRESHOLD
    if ratio >= 0.8:
        label = "STRONG"
    elif ratio >= 0.6:
        label = "STABLE"
    elif ratio >= DECAY_THRESHOLD:
        label = "WATCH"
    else:
        label = "DECAYED"
    return {"ratio": round(ratio, 3), "passed": passed, "label": label}


def main() -> None:
    """入口."""
    t0 = time.time()
    logger.info("=" * 60)
    logger.info("R125 因子滚动 IC 衰减监控")
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

    # 2. 计算前向收益
    df["fwd_5d"] = df.groupby("symbol")["close"].transform(
        lambda s: s.shift(-FWD_DAYS) / s - 1.0
    )

    # 3. 计算因子
    factors = {
        "amihud": compute_factor_amihud,
        "ivol": compute_factor_ivol,
        "skewness": compute_factor_skewness,
        "wk52_high": compute_factor_52wk_high,
        "reversal_3m": compute_factor_reversal_3m,
        "value_prof": compute_factor_value_prof,
    }
    for fname, func in factors.items():
        df[fname] = func(df)

    # 4. 滚动 IC + 衰减检查
    results = {}
    cutoff = pd.Timestamp("2023-03-01")  # 近3年
    logger.info("\n%-15s %8s %8s %8s %8s %8s",
                "Factor", "FullIC", "3yrIC", "Ratio", "Status", "n_months")
    logger.info("-" * 65)

    for fname in factors:
        mdf = compute_rolling_ic(df, fname, "fwd_5d")
        if mdf.empty:
            continue
        full_ic = mdf["ic"].mean()
        recent = mdf[mdf["ym"] >= cutoff]
        recent_ic = recent["ic"].mean() if len(recent) > 0 else 0
        decay = check_decay(full_ic, recent_ic)
        results[fname] = {
            "full_ic": round(full_ic, 4),
            "recent_3yr_ic": round(recent_ic, 4),
            "decay": decay,
            "n_months_total": len(mdf),
            "n_months_recent": len(recent),
        }
        logger.info(
            "%-15s %8.4f %8.4f %8.3f %8s %8d",
            fname, full_ic, recent_ic, decay["ratio"],
            decay["label"], len(recent),
        )

    # 5. 保存
    # 确保 JSON 可序列化
    def _json_safe(obj):
        if isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        return obj

    safe_results = {}
    for k, v in results.items():
        safe_results[k] = {
            k2: (_json_safe(v2) if not isinstance(v2, dict)
                 else {k3: _json_safe(v3) for k3, v3 in v2.items()})
            for k2, v2 in v.items()
        }
    out = {"analysis": "factor_rolling_ic_decay", "results": safe_results,
           "elapsed": round(time.time() - t0, 1)}
    out_path = ROOT / "output" / "factor_rolling_ic_monitor.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    logger.info("\n结果已保存: %s (%.1fs)", out_path, time.time() - t0)


if __name__ == "__main__":
    main()
