"""Legacy ML 选股模型训练脚本。

.. deprecated::
   The canonical entry point is ``ml_ranking`` / ``aipha.research``.  This
   module remains for compatibility with historical factor-analysis jobs; it
   is not part of the default A-share research API and its non-canonical model
   choices are intentionally kept out of the new model registry.

用法:
    python scripts/train_ml_factor.py --universe csi500
    python scripts/train_ml_factor.py --universe csi500 --model lightgbm
    python scripts/train_ml_factor.py --universe csi500 --legacy-model double_ensemble

功能:
1. 加载 A 股日线 OHLCV 数据 (从缓存)
2. 按指数成分股筛选 Universe
3. 计算 Alpha158 量价因子
4. Walk-Forward 训练 (LightGBM / DoubleEnsemble)
5. OOS IC / ICIR / Top-Bottom Return 评估
6. 保存训练好的模型
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
import warnings

import numpy as np
import pandas as pd

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("train_ml_factor")
CANONICAL_ENTRYPOINT = "ml_ranking"
LEGACY_ENTRYPOINT = True

if __name__ != "__main__":
    warnings.warn(
        "scripts.train_ml_factor is a legacy compatibility module; use "
        "aipha/engines/aipha_ml_engine.py or the ml_ranking tool",
        DeprecationWarning,
        stacklevel=2,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    p = argparse.ArgumentParser(
        description="Legacy ML 训练脚本；新研究请使用 ml_ranking 入口",
    )
    p.add_argument("--universe", default="csi500",
                    choices=["csi300", "csi500", "csi800", "csi1000",
                             "csi2000", "micro", "all"])
    model_group = p.add_mutually_exclusive_group()
    model_group.add_argument(
        "--model", default="lightgbm", choices=["lightgbm", "elasticnet"],
        help="规范模型选择（默认 lightgbm；elasticnet 为线性基线）",
    )
    model_group.add_argument(
        "--legacy-model", dest="legacy_model",
        choices=["double_ensemble", "xgboost", "gru", "tra", "master", "gat", "hist"],
        help="显式启用历史实验模型；不属于规范研究 API",
    )
    p.add_argument("--start", default="2015-01-05")
    p.add_argument("--end", default="2026-02-28")
    p.add_argument("--train-years", type=int, default=3)
    p.add_argument("--predict-months", type=int, default=3)
    p.add_argument("--forward-days", type=int, default=5)
    p.add_argument("--label-skip-days", type=int, default=0,
                    help="T+1 alignment: skip N days after signal before return window "
                         "(1 = close(D+1)->close(D+6) instead of close(D)->close(D+5))")
    p.add_argument("--label-price-field", default="close",
                    choices=["close", "open"],
                    help="Price field for return label: close or open")
    p.add_argument("--output", default=None)
    p.add_argument(
        "--legacy-compat", action="store_true",
        help="显式运行历史缓存特征训练链路；新研究请使用 ml_ranking / research_retrain.py",
    )
    # --- 正则化参数 (B-01 fix: 降低 IS-OOS Sharpe 衰减) ---
    p.add_argument("--reg-alpha", type=float, default=0.1,
                    help="L1 regularization (default: 0.1)")
    p.add_argument("--reg-lambda", type=float, default=1.0,
                    help="L2 regularization (default: 1.0)")
    p.add_argument("--min-child-samples", type=int, default=50,
                    help="Min samples in leaf (default: 50)")
    p.add_argument("--subsample", type=float, default=0.8,
                    help="Row subsample ratio (default: 0.8)")
    p.add_argument("--colsample-bytree", type=float, default=0.8,
                    help="Column subsample ratio (default: 0.8)")
    p.add_argument("--max-depth", type=int, default=6)
    p.add_argument("--num-leaves", type=int, default=31)
    p.add_argument("--n-estimators", type=int, default=300)
    p.add_argument("--alt-factors", action="store_true",
                    help="Include alternative factors (chips/flow/analyst)")
    p.add_argument("--alt-cache", default="data/cache/alternative",
                    help="Alternative data cache directory")
    p.add_argument("--deep-fundamental", action="store_true", default=True,
                    help="Include deep fundamental factors from fina_indicator (ROE/ROA/margins). "
                         "Default=True since v2026.03 (R56 A4 fix). Use --no-deep-fundamental to disable.")
    p.add_argument("--no-deep-fundamental", dest="deep_fundamental", action="store_false",
                    help="Disable deep fundamental factors (fina_indicator)")
    p.add_argument("--behavioral", action="store_true", default=True,
                    help="Include behavioral bias factors (ABT/MAX/Overreaction). Default=True.")
    p.add_argument("--no-behavioral", dest="behavioral", action="store_false",
                    help="Disable behavioral bias factors")
    p.add_argument("--behavioral-only", action="store_true", default=False,
                    help="ONLY use behavioral + P1 anomaly factors (skip Alpha158). "
                         "Creates independent behavioral model with ~18 features for "
                         "diversification (low correlation with Alpha158-based models).")
    p.add_argument("--include-bse", action="store_true", default=False,
                    help="Include BSE (.BJ) stocks in csi2000/micro universe (old behavior before R40)")
    # --- Du 2025 中性化 ---
    p.add_argument("--neutralize", action="store_true", default=False,
                    help="Apply Du 2025 industry+size neutralization to features")
    p.add_argument("--no-neutralize", dest="neutralize", action="store_false",
                    help="Disable factor neutralization")
    # --- 高级 alpha 因子 ---
    p.add_argument("--advanced-alpha", action="store_true", default=False,
                    help="Add residual momentum + value/profitability factors")
    # --- P1 验证通过因子 (R125) ---
    p.add_argument("--p1-factors", action="store_true", default=False,
                    help="Add R125 validated P0+P1 factors (IVOL/Amihud/Skewness/52WkHigh/Reversal3M/ValueProf)")
    # --- P2 基本面/质量因子 (R127) ---
    p.add_argument("--p2-factors", action="store_true", default=False,
                    help="Add R127 fundamental/quality factors (SUE/Accruals/CFTP/ROE_Mom/TurnoverStab)")
    # --- P3 制度防御/反拥挤因子 (R128) ---
    p.add_argument("--p3-factors", action="store_true", default=False,
                    help="Add R128 regime-defensive factors (QualityComposite/LowBeta/ValueQuality/EarningsStab/CrowdingBeta)")
    # --- R129: 目标函数切换 ---
    p.add_argument("--objective", default="regression",
                    choices=["regression", "lambdarank"],
                    help="LightGBM objective: regression (pointwise MSE) or lambdarank (listwise NDCG)")
    p.add_argument("--n-rank-bins", type=int, default=5,
                    help="Number of relevance bins for lambdarank labels (default: 5)")
    # --- R130: 截断训练 ---
    p.add_argument("--truncate-quantile", type=float, default=0.0,
                    help="Truncated training: keep only Top/Bottom q%% samples "
                         "(0.0=full, 0.3=Top30%%+Bot30%%, discard middle 40%%)")
    # --- R132 B3: 目标变量去市值化 ---
    p.add_argument("--size-neutral", action="store_true", default=False,
                    help="Size-neutral label: regress fwd_return on log(circ_mv) per date, "
                         "use residual as training label (removes market-cap bias)")
    # --- R132+ P4: 分析师一致预期修正率 ---
    p.add_argument("--p4-factors", action="store_true", default=False,
                    help="Add P4 analyst revision factor (Phase 0 validated: IC=3.25%% @50-130B)")
    # --- R96: P5 价值投资因子 (007南大/管我财) ---
    p.add_argument("--p5-factors", action="store_true", default=False,
                    help="Add P5 value investor factors (R96: EP/Lynch/DV/OCF/Reversal60d/Composite/GARP)")
    # --- 挖掘因子 (P0: LLM/mutation自动挖掘因子集成) ---
    p.add_argument("--mined-factors", action="store_true", default=False,
                    help="Load passed mined factors from knowledge base and compute as features")
    p.add_argument("--mined-factors-kb",
                    default="output/auto_mining_v6/mutation_kb.json",
                    help="Path to mined factor knowledge base JSON "
                         "(default: output/auto_mining_v6/mutation_kb.json)")
    p.add_argument("--mined-factors-min-ic", type=float, default=0.03,
                    help="Minimum IC threshold for mined factor inclusion (default: 0.03)")
    p.add_argument("--mined-factors-min-icir", type=float, default=0.40,
                    help="Minimum ICIR threshold for mined factor inclusion (default: 0.40)")
    p.add_argument("--mined-factors-max", type=int, default=50,
                    help="Maximum number of mined factors to include, sorted by ICIR desc (default: 50)")
    # --- GRU 专有参数 ---
    p.add_argument("--gru-hidden-dim", type=int, default=128)
    p.add_argument("--gru-seq-len", type=int, default=20,
                    help="GRU lookback window in trading days (default: 20)")
    p.add_argument("--gru-epochs", type=int, default=80)
    p.add_argument("--gru-patience", type=int, default=10)
    p.add_argument("--gru-lr", type=float, default=1e-3)
    p.add_argument("--gru-batch-size", type=int, default=2048)
    # --- 增量训练 (P1-1) ---
    p.add_argument("--enable-incremental", action="store_true", default=False,
                    help="Enable warm-start incremental training between WF folds (LightGBM only)")
    p.add_argument("--incremental-n-estimators", type=int, default=100,
                    help="Additional boosting rounds for incremental folds (default: 100)")
    p.add_argument("--incremental-psi-threshold", type=float, default=0.20,
                    help="PSI threshold to abort incremental and train fresh (default: 0.20)")
    args = p.parse_args(argv)
    if args.legacy_model:
        args.model = args.legacy_model
        logger.warning(
            "Using legacy experimental model %s; canonical research supports "
            "LightGBM and ElasticNet only. Use ml_ranking for new runs.",
            args.legacy_model,
        )
    return args


def validate_cli_mode(args: argparse.Namespace) -> None:
    """Require an explicit opt-in before invoking the historical trainer."""
    if not args.legacy_compat:
        raise SystemExit(
            "train_ml_factor is a legacy cache-backed pipeline and is disabled by default; "
            "use ml_ranking train_tushare or scripts/research_retrain.py, or pass "
            "--legacy-compat to deliberately run a historical training job"
        )


def _apply_adj_factor(
    ohlcv: pd.DataFrame, cache_dir: Path,
) -> pd.DataFrame:
    """应用 adj_factor 前复权到原始 OHLCV 数据。

    qfq_price = raw_price * adj_factor / ref_factor
    ref_factor = 每只股票最新日期的 adj_factor。
    """
    af_parts: list[pd.DataFrame] = []
    for f in sorted(cache_dir.glob("adj_factor_*.parquet")):
        af_parts.append(pd.read_parquet(f))
    if not af_parts:
        logger.warning("无 adj_factor 缓存，使用原始价格")
        return ohlcv

    af = pd.concat(af_parts, ignore_index=True)
    # 计算参考因子（每只股票最新日期的 adj_factor）
    latest = af.sort_values("trade_date").groupby("ts_code").tail(1)
    ref_map = dict(zip(latest["ts_code"], latest["adj_factor"]))

    ohlcv = ohlcv.merge(
        af[["ts_code", "trade_date", "adj_factor"]],
        on=["ts_code", "trade_date"], how="left",
    )
    ohlcv["adj_factor"] = ohlcv["adj_factor"].fillna(1.0)
    ohlcv["ref_factor"] = ohlcv["ts_code"].map(ref_map).fillna(1.0)
    ratio = ohlcv["adj_factor"] / ohlcv["ref_factor"]

    for col in ["open", "high", "low", "close"]:
        if col in ohlcv.columns:
            ohlcv[col] = np.round(ohlcv[col] * ratio, 4)
    if "pre_close" in ohlcv.columns:
        ohlcv["pre_close"] = np.round(ohlcv["pre_close"] * ratio, 4)

    n_adj = (ratio != 1.0).sum()
    logger.info("前复权: %d/%d 行应用了 adj_factor", n_adj, len(ohlcv))
    ohlcv = ohlcv.drop(columns=["adj_factor", "ref_factor"], errors="ignore")
    return ohlcv


def load_daily_data(start: str, end: str) -> pd.DataFrame:
    """从缓存加载全市场日线 OHLCV + daily_basic。"""
    cache_dir = ROOT / "data" / "cache" / "equity_tushare"
    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)

    # 1) 加载日线 OHLCV (按月缓存: daily_YYYYMM.parquet)
    ohlcv_parts: list[pd.DataFrame] = []
    for f in sorted(cache_dir.glob("daily_2*.parquet")):
        # 提取 YYYYMM — 跳过 daily_basic_* 文件
        stem = f.stem  # daily_202301
        if "basic" in stem:
            continue
        ym = stem.replace("daily_", "")
        try:
            month_start = pd.Timestamp(ym[:4] + "-" + ym[4:6] + "-01")
        except ValueError:
            continue
        month_end = month_start + pd.offsets.MonthEnd(1)
        if month_end < start_dt or month_start > end_dt:
            continue
        try:
            part = pd.read_parquet(f)
            ohlcv_parts.append(part)
        except Exception as e:
            logger.warning("跳过 %s: %s", f.name, e)
    if not ohlcv_parts:
        raise RuntimeError(f"无日线数据: {cache_dir}")
    ohlcv = pd.concat(ohlcv_parts, ignore_index=True)
    logger.info("加载日线 OHLCV: %d 条", len(ohlcv))

    # 1.5) 应用 adj_factor 前复权 (bulk daily 是不复权数据)
    ohlcv = _apply_adj_factor(ohlcv, cache_dir)

    # 2) 标准化列名
    col_map = {
        "ts_code": "symbol", "trade_date": "datetime",
    }
    ohlcv.rename(columns=col_map, inplace=True)
    if "datetime" in ohlcv.columns:
        ohlcv["datetime"] = pd.to_datetime(ohlcv["datetime"], format="%Y%m%d")
    # 筛选日期范围
    ohlcv = ohlcv[(ohlcv["datetime"] >= start_dt) & (ohlcv["datetime"] <= end_dt)]

    # 确保基本列存在
    needed = ["datetime", "symbol", "open", "high", "low", "close", "vol"]
    for c in needed:
        if c not in ohlcv.columns:
            raise KeyError(f"缺少列 {c}, 现有: {list(ohlcv.columns)}")
    # vol → volume
    if "volume" not in ohlcv.columns and "vol" in ohlcv.columns:
        ohlcv["volume"] = ohlcv["vol"]
    ohlcv = ohlcv.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    logger.info("日期范围: %s ~ %s, 股票数: %d",
                ohlcv["datetime"].min().strftime("%Y-%m-%d"),
                ohlcv["datetime"].max().strftime("%Y-%m-%d"),
                ohlcv["symbol"].nunique())

    # 3) 加载 daily_basic 基本面数据 (PE/PB/MV/换手率/股息率)
    basic_parts: list[pd.DataFrame] = []
    for f in sorted(cache_dir.glob("daily_basic_2*.parquet")):
        stem = f.stem  # daily_basic_202301
        ym = stem.replace("daily_basic_", "")
        try:
            month_start = pd.Timestamp(ym[:4] + "-" + ym[4:6] + "-01")
        except ValueError:
            continue
        month_end = month_start + pd.offsets.MonthEnd(1)
        if month_end < start_dt or month_start > end_dt:
            continue
        try:
            part = pd.read_parquet(f)
            basic_parts.append(part)
        except Exception as e:
            logger.warning("跳过 basic %s: %s", f.name, e)

    if basic_parts:
        basic = pd.concat(basic_parts, ignore_index=True)
        basic.rename(columns={"ts_code": "symbol", "trade_date": "datetime"},
                     inplace=True)
        basic["datetime"] = pd.to_datetime(basic["datetime"], format="%Y%m%d")
        # 保留需要的基本面列
        fund_cols = ["pe_ttm", "pb", "total_mv", "circ_mv",
                     "turnover_rate", "dv_ratio", "dv_ttm"]
        keep = ["symbol", "datetime"] + [c for c in fund_cols if c in basic.columns]
        basic = basic[keep].drop_duplicates(["symbol", "datetime"])
        ohlcv = ohlcv.merge(basic, on=["symbol", "datetime"], how="left")
        n_fund = len([c for c in fund_cols if c in ohlcv.columns])
        logger.info("合并 daily_basic: %d 基本面列, 匹配率 %.1f%%",
                     n_fund,
                     ohlcv["pe_ttm"].notna().mean() * 100 if "pe_ttm" in ohlcv.columns else 0)
    else:
        logger.warning("未找到 daily_basic 数据, 跳过基本面因子")

    return ohlcv


def filter_universe(df: pd.DataFrame, universe: str, *, include_bse: bool = False) -> pd.DataFrame:
    """按指数成分股筛选 Universe。

    使用 union 成分股 (历史任意时点曾入选的所有股票)。
    支持: csi300, csi500, csi800, csi1000, csi2000, micro, all
    """
    cache_dir = ROOT / "data" / "cache" / "equity_tushare"

    # all = 全市场
    if universe == "all":
        logger.info("Universe all: 全市场 %d 股票", df["symbol"].nunique())
        return df

    # micro = 不在 CSI800+CSI1000 中的全部小微盘股 (排除 BSE)
    if universe == "micro":
        large_members: set[str] = set()
        for idx_code in ["000300_SH", "000905_SH", "000852_SH"]:
            for pattern in [f"index_members_union_{idx_code}*",
                            f"index_members_{idx_code}*"]:
                for f in sorted(cache_dir.glob(pattern)):
                    mdf = pd.read_parquet(f)
                    col = "con_code" if "con_code" in mdf.columns else "symbol"
                    large_members.update(mdf[col].unique())
        before = df["symbol"].nunique()
        df = df[~df["symbol"].isin(large_members)].copy()
        # 排除 BSE/NEEQ 股票
        n_bse = df["symbol"].str.endswith(".BJ").sum()
        if n_bse > 0:
            df = df[~df["symbol"].str.endswith(".BJ")].copy()
            logger.info("  排除 BSE(.BJ) 股票: %d 行", n_bse)
        after = df["symbol"].nunique()
        logger.info("Universe micro: %d → %d 股 (排除了 %d 大中盘股)",
                     before, after, len(large_members))
        return df

    # csi2000 = 全市场 排除 CSI800+CSI1000 + 排除 BSE (近似 CSI2000)
    if universe == "csi2000":
        large_members = set()
        for idx_code in ["000300_SH", "000905_SH", "000852_SH"]:
            for pattern in [f"index_members_union_{idx_code}*",
                            f"index_members_{idx_code}*"]:
                for f in sorted(cache_dir.glob(pattern)):
                    mdf = pd.read_parquet(f)
                    col = "con_code" if "con_code" in mdf.columns else "symbol"
                    large_members.update(mdf[col].unique())
        before = df["symbol"].nunique()
        df = df[~df["symbol"].isin(large_members)].copy()
        # 排除 BSE/NEEQ 股票 (920xxx.BJ, 833xxx.BJ 等)
        # CSI2000 仅包含 SSE(.SH) 和 SZSE(.SZ) 股票
        n_bse = df["symbol"].str.endswith(".BJ").sum()
        if n_bse > 0 and not include_bse:
            df = df[~df["symbol"].str.endswith(".BJ")].copy()
            logger.info("  排除 BSE(.BJ) 股票: %d 行", n_bse)
        elif n_bse > 0 and include_bse:
            logger.info("  保留 BSE(.BJ) 股票: %d 行 (--include-bse)", n_bse)
        after = df["symbol"].nunique()
        logger.info("Universe csi2000 (approx): %d → %d 股 (排除 %d 大中盘)",
                     before, after, len(large_members))
        return df

    idx_map = {
        "csi300": "000300_SH",
        "csi500": "000905_SH",
        "csi800": None,  # CSI800 = CSI300 + CSI500
        "csi1000": "000852_SH",
    }
    # 查找 union 成分股文件
    members: set[str] = set()
    if universe == "csi800":
        # CSI800 = union of CSI300 + CSI500
        for idx_code in ["000300_SH", "000905_SH"]:
            union_files = sorted(cache_dir.glob(
                f"index_members_union_{idx_code}*.parquet"
            ))
            if union_files:
                mdf = pd.read_parquet(union_files[-1])
                if "con_code" in mdf.columns:
                    members.update(mdf["con_code"].unique())
                elif "symbol" in mdf.columns:
                    members.update(mdf["symbol"].unique())
    else:
        idx_code = idx_map.get(universe)
        if not idx_code:
            logger.warning("未知 universe: %s, 使用全市场", universe)
            return df
        union_files = sorted(cache_dir.glob(
            f"index_members_union_{idx_code}*.parquet"
        ))
        if not union_files:
            # 尝试 latest
            latest = sorted(cache_dir.glob(
                f"index_members_{idx_code}*.parquet"
            ))
            if latest:
                union_files = [latest[-1]]
        if union_files:
            mdf = pd.read_parquet(union_files[-1])
            col = "con_code" if "con_code" in mdf.columns else "symbol"
            members.update(mdf[col].unique())

    if not members:
        logger.warning("未找到 %s 成分股数据, 使用全市场", universe)
        return df

    before = df["symbol"].nunique()
    df = df[df["symbol"].isin(members)].copy()
    after = df["symbol"].nunique()
    logger.info("Universe %s: %d → %d 股票", universe, before, after)
    return df


# =========================================================================
# 深度基本面因子 (fina_indicator 季报级)
# =========================================================================
_DEEP_FINA_COLS = [
    "roe", "roa", "grossprofit_margin", "netprofit_margin",
    "debt_to_assets", "current_ratio", "quick_ratio",
    "dt_netprofit_yoy", "op_yoy", "cfps", "eps", "bps",
]


def _load_fina_indicator_bulk(cache_dir: Path, symbols: list[str]) -> pd.DataFrame:
    """批量加载 fina_indicator parquet (PIT 对齐)。

    从 data/cache/equity_tushare/fina_indicator_{code}.parquet 批量读取,
    仅保留 _DEEP_FINA_COLS + ann_date + end_date + ts_code。
    """
    parts: list[pd.DataFrame] = []
    loaded = 0
    for sym in symbols:
        fname = f"fina_indicator_{sym.replace('.', '_')}.parquet"
        fpath = cache_dir / fname
        if not fpath.exists():
            continue
        try:
            part = pd.read_parquet(fpath)
            keep = ["ts_code", "ann_date", "end_date"] + [
                c for c in _DEEP_FINA_COLS if c in part.columns
            ]
            part = part[keep].copy()
            part["symbol"] = sym
            parts.append(part)
            loaded += 1
        except Exception:
            continue
    if not parts:
        logger.warning("无 fina_indicator 数据")
        return pd.DataFrame()
    fina = pd.concat(parts, ignore_index=True)
    # 转数值
    for col in _DEEP_FINA_COLS:
        if col in fina.columns:
            fina[col] = pd.to_numeric(fina[col], errors="coerce")
    # ann_date 转 datetime — 无效/缺失值变 NaT，必须剔除否则 merge_asof 报错
    fina["ann_date"] = pd.to_datetime(fina["ann_date"], format="%Y%m%d", errors="coerce")
    fina = fina.dropna(subset=["ann_date"])
    # 去重: 同 symbol + ann_date 取最新 end_date
    fina = fina.sort_values(["symbol", "ann_date", "end_date"])
    fina = fina.drop_duplicates(subset=["symbol", "ann_date"], keep="last")
    logger.info("加载 fina_indicator: %d 标的, %d 条记录", loaded, len(fina))
    return fina


def _pit_merge_fina(
    daily: pd.DataFrame, fina: pd.DataFrame,
) -> pd.DataFrame:
    """PIT 对齐: 每行日线取 ann_date <= datetime 的最新财报。

    使用逐 symbol pd.merge_asof 实现 PIT 合并。
    """
    if fina.empty:
        return daily
    fina_cols = ["symbol", "ann_date"] + [
        c for c in _DEEP_FINA_COLS if c in fina.columns
    ]
    fina_sub = fina[fina_cols].copy()
    # 财务列降精度减少内存（float64→float32，PIT 合并后 concat 约需 5GB）
    fina_f32_cols = [c for c in _DEEP_FINA_COLS if c in fina_sub.columns]
    for c in fina_f32_cols:
        fina_sub[c] = fina_sub[c].astype(np.float32)
    parts: list[pd.DataFrame] = []
    for sym, grp in daily.groupby("symbol"):
        grp_sorted = grp.sort_values("datetime")
        fina_sym = fina_sub[fina_sub["symbol"] == sym].sort_values("ann_date")
        if fina_sym.empty:
            parts.append(grp_sorted)
            continue
        m = pd.merge_asof(
            grp_sorted, fina_sym.drop(columns=["symbol"]),
            left_on="datetime", right_on="ann_date",
            direction="backward",
        )
        parts.append(m)
    merged = pd.concat(parts, ignore_index=True)
    n_matched = merged[_DEEP_FINA_COLS[0]].notna().sum() if _DEEP_FINA_COLS[0] in merged.columns else 0
    logger.info("PIT 合并: %d/%d 行有财报数据 (%.1f%%)",
                n_matched, len(merged), 100 * n_matched / max(len(merged), 1))
    return merged


def _add_deep_fundamental_factors(
    df: pd.DataFrame, cache_dir: Path,
) -> int:
    """加载 fina_indicator 并构建深度基本面因子。

    因子工程:
    1. PIT 合并季报数据
    2. 前向填充 (同 symbol, 最多 60 交易日 ≈ 3 个月)
    3. 截面 rank
    4. 时序变化率 (与上一期 vs ann_date 变化)

    Returns:
        新增因子列数。
    """
    symbols = df["symbol"].unique().tolist()
    fina = _load_fina_indicator_bulk(cache_dir, symbols)
    if fina.empty:
        return 0
    # PIT 合并
    df_merged = _pit_merge_fina(df, fina)
    # 把合并后的列写回 df (inplace 更新)
    avail = [c for c in _DEEP_FINA_COLS if c in df_merged.columns]
    for col in avail:
        df[col] = df_merged[col].values
    # 前向填充 (同 symbol 内, 最多 60 天)
    for col in avail:
        df[col] = df.groupby("symbol")[col].ffill(limit=60)
    added = 0
    # 截面 rank
    for col in avail:
        rank_col = f"cs_rank_fina_{col}"
        df[rank_col] = df.groupby("datetime")[col].rank(pct=True)
        added += 1
    # 复合因子
    if "roe" in avail and "debt_to_assets" in avail:
        df["roe_debt_quality"] = df["roe"] / (df["debt_to_assets"].clip(10) / 100)
        df["cs_rank_roe_debt_q"] = df.groupby("datetime")["roe_debt_quality"].rank(pct=True)
        added += 2
    if "eps" in avail and "cfps" in avail:
        df["accruals_ratio"] = (df["eps"] - df["cfps"]) / df["eps"].abs().clip(0.01)
        df["accruals_ratio"] = df["accruals_ratio"].clip(-5, 5)
        df["cs_rank_accruals"] = df.groupby("datetime")["accruals_ratio"].rank(pct=True)
        added += 2
    if "grossprofit_margin" in avail:
        # Gross Margin 稳定性: 20日标准差
        df["gm_std_20"] = df.groupby("symbol")["grossprofit_margin"].transform(
            lambda s: s.rolling(20, min_periods=5).std()
        )
        df["cs_rank_gm_std"] = df.groupby("datetime")["gm_std_20"].rank(pct=True)
        added += 2
    logger.info("深度基本面因子: 新增 %d 个 (基于 %d 个 fina_indicator 字段)",
                added, len(avail))
    return added


def _load_industry_for_neutralize(
    df: pd.DataFrame, start: str, end: str,
) -> None:
    """加载申万行业分类并合并到 df (原地添加 'industry' 列)。

    优先使用申万 L1 分类, 降级到 stock_basic.industry 字段。
    """
    if "industry" in df.columns:
        n_valid = df["industry"].notna().sum()
        if n_valid > len(df) * 0.5:
            logger.info("行业列已存在 (%d/%d 有效), 跳过加载", n_valid, len(df))
            return
    try:
        from aipha.data.tushare_equity_provider import TuShareEquityProvider
        cache_dir = ROOT / "data" / "cache" / "equity_tushare"
        provider = TuShareEquityProvider(cache_dir=str(cache_dir))
        ind_map = provider.load_sw_industry_mapping(level="L1")
        if not ind_map:
            logger.warning("申万行业为空, 尝试 stock_basic 行业")
            ind_map = provider.load_industry_mapping()
        if ind_map:
            df["industry"] = df["symbol"].map(ind_map)
            n_matched = df["industry"].notna().sum()
            logger.info("行业映射: %d/%d 匹配 (%d 个行业)",
                        n_matched, len(df), len(set(ind_map.values())))
        else:
            logger.warning("无行业数据可用")
    except Exception as e:
        logger.warning("加载行业数据失败 (非阻塞): %s", e)


def _load_and_compute_mined_factors(
    df: pd.DataFrame,
    kb_path: str,
    min_ic: float = 0.03,
    min_icir: float = 0.40,
    max_factors: int = 50,
) -> int:
    """加载挖掘因子知识库并计算因子列。

    从 mutation_kb.json 中加载 passed=True 且满足 IC/ICIR 门槛的因子，
    使用 FactorExpressionParser 计算因子值并添加到 df 中。

    Args:
        df: 包含 datetime/symbol/OHLCV 列的 DataFrame (inplace 修改)
        kb_path: mutation_kb.json 路径
        min_ic: 最低 IC 门槛
        min_icir: 最低 ICIR 门槛

    Returns:
        成功添加的因子列数
    """
    import json
    from aipha.factors.factor_expression_parser import FactorExpressionParser

    kb_file = Path(kb_path)
    if not kb_file.is_absolute():
        kb_file = ROOT / kb_file
    if not kb_file.exists():
        logger.warning("知识库不存在: %s — 跳过挖掘因子", kb_file)
        return 0

    try:
        with open(kb_file, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except Exception as e:
        logger.error("知识库加载失败: %s", e)
        return 0

    passed = [
        e for e in entries
        if e.get("passed")
        and e.get("ic_mean", 0) >= min_ic
        and e.get("icir", 0) >= min_icir
    ]
    # 按 ICIR 降序排列，优先计算高质量因子
    passed.sort(key=lambda e: e.get("icir", 0), reverse=True)
    if max_factors > 0 and len(passed) > max_factors:
        logger.info("挖掘因子: 从 %d 个通过筛选的因子中取 top %d (by ICIR)",
                    len(passed), max_factors)
        passed = passed[:max_factors]
    logger.info(
        "挖掘因子知识库: %d 总条目 → %d 使用 (IC≥%.3f, ICIR≥%.2f, max=%d)",
        len(entries), len(passed), min_ic, min_icir, max_factors,
    )
    if not passed:
        return 0

    # 按 ICIR 降序排列，优先计算高质量因子
    passed.sort(key=lambda e: e.get("icir", 0), reverse=True)

    parser = FactorExpressionParser()
    computed: dict[str, pd.Series] = {}

    for entry in passed:
        name = entry["name"]
        expression = entry["expression"]
        if name in df.columns:
            continue
        try:
            parsed = parser.parse(expression, name=name)
            if not parsed.is_valid:
                logger.debug("解析失败 %s: %s", name, expression)
                continue
            factor_series = parser.execute(parsed, df)
            computed[name] = factor_series
        except Exception as e:
            logger.debug("计算挖掘因子失败 %s: %s (非阻塞)", name, e)
            continue

    # 使用 .values 避免 reindex 时的额外拷贝
    n_added = len(computed)
    if computed:
        new_cols = pd.DataFrame(computed, index=df.index)
        del computed  # 释放中间 dict
        for col in new_cols.columns:
            df[col] = new_cols[col].values
        del new_cols

    logger.info("挖掘因子: 成功计算 %d/%d 列", n_added, len(passed))
    return n_added


def _compute_behavioral_only_features(df: pd.DataFrame) -> pd.DataFrame:
    """仅计算行为异常因子 (跳过 Alpha158), 用于独立行为因子模型.

    特征来源:
    - Behavioral (R120): ABT/MAX/Overreaction + cs_ranks (6列)
    - P1 (R125): IVOL/Amihud/Skewness/52WkHigh/Reversal3M/ValueProf + cs_ranks (12列)
    总计: ~18 个特征, 全部为行为金融学/市场异象类因子。
    """
    t0 = time.time()
    result = df.copy()

    # Behavioral: ABT/MAX/Overreaction (6列)
    n_beh = 0
    try:
        from aipha.factors.behavioral_ml import add_behavioral_factors
        n_beh = add_behavioral_factors(result)
    except Exception as e:
        logger.warning("行为因子计算失败: %s", e)

    # P1: IVOL/Amihud/Skewness/52WkHigh/Reversal3M/ValueProf (12列)
    n_p1 = 0
    try:
        from aipha.factors.p1_factors_ml import add_p1_factors
        n_p1 = add_p1_factors(result)
    except Exception as e:
        logger.warning("P1因子计算失败: %s", e)

    elapsed = time.time() - t0
    total = n_beh + n_p1
    logger.info(
        "Behavioral-only 特征完成: %d 因子 "
        "(行为偏差 %d + P1异常 %d), 耗时 %.1fs",
        total, n_beh, n_p1, elapsed,
    )
    return result


def compute_features(
    df: pd.DataFrame, *, deep_fundamental: bool = False,
    behavioral: bool = True,
    behavioral_only: bool = False,
) -> pd.DataFrame:
    """计算 Alpha158 量价因子 + 基本面因子。

    Args:
        deep_fundamental: 启用深度基本面因子 (fina_indicator 季报级)。
        behavioral_only: 仅使用行为因子+P1异常因子 (跳过Alpha158)，
                         用于构建独立行为因子模型 (Phase 1突破计划)。
    """
    if behavioral_only:
        return _compute_behavioral_only_features(df)

    from aipha.factors.alpha158 import compute_alpha158

    logger.info("计算 Alpha158 因子 (%d 条数据)...", len(df))
    t0 = time.time()
    result = compute_alpha158(df)

    # 计算基本面因子 (如果 daily_basic 列存在)
    fund_cols_available = [
        c for c in ["pe_ttm", "pb", "total_mv", "circ_mv",
                     "turnover_rate", "dv_ratio", "dv_ttm"]
        if c in result.columns
    ]
    n_fund_factors = 0
    if fund_cols_available:
        logger.info("计算基本面因子 (基于 %d 个原始列)...", len(fund_cols_available))
        n_fund_factors = _add_fundamental_factors(result, fund_cols_available)
        logger.info("基本面因子: 新增 %d 个", n_fund_factors)

    # 深度基本面因子 (fina_indicator)
    n_deep = 0
    if deep_fundamental:
        cache_dir = ROOT / "data" / "cache" / "equity_tushare"
        n_deep = _add_deep_fundamental_factors(result, cache_dir)

    # 行为偏差因子 (R120/R121 IC 验证通过: ABT/MAX/Overreaction)
    n_behavioral = 0
    if behavioral:
        try:
            from aipha.factors.behavioral_ml import add_behavioral_factors
            n_behavioral = add_behavioral_factors(result)
        except Exception as e:
            logger.warning("行为偏差因子计算失败 (非阻塞): %s", e)

    elapsed = time.time() - t0
    # 统计生成的因子列数
    base_cols = {"datetime", "symbol", "open", "high", "low", "close",
                 "volume", "vol", "amount", "pre_close", "change", "pct_chg",
                 "pe_ttm", "pb", "total_mv", "circ_mv",
                 "turnover_rate", "dv_ratio", "dv_ttm"}
    factor_cols = [c for c in result.columns if c not in base_cols]
    logger.info("特征完成: %d 因子 (Alpha158 + %d 浅层基本面 + %d 深层基本面 + %d 行为偏差), 耗时 %.1fs",
                len(factor_cols), n_fund_factors, n_deep, n_behavioral, elapsed)
    return result


def _load_and_merge_alt_factors(
    df: pd.DataFrame, alt_cache: str, start: str, end: str,
) -> pd.DataFrame:
    """加载替代数据并合并因子到主 DataFrame。

    尝试加载 chips/flow/analyst 三类替代数据。
    任一数据源不可用时，跳过该源并继续。
    """
    from aipha.data.tushare_alternative_provider import TuShareAlternativeProvider
    from aipha.factors.alternative_factors import (
        compute_analyst_factors,
        compute_chips_factors,
        compute_flow_factors,
        merge_alternative_factors,
    )

    logger.info("加载替代数据因子 (cache=%s)...", alt_cache)
    provider = TuShareAlternativeProvider(cache_dir=alt_cache)

    start_clean = start.replace("-", "")
    end_clean = end.replace("-", "")
    chips_factors = None
    flow_factors = None
    analyst_factors = None
    margin_factors = None

    # 尝试加载筹码分布（按日期范围，逐日缓存）
    try:
        from aipha.data.calendar import CNStockCalendar
        from datetime import date as _date

        cal = CNStockCalendar()
        sd = _date(int(start_clean[:4]), int(start_clean[4:6]), int(start_clean[6:8]))
        ed = _date(int(end_clean[:4]), int(end_clean[4:6]), int(end_clean[6:8]))
        dates = cal.get_trading_days(sd, ed)
        chips_parts: list[pd.DataFrame] = []
        for d in dates:
            ds = d.strftime("%Y%m%d")
            part = provider.load_chips_bulk(ds)
            if not part.empty:
                chips_parts.append(part)
        if chips_parts:
            chips_raw = pd.concat(chips_parts, ignore_index=True)
            chips_factors = compute_chips_factors(chips_raw)
            logger.info("筹码因子: %d 行", len(chips_factors))
    except Exception as e:
        logger.warning("筹码数据加载失败: %s", e)

    # 尝试加载融资融券
    try:
        from aipha.factors.alternative_factors import compute_margin_factors

        margin_raw = provider.load_margin_range(start_clean, end_clean)
        if not margin_raw.empty:
            margin_factors = compute_margin_factors(margin_raw)
            logger.info("融资融券因子: %d 行", len(margin_factors))
    except Exception as e:
        logger.warning("融资融券加载失败: %s", e)

    # 尝试加载资金流（按日期范围）
    try:
        flow_raw = provider.load_moneyflow_range(start_clean, end_clean)
        if not flow_raw.empty:
            flow_factors = compute_flow_factors(flow_raw)
            logger.info("资金流因子: %d 行", len(flow_factors))
    except Exception as e:
        logger.warning("资金流加载失败: %s", e)

    # 尝试加载分析师预测
    try:
        analyst_raw = provider.load_analyst_forecasts(start_clean, end_clean)
        if not analyst_raw.empty:
            analyst_factors = compute_analyst_factors(analyst_raw, df)
            logger.info("分析师因子: %d 行", len(analyst_factors))
    except Exception as e:
        logger.warning("分析师数据加载失败: %s", e)

    # 合并
    df = merge_alternative_factors(
        df, chips_factors=chips_factors,
        flow_factors=flow_factors,
        analyst_factors=analyst_factors,
        margin_factors=margin_factors,
    )
    alt_cols = [c for c in df.columns
                if c.startswith(("chips_", "flow_", "analyst_", "margin_"))]
    logger.info("替代因子合并完成: %d 个新因子列", len(alt_cols))
    return df


def _add_fundamental_factors(df: pd.DataFrame, cols: list[str]) -> int:
    """基于 daily_basic 原始列计算衍生基本面因子。

    处理逻辑:
    1. 填充缺失值 (前向填充, 最多5天)
    2. 截面标准化 (z-score)
    3. 时序变化率 (5/20天)
    4. 时序排名

    Returns:
        新增因子数量
    """
    grp = df["symbol"]
    added = 0

    for col in cols:
        # 前向填充缺失 (同 symbol 内)
        df[col] = df.groupby("symbol")[col].ffill(limit=5)

    # --- 截面因子 (每天截面 rank / z-score) ---
    for col in cols:
        # 截面 rank (0~1)
        rank_col = f"cs_rank_{col}"
        df[rank_col] = df.groupby("datetime")[col].rank(pct=True)
        added += 1

    # --- 时序因子 ---
    for col in cols:
        s = df[col]
        for lag in [5, 20]:
            # 变化率: current / lag-day-ago
            delayed = df.groupby("symbol")[col].shift(lag)
            chg_col = f"{col}_chg_{lag}d"
            df[chg_col] = s / (delayed + 1e-8) - 1
            df[chg_col] = df[chg_col].clip(-5, 5)  # 截断极端值
            added += 1

    # --- 复合因子 ---
    if "pe_ttm" in cols and "pb" in cols:
        # EP (1/PE) — 盈利收益率
        df["ep_ttm"] = 1.0 / df["pe_ttm"].clip(lower=1)
        df["ep_ttm"] = df["ep_ttm"].fillna(0)
        df["cs_rank_ep"] = df.groupby("datetime")["ep_ttm"].rank(pct=True)
        added += 2

    if "pb" in cols:
        # BP (1/PB) — 账面价值收益率
        df["bp"] = 1.0 / df["pb"].clip(lower=0.1)
        df["bp"] = df["bp"].fillna(0)
        df["cs_rank_bp"] = df.groupby("datetime")["bp"].rank(pct=True)
        added += 2

    if "circ_mv" in cols:
        # 对数流通市值
        df["log_circ_mv"] = np.log1p(df["circ_mv"].clip(lower=0))
        df["cs_rank_log_mv"] = df.groupby("datetime")["log_circ_mv"].rank(pct=True)
        added += 2

    if "dv_ttm" in cols:
        # 股息率截面排名
        df["dv_ttm_clean"] = df["dv_ttm"].fillna(0).clip(0, 30)
        df["cs_rank_dv"] = df.groupby("datetime")["dv_ttm_clean"].rank(pct=True)
        added += 2

    return added


def evaluate_oos(
    oos_df: pd.DataFrame,
    top_k: int = 50,
    forward_days: int = 5,
    t1_discount_factor: float = 0.90,
) -> dict:
    """评估 OOS 预测: IC / ICIR / Top-Bottom Return。

    forward_days 用于去重叠: 每 forward_days 天取一个调仓点计算收益。
    t1_discount_factor: A股 T+1 机制对信号的折扣系数（默认 0.90），
        输出 sharpe_t1_adjusted = ic_ir * t1_discount_factor。
        参见项目规范 §5.6 及 R52 P10 原则D。
    """
    results: dict = {}
    if "fwd_return" not in oos_df.columns or "pred_score" not in oos_df.columns:
        return {"error": "missing columns"}

    # 1) 逐日 IC (Rank Spearman) — all days
    daily_ic = oos_df.groupby("datetime").apply(
        lambda g: g["pred_score"].corr(g["fwd_return"], method="spearman")
        if len(g) > 20 else np.nan,
        include_groups=False,
    ).dropna()
    results["mean_ic"] = float(daily_ic.mean())
    results["std_ic"] = float(daily_ic.std())
    results["ic_ir"] = (
        float(daily_ic.mean() / daily_ic.std())
        if daily_ic.std() > 0 else 0.0
    )
    results["ic_ir_t1_adjusted"] = round(results["ic_ir"] * t1_discount_factor, 4)
    results["t1_discount_factor"] = t1_discount_factor
    results["ic_positive_ratio"] = float((daily_ic > 0).mean())
    results["n_ic_days"] = int(len(daily_ic))

    # 2) Top/Bottom 组合收益 — 去重叠 (每 forward_days 天取一次)
    all_dates = sorted(oos_df["datetime"].unique())
    rebal_dates = all_dates[::forward_days]
    rebal_df = oos_df[oos_df["datetime"].isin(set(rebal_dates))]

    def _group_return(g: pd.DataFrame) -> pd.Series:
        g_sorted = g.sort_values("pred_score", ascending=False)
        n = min(top_k, len(g_sorted) // 5)
        if n < 5:
            return pd.Series({"top": np.nan, "bottom": np.nan})
        top_ret = g_sorted.head(n)["fwd_return"].mean()
        bot_ret = g_sorted.tail(n)["fwd_return"].mean()
        return pd.Series({"top": top_ret, "bottom": bot_ret})

    tb = rebal_df.groupby("datetime").apply(
        _group_return, include_groups=False,
    ).dropna()
    periods_per_year = 244.0 / forward_days
    if not tb.empty:
        n_periods = len(tb)
        n_years = n_periods / periods_per_year
        top_cum = (1 + tb["top"]).prod()
        bot_cum = (1 + tb["bottom"]).prod()
        ls_cum = top_cum / bot_cum
        results["top_annual_ret"] = float(
            top_cum ** (1 / max(n_years, 0.5)) - 1
        )
        results["bottom_annual_ret"] = float(
            bot_cum ** (1 / max(n_years, 0.5)) - 1
        )
        results["long_short_annual"] = float(
            ls_cum ** (1 / max(n_years, 0.5)) - 1
        )
        results["top_avg_period_ret"] = float(tb["top"].mean())
        results["bottom_avg_period_ret"] = float(tb["bottom"].mean())
        results["n_rebalance_periods"] = n_periods
    return results


def simulate_portfolio(
    oos_df: pd.DataFrame,
    top_k: int = 50,
    rebalance_days: int = 5,
    cost_bps: float = 30.0,
) -> dict:
    """基于 OOS 预测模拟 Top-K 等权组合。

    每 rebalance_days 天根据 pred_score 选 top_k 股票等权持有，
    持仓期间按实际 fwd_return (5 日收益) 计算组合收益。
    """
    if "pred_score" not in oos_df.columns or "fwd_return" not in oos_df.columns:
        return {"error": "missing columns"}

    all_dates = sorted(oos_df["datetime"].unique())
    rebal_dates = all_dates[::rebalance_days]
    cost_rate = cost_bps / 10000.0

    portfolio_rets: list[float] = []
    prev_holdings: set[str] = set()

    for dt in rebal_dates:
        day_df = oos_df[oos_df["datetime"] == dt]
        if len(day_df) < top_k:
            continue
        # 选 top_k
        top = day_df.nlargest(top_k, "pred_score")
        new_holdings = set(top["symbol"])
        # 换手率
        if prev_holdings:
            turnover = len(new_holdings - prev_holdings) / top_k
        else:
            turnover = 1.0
        # 等权平均收益
        period_ret = top["fwd_return"].mean()
        # 扣成本 (双边 * 换手率)
        net_ret = period_ret - 2 * cost_rate * turnover
        portfolio_rets.append(net_ret)
        prev_holdings = new_holdings

    if not portfolio_rets:
        return {"error": "no rebalance periods"}

    rets = np.array(portfolio_rets)
    cum = (1 + rets).prod()
    n_years = len(rets) / (244.0 / rebalance_days)
    ann_ret = cum ** (1 / max(n_years, 0.25)) - 1
    ann_vol = rets.std() * np.sqrt(244.0 / rebalance_days)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0

    # 最大回撤
    cum_curve = np.cumprod(1 + rets)
    peak = np.maximum.accumulate(cum_curve)
    dd = (peak - cum_curve) / peak
    max_dd = dd.max()

    avg_turnover = np.mean(
        [len(set()) for _ in range(1)]  # placeholder
    )

    result = {
        "annual_return": float(ann_ret),
        "annual_volatility": float(ann_vol),
        "sharpe_ratio": float(sharpe),
        "max_drawdown": float(max_dd),
        "calmar_ratio": float(ann_ret / max_dd) if max_dd > 0 else 0.0,
        "total_return": float(cum - 1),
        "n_periods": len(rets),
        "n_years": round(n_years, 2),
        "cost_bps": cost_bps,
        "avg_period_return": float(rets.mean()),
        "win_rate": float((rets > 0).mean()),
    }
    logger.info(
        "组合模拟: AR=%.1f%%, MDD=%.1f%%, SR=%.2f, Calmar=%.2f, WR=%.1f%%",
        result["annual_return"] * 100, result["max_drawdown"] * 100,
        result["sharpe_ratio"], result["calmar_ratio"],
        result["win_rate"] * 100,
    )
    return result


def main() -> None:
    """主入口。"""
    args = parse_args()
    validate_cli_mode(args)
    t_start = time.time()

    # 1) 加载数据
    logger.info("=" * 60)
    logger.info("ML 选股模型训练 — %s / %s", args.universe, args.model)
    logger.info("日期: %s ~ %s", args.start, args.end)
    logger.info("=" * 60)

    df = load_daily_data(args.start, args.end)
    df = filter_universe(df, args.universe, include_bse=getattr(args, 'include_bse', False))

    # 2) 计算特征
    behavioral_only = getattr(args, 'behavioral_only', False)
    df = compute_features(df, deep_fundamental=args.deep_fundamental,
                           behavioral=args.behavioral,
                           behavioral_only=behavioral_only)

    # behavioral-only 模式下跳过额外因子（P1已在内部添加）
    if behavioral_only:
        logger.info("Behavioral-only 模式: 跳过高级/P2/P3/P4/P5/挖掘/替代因子")

    # 2.3) 高级 alpha 因子 (残差动量 + 价值盈利复合)
    if not behavioral_only and args.advanced_alpha:
        from aipha.factors.advanced_alpha import add_advanced_alpha_factors
        n_adv = add_advanced_alpha_factors(df)
        logger.info("高级 alpha 因子: 新增 %d 列", n_adv)

    # 2.4) P1 验证通过因子 (R125: IVOL/Amihud/Skewness/52WkHigh/Reversal3M/ValueProf)
    if not behavioral_only and args.p1_factors:
        from aipha.factors.p1_factors_ml import add_p1_factors
        n_p1 = add_p1_factors(df)
        logger.info("P1 验证通过因子: 新增 %d 列", n_p1)

    # 2.4b) P2 基本面/质量因子 (R127: SUE/Accruals/CFTP/ROE_Mom/TurnoverStab)
    if not behavioral_only and args.p2_factors:
        from aipha.factors.p2_factors_ml import add_p2_factors
        n_p2 = add_p2_factors(df)
        logger.info("P2 基本面因子: 新增 %d 列", n_p2)

    # 2.4c) P3 制度防御/反拥挤因子 (R128: QMJ/LowBeta/ValueQuality/EarningsStab/CrowdingBeta)
    if not behavioral_only and args.p3_factors:
        from aipha.factors.p3_factors_ml import add_p3_factors
        n_p3 = add_p3_factors(df)
        logger.info("P3 制度防御因子: 新增 %d 列", n_p3)

    # 2.4d) P4 分析师修正因子 (R132+ Phase 0: analyst_rev/disp/n_analysts/rev_rank)
    if not behavioral_only and args.p4_factors:
        from aipha.factors.p4_factors_ml import add_p4_factors
        n_p4 = add_p4_factors(df)
        logger.info("P4 分析师因子: 新增 %d 列", n_p4)

    # 2.4e) P5 价值投资因子 (R96: 007南大/管我财, EP/Lynch/DV/Reversal60d/Composite/GARP)
    if not behavioral_only and args.p5_factors:
        from aipha.factors.p5_value_factors_ml import add_p5_value_factors
        n_p5 = add_p5_value_factors(df)
        logger.info("P5 价值因子: 新增 %d 列", n_p5)

    # 2.6) 挖掘因子 (P0: LLM/mutation 自动发现的高 IC 因子)
    if not behavioral_only and args.mined_factors:
        n_mined = _load_and_compute_mined_factors(
            df,
            kb_path=args.mined_factors_kb,
            min_ic=args.mined_factors_min_ic,
            min_icir=args.mined_factors_min_icir,
            max_factors=args.mined_factors_max,
        )
        logger.info("挖掘因子: 新增 %d 列", n_mined)

    # 2.5) 替代因子 (可选)
    if not behavioral_only and args.alt_factors:
        df = _load_and_merge_alt_factors(
            df, args.alt_cache, args.start, args.end,
        )

    # 2.7) Du 2025 因子中性化 (行业 + 规模含二次项)
    if not behavioral_only and args.neutralize:
        from aipha.factors.ml_neutralizer import neutralize_ml_features
        base_cols = {
            "datetime", "symbol", "open", "high", "low", "close",
            "volume", "vol", "amount", "pre_close", "change", "pct_chg",
            "pe_ttm", "pb", "total_mv", "circ_mv",
            "turnover_rate", "dv_ratio", "dv_ttm", "label",
            "industry",
        }
        feat_cols = [c for c in df.columns if c not in base_cols]
        # 加载行业数据
        _load_industry_for_neutralize(df, args.start, args.end)
        df = neutralize_ml_features(
            df, feat_cols, industry_col="industry", cap_col="circ_mv",
        )
        # 中性化后删除行业列 (非数值, 不可作为训练特征)
        df.drop(columns=["industry"], inplace=True, errors="ignore")

    # 3) WF 训练
    from aipha.models.ml_stock_selector import MLSelectorConfig, MLStockSelector

    model_cfg = MLSelectorConfig(
        model_type=args.model,
        allow_experimental_models=bool(args.legacy_model),
        objective=args.objective,
        n_rank_bins=args.n_rank_bins,
        truncate_quantile=args.truncate_quantile,
        size_neutral_label=args.size_neutral,
        forward_days=args.forward_days,
        label_skip_days=args.label_skip_days,
        label_price_field=args.label_price_field,
        train_years=args.train_years,
        predict_months=args.predict_months,
        n_estimators=args.n_estimators,
        learning_rate=0.05,
        max_depth=args.max_depth,
        num_leaves=args.num_leaves,
        reg_alpha=args.reg_alpha,
        reg_lambda=args.reg_lambda,
        min_child_samples=args.min_child_samples,
        subsample=args.subsample,
        colsample_bytree=args.colsample_bytree,
        de_num_models=6,
        de_epochs=100,
        de_early_stopping=50,
        gru_hidden_dim=args.gru_hidden_dim,
        gru_seq_len=args.gru_seq_len,
        gru_epochs=args.gru_epochs,
        gru_patience=args.gru_patience,
        gru_lr=args.gru_lr,
        gru_batch_size=args.gru_batch_size,
        enable_incremental=args.enable_incremental,
        incremental_n_estimators=args.incremental_n_estimators,
        incremental_psi_threshold=args.incremental_psi_threshold,
    )
    selector = MLStockSelector(model_cfg)

    logger.info("开始 Walk-Forward 训练 (model=%s)...", args.model)
    oos_df = selector.walk_forward_train(df)

    # 4) 评估
    logger.info("评估 OOS 预测...")
    metrics = evaluate_oos(oos_df, top_k=50, forward_days=args.forward_days)
    wf_summary = selector.get_wf_summary()

    # 4.1) 确定输出路径
    output_dir = args.output or f"models/ml_{args.universe}_{args.model}"
    output_path = ROOT / output_dir
    output_path.mkdir(parents=True, exist_ok=True)

    # 4.2) 保存 OOS 预测 (供后续回测使用)
    oos_path = output_path / "oos_predictions.parquet"
    oos_df.to_parquet(oos_path, index=False)
    logger.info("OOS 预测已保存: %s (%d 条)", oos_path, len(oos_df))

    # 4.3) 简易组合模拟
    portfolio_metrics = simulate_portfolio(
        oos_df, top_k=50, rebalance_days=args.forward_days, cost_bps=30.0,
    )

    # 5) 输出结果
    logger.info("=" * 60)
    logger.info("模型: %s | Universe: %s", args.model, args.universe)
    logger.info("WF Folds: %d", wf_summary.get("n_folds", 0))
    logger.info("Mean IC: %.4f", metrics.get("mean_ic", 0))
    logger.info("IC IR:   %.4f", metrics.get("ic_ir", 0))
    logger.info("IC>0 %%:  %.1f%%", metrics.get("ic_positive_ratio", 0) * 100)
    logger.info("Top50 AR:  %.2f%%", metrics.get("top_annual_ret", 0) * 100)
    logger.info("Bottom AR: %.2f%%", metrics.get("bottom_annual_ret", 0) * 100)
    logger.info("L-S AR:    %.2f%%", metrics.get("long_short_annual", 0) * 100)
    logger.info("=" * 60)

    # IS-OOS 衰减分析
    is_oos_decay = wf_summary.get("is_oos_mean_decay")
    if is_oos_decay is not None:
        logger.info("IS-OOS IC 衰减: %.1f%%", is_oos_decay * 100)
        logger.info(
            "IS Mean IC: %.4f | OOS Mean IC: %.4f",
            wf_summary.get("is_mean_ic", 0),
            wf_summary.get("mean_ic", 0),
        )
        if is_oos_decay <= 0.50:
            logger.info("✓ IS-OOS 衰减 ≤ 50%% 达标")
        else:
            logger.warning(
                "✗ IS-OOS 衰减 %.1f%% > 50%% 未达标",
                is_oos_decay * 100,
            )

    # 4.5) WF 统计验证（DSR / HLZ / PBO）
    from aipha.models.wf_stats import compute_wf_statistical_summary
    n_trials = max(1, wf_summary.get("n_folds", 1))
    wf_stats = compute_wf_statistical_summary(oos_df, wf_summary, n_trials=n_trials)
    logger.info("=" * 60)
    logger.info("WF 统计验证:")
    logger.info("  DSR:          %.4f  (>0 为正, >1 为强)", wf_stats.get("dsr", float("nan")))
    logger.info("  HLZ t adj:    %.4f  (>2 显著)", wf_stats.get("hlz_adjusted_t", float("nan")))
    logger.info("  PBO:          %.4f  (<0.5 合格)", wf_stats.get("pbo", float("nan")))
    logger.info("  N obs (IC):   %d", wf_stats.get("n_obs", 0))

    # 6) 保存模型
    selector.save(output_path)
    logger.info("模型已保存到: %s", output_path)

    # 6.5) 保存漂移基线（供 DriftMonitor 运行时检测使用）
    try:
        from aipha.models.drift_monitor import DriftMonitor
        _base_cols = {
            "datetime", "symbol", "open", "high", "low", "close",
            "volume", "vol", "amount", "pre_close", "change", "pct_chg",
            "pe_ttm", "pb", "total_mv", "circ_mv",
            "turnover_rate", "dv_ratio", "dv_ttm", "label",
        }
        feature_cols = [c for c in df.columns if c not in _base_cols]
        if feature_cols:
            drift_mon = DriftMonitor(feature_names=feature_cols)
            drift_mon.set_baseline(df[feature_cols])
            baseline_path = output_path / "drift_baseline.json"
            import json as _json2
            with open(baseline_path, "w", encoding="utf-8") as _f2:
                _json2.dump(drift_mon._baseline, _f2, indent=2, default=str)
            logger.info("漂移基线已保存: %s (%d 个特征)", baseline_path, len(feature_cols))
        else:
            logger.warning("未找到特征列，跳过漂移基线保存。")
    except Exception as _de:
        logger.warning("漂移基线保存失败（非阻塞）: %s", _de)

    # 7) 保存评估报告
    import json
    report = {
        "model_type": args.model,
        "universe": args.universe,
        "date_range": f"{args.start} ~ {args.end}",
        "wf_summary": wf_summary,
        "oos_metrics": metrics,
        "portfolio_simulation": portfolio_metrics,
        "wf_statistical_validation": {
            "pbo": wf_stats.get("pbo"),
            "dsr": wf_stats.get("dsr"),
            "hlz_adjusted_t": wf_stats.get("hlz_adjusted_t"),
            "n_obs": wf_stats.get("n_obs"),
            "n_trials": wf_stats.get("n_trials"),
        },
        "elapsed_seconds": round(time.time() - t_start, 1),
    }
    report_path = output_path / "evaluation_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    logger.info("评估报告: %s", report_path)
    logger.info("总耗时: %.1f 秒", time.time() - t_start)


if __name__ == "__main__":
    main()
