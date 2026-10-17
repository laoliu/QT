"""Risk-Controlled OOS Replay — ML-A 风控增强版。

在 enhanced_oos_replay 基础上添加：
1. 行业中性约束: 单行业 ≤15% 持仓权重
2. 市值分散约束: 单票 ≤3% 持仓权重
3. 波动率动态仓位: 高波动时自动降仓
4. 增强型 MDD 控制: 渐进减仓（10%/15%/20%/25%）

目标: MDD 从 43.3% → ≤25%, SR ≥ 1.0

用法:
    python scripts/risk_controlled_replay.py --index csi2000_20d_r40
    python scripts/risk_controlled_replay.py --index csi2000_20d_r40 \\
        --max-industry-pct 0.15 --max-stock-pct 0.03
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
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
logger = logging.getLogger("risk_controlled_replay")

# ---- 公共风控模块 (P0#1: 回测/模拟共享) ----
from aipha.risk.ml_risk_controls import (  # noqa: E402
    CombinedPositionSizer,
    IndustryConstraint,
    MddGradientDeleverager,
    VolTargetScaler,
)

# ---- 共享费用模块（§13.2 单一实现原则）----
from aipha.execution.fees import TimeVaryingStampTaxModel  # noqa: E402

# ---- 费用参数（从 .env 加载，详见 scripts/fee_config.py）----
from fee_config import (  # noqa: E402
    COMMISSION_RATE, COMMISSION_MIN, STAMP_TAX_SELL,
    TRANSFER_FEE, SLIPPAGE_SPREAD_BPS, IMPACT_COEFF,
    MAX_PARTICIPATION, LOT_SIZE,
)

# ---- 风控参数 ----
DEFAULT_MAX_INDUSTRY_PCT = 0.15
DEFAULT_MAX_STOCK_PCT = 0.03
DEFAULT_VOL_TARGET = 0.20
DEFAULT_VOL_LOOKBACK = 60
# MDD 梯度减仓（V3: 平衡版——保留alpha同时控制尾部）
MDD_LEVELS = [
    (0.10, 1.0),   # MDD < 10%: 满仓
    (0.15, 0.80),  # MDD 10-15%: 80% 仓位
    (0.20, 0.55),  # MDD 15-20%: 55% 仓位
    (0.25, 0.30),  # MDD 20-25%: 30% 仓位
    (1.00, 0.10),  # MDD > 25%: 10% 仓位（不清仓）
]

# 预测配置
INDEX_CONFIGS: dict[str, dict[str, Any]] = {
    "csi2000_20d_r40": {
        "predictions": "models/ml_csi2000_lgb_20d_r40/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 20,
    },
    "csi2000_20d": {
        "predictions": "models/ml_csi2000_de_20d/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 20,
    },
    "csi2000_10d_r40": {
        "predictions": "models/ml_csi2000_lgb_10d_r40/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 10,
    },
    # Phase 2: ML-B 候选
    "csi2000_de_5d": {
        "predictions": "models/ml_csi2000_de_5d/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,
    },
    "csi2000_de_10d": {
        "predictions": "models/ml_csi2000_de_10d/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 10,
    },
    "csi2000_lgb_fund": {
        "predictions": "models/ml_csi2000_lgb_fund/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5, "allow_bse": True,  # fund model includes BSE
    },
    "csi2000_lgb_fund_nobse": {
        "predictions": "models/ml_csi2000_lgb_fund/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,  # fund model, filter out BSE
    },
    "csi2000_lgb_p1": {
        "predictions": "models/ml_csi2000_lgb_p1/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,  # R125 6-factor enhanced model
    },
    "csi2000_lgb_p2": {
        "predictions": "models/ml_csi2000_lgb_p2/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,  # R127 fundamental/quality factor model
    },
    "csi2000_lgb_p3": {
        "predictions": "models/ml_csi2000_lgb_p3/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,  # R128 regime-defensive factor model
    },
    "csi2000_lgb_rank": {
        "predictions": "models/ml_csi2000_lgb_rank/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,  # R129 LambdaRank 5-bin objective model
    },
    "csi2000_lgb_rank20": {
        "predictions": "models/ml_csi2000_lgb_rank20/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,  # R129 LambdaRank 20-bin objective model
    },
    "csi2000_lgb_trunc30": {
        "predictions": "models/ml_csi2000_lgb_trunc30/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,  # R130 Truncated training Top/Bot 30%
    },
    # R132 B3: 目标变量去市值化 (label = residual of fwd_return ~ log(circ_mv))
    "csi2000_lgb_b3_sizeneutral": {
        "predictions": "models/ml_csi2000_lgb_b3_sizeneutral/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,
    },
    # B3+mv30: 去市值化模型在≥30亿子集上的表现 (验证size-neutral alpha)
    "csi2000_lgb_b3_mv30": {
        "predictions": "models/ml_csi2000_lgb_b3_sizeneutral/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5, "min_circ_mv_yi": 30,
    },
    # R132+ P4a: P3 baseline + 分析师修正因子 (analyst_rev)
    "csi2000_lgb_p4a": {
        "predictions": "models/ml_csi2000_lgb_p4a/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,
    },
    # P4a+mv30: P4a 在≥30亿子集的表现 (中盘 alpha 验证)
    "csi2000_lgb_p4a_mv30": {
        "predictions": "models/ml_csi2000_lgb_p4a/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5, "min_circ_mv_yi": 30,
    },
    # ── R133 Phase 1: 市值分层验证 (Tier Experiment) ──
    # T1-A: 大盘 ≥100亿 (验证Alpha158在大盘上是否有alpha)
    "p3_tier1_mv100": {
        "predictions": "models/ml_csi2000_lgb_p3/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5, "min_circ_mv_yi": 100,
    },
    # T1-B: 中盘 30-100亿
    "p3_tier2_mv30_100": {
        "predictions": "models/ml_csi2000_lgb_p3/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,
        "min_circ_mv_yi": 30, "max_circ_mv_yi": 100,
    },
    # T1-B2: 中盘 50-200亿 (更宽区间)
    "p3_tier2_mv50_200": {
        "predictions": "models/ml_csi2000_lgb_p3/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,
        "min_circ_mv_yi": 50, "max_circ_mv_yi": 200,
    },
    # T1-A2: 大盘 ≥200亿
    "p3_tier1_mv200": {
        "predictions": "models/ml_csi2000_lgb_p3/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5, "min_circ_mv_yi": 200,
    },
    # 对照: P3 ≥30亿 (已有但重命名以对齐)
    "p3_mv30": {
        "predictions": "models/ml_csi2000_lgb_p3/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5, "min_circ_mv_yi": 30,
    },
    # T1: 20-50亿 (小中盘过渡区)
    "p3_tier_mv20_50": {
        "predictions": "models/ml_csi2000_lgb_p3/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,
        "min_circ_mv_yi": 20, "max_circ_mv_yi": 50,
    },
    # T1: <20亿 micro (控制组, 应≈全量P3)
    "p3_tier_mv0_20": {
        "predictions": "models/ml_csi2000_lgb_p3/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5, "max_circ_mv_yi": 20,
    },
    # T1: 50-100亿
    "p3_tier_mv50_100": {
        "predictions": "models/ml_csi2000_lgb_p3/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,
        "min_circ_mv_yi": 50, "max_circ_mv_yi": 100,
    },
    # ── R134 Phase 1: CSI500 模型分层验证 ──
    "csi500_lgb_p3": {
        "predictions": "models/ml_csi500_lgb_p3/oos_predictions.parquet",
        "top_k": 30, "rebalance_days": 5,
    },
    "csi500_p3_mv30": {
        "predictions": "models/ml_csi500_lgb_p3/oos_predictions.parquet",
        "top_k": 30, "rebalance_days": 5, "min_circ_mv_yi": 30,
    },
    "csi500_p3_mv50": {
        "predictions": "models/ml_csi500_lgb_p3/oos_predictions.parquet",
        "top_k": 30, "rebalance_days": 5, "min_circ_mv_yi": 50,
    },
    "csi500_p3_mv100": {
        "predictions": "models/ml_csi500_lgb_p3/oos_predictions.parquet",
        "top_k": 30, "rebalance_days": 5, "min_circ_mv_yi": 100,
    },
    "csi500_p3_mv30_100": {
        "predictions": "models/ml_csi500_lgb_p3/oos_predictions.parquet",
        "top_k": 30, "rebalance_days": 5,
        "min_circ_mv_yi": 30, "max_circ_mv_yi": 100,
    },
    "csi500_p3_mv50_200": {
        "predictions": "models/ml_csi500_lgb_p3/oos_predictions.parquet",
        "top_k": 30, "rebalance_days": 5,
        "min_circ_mv_yi": 50, "max_circ_mv_yi": 200,
    },
    # ── R135 Phase 1: CSI300 模型分层验证 ──
    "csi300_lgb_p3": {
        "predictions": "models/ml_csi300_lgb_p3/oos_predictions.parquet",
        "top_k": 30, "rebalance_days": 5,
    },
    "csi300_p3_mv100": {
        "predictions": "models/ml_csi300_lgb_p3/oos_predictions.parquet",
        "top_k": 30, "rebalance_days": 5, "min_circ_mv_yi": 100,
    },
    "csi300_p3_mv200": {
        "predictions": "models/ml_csi300_lgb_p3/oos_predictions.parquet",
        "top_k": 30, "rebalance_days": 5, "min_circ_mv_yi": 200,
    },
    "csi300_p3_mv100_500": {
        "predictions": "models/ml_csi300_lgb_p3/oos_predictions.parquet",
        "top_k": 30, "rebalance_days": 5,
        "min_circ_mv_yi": 100, "max_circ_mv_yi": 500,
    },
    "csi2000_lgb_fresh": {
        "predictions": "models/ml_csi2000_lgb_fresh_baseline/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,  # R130 Fresh baseline (230 features, 19 folds)
    },
    "csi2000_lgb_v2": {
        "predictions": "models/ml_csi2000_lgb_v2/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,  # R130 v2: start=2017, 230 features, 23 folds
    },
    "csi2000_lgb_v3_194feat": {
        "predictions": "models/ml_csi2000_lgb_v3_194feat/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,  # R130 v3: start=2017, 194 features, 23 folds
    },
    # R131: 市值约束变体——排除微盘股，仅保留流通市值≥30亿的股票
    "csi2000_lgb_v3_mv30": {
        "predictions": "models/ml_csi2000_lgb_v3_194feat/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5, "min_circ_mv_yi": 30,  # ≥30亿流通市值
    },
    "csi2000_lgb_v3_mv20": {
        "predictions": "models/ml_csi2000_lgb_v3_194feat/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5, "min_circ_mv_yi": 20,  # ≥20亿流通市值
    },
    "csi2000_lgb_v3_mv50": {
        "predictions": "models/ml_csi2000_lgb_v3_194feat/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5, "min_circ_mv_yi": 50,  # ≥50亿流通市值
    },
    "csi2000_lgb_v4_bse": {
        "predictions": "models/ml_csi2000_lgb_v4_bse/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5, "allow_bse": True,  # R130 v4: +BSE
    },
    "csi2000_lgb_fund_11y": {
        "predictions": "models/ml_csi2000_lgb_fund_11y/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,  # 11-year model (2015-2025), same signal horizon
    },
    "csi500_de": {
        "predictions": "models/ml_csi500_double_ensemble/oos_predictions.parquet",
        "top_k": 30, "rebalance_days": 20,
    },
    "csi1000_lgb_ext": {
        "predictions": "models/ml_csi1000_lgb_ext/oos_predictions.parquet",
        "top_k": 40, "rebalance_days": 20,
    },
    "csi1000_lgb_fund": {
        "predictions": "models/ml_csi1000_lgb_fund/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,
    },
    "csi1000_lgb_enhanced": {
        "predictions": "models/ml_csi1000_lgb_enhanced/oos_predictions.parquet",
        "top_k": 50, "rebalance_days": 5,
    },
}


def _load_industry_map() -> dict[str, str]:
    """加载行业映射 {ts_code: industry_name}。"""
    from aipha.data.tushare_equity_provider import TuShareEquityProvider
    provider = TuShareEquityProvider()
    return provider.load_industry_mapping()


def _load_circ_mv_map(
    trade_dates: list[pd.Timestamp],
) -> dict[pd.Timestamp, dict[str, float]]:
    """加载每月流通市值快照 {date: {ts_code: circ_mv_万元}}。

    R131: 为市值约束过滤提供数据。每月取一次 daily_basic 快照，
    同月内的交易日共用该月快照。避免每日调用 API。
    """
    from aipha.data.tushare_gateway import get_tushare_gateway
    import time as _time

    gw = get_tushare_gateway()
    # 确定需要的月份快照
    months_needed: dict[str, pd.Timestamp] = {}
    for dt in trade_dates:
        ym = pd.to_datetime(dt).strftime("%Y%m")
        if ym not in months_needed:
            months_needed[ym] = dt  # 用该月第一个交易日作为采样点

    monthly_mv: dict[str, dict[str, float]] = {}
    for ym, sample_dt in sorted(months_needed.items()):
        dt_str = pd.to_datetime(sample_dt).strftime("%Y%m%d")
        try:
            df = gw.call(
                "daily_basic", trade_date=dt_str,
                fields="ts_code,circ_mv",
            )
            if df is not None and len(df) > 0:
                monthly_mv[ym] = df.set_index("ts_code")["circ_mv"].to_dict()
                logger.debug("  circ_mv %s: %d stocks", ym, len(monthly_mv[ym]))
            _time.sleep(0.35)
        except Exception as exc:
            logger.warning("  circ_mv %s failed: %s", ym, exc)
            _time.sleep(0.5)

    # 映射每个交易日 → 对应月份的 mv_map
    date_mv: dict[pd.Timestamp, dict[str, float]] = {}
    for dt in trade_dates:
        ym = pd.to_datetime(dt).strftime("%Y%m")
        if ym in monthly_mv:
            date_mv[dt] = monthly_mv[ym]
    logger.info("circ_mv 加载完成: %d 月份, %d 交易日", len(monthly_mv), len(date_mv))
    return date_mv


def _get_mdd_position_scale(current_mdd: float) -> float:
    """根据当前 MDD 返回仓位缩放比例 (0~1)。
    委托给公共模块 MddGradientDeleverager。
    """
    return MddGradientDeleverager(levels=list(MDD_LEVELS)).get_scale(current_mdd)


def _get_vol_position_scale(
    nav_series: pd.Series, vol_target: float, lookback: int,
) -> float:
    """波动率目标仓位缩放。
    委托给公共模块 VolTargetScaler。
    """
    return VolTargetScaler(
        vol_target=vol_target, lookback=lookback,
    ).get_scale(nav_series)


def _apply_industry_constraint(
    candidates: list[str],
    exec_price: pd.DataFrame,
    industry_map: dict[str, str],
    max_pct: float,
    top_k: int,
) -> list[str]:
    """行业中性约束。委托给公共模块 IndustryConstraint。"""
    constraint = IndustryConstraint(max_pct=max_pct)
    filtered, _ = constraint.apply(candidates, industry_map, top_k)
    return filtered


# 复用 enhanced_oos_replay 的数据加载/涨跌停检测
from enhanced_oos_replay import (  # noqa: E402
    detect_limit_stocks,
    load_bulk_prices,
    load_predictions,
)


# 共享的时变印花税模型实例（§13.2 禁止独立实现）
_STAMP_TAX_MODEL = TimeVaryingStampTaxModel()


def _stamp_tax_rate(exec_date: pd.Timestamp) -> float:
    """时变印花税率 — 委托给共享 TimeVaryingStampTaxModel。"""
    return _STAMP_TAX_MODEL.rate_at(exec_date.date())


def _calc_sell_cost(
    shares: float, px: float, exec_date: pd.Timestamp,
    day_amount_yuan: float,
) -> tuple[float, float]:
    """计算卖出净收入和费用。返回 (net_proceeds, total_fee)。"""
    sell_val = shares * px
    participation = (sell_val / day_amount_yuan) if day_amount_yuan > 0 else 0.01
    impact = SLIPPAGE_SPREAD_BPS + IMPACT_COEFF * np.sqrt(participation)
    adj_px = px * (1 - impact / 10000)
    gross = shares * adj_px
    fee = max(gross * COMMISSION_RATE, COMMISSION_MIN)
    stamp = gross * _stamp_tax_rate(exec_date)
    transfer = gross * TRANSFER_FEE
    net = gross - fee - stamp - transfer
    return net, fee + stamp + transfer


def run_risk_controlled_replay(
    predictions: pd.DataFrame,
    prices_df: pd.DataFrame,
    industry_map: dict[str, str],
    top_k: int = 50,
    rebalance_days: int = 20,
    initial_capital: float = 1_000_000.0,
    max_industry_pct: float = DEFAULT_MAX_INDUSTRY_PCT,
    max_stock_pct: float = DEFAULT_MAX_STOCK_PCT,
    vol_target: float = DEFAULT_VOL_TARGET,
    vol_lookback: int = DEFAULT_VOL_LOOKBACK,
    min_circ_mv_yi: float = 0.0,
    max_circ_mv_yi: float = 0.0,
    circ_mv_by_date: dict[pd.Timestamp, dict[str, float]] | None = None,
) -> dict:
    """风控增强型回测主循环。

    Parameters
    ----------
    min_circ_mv_yi : float
        最低流通市值(亿元)。>0时过滤低于此阈值的股票(R131 CF-026)。
    max_circ_mv_yi : float
        最高流通市值(亿元)。>0时过滤高于此阈值的股票(R133 Tier实验)。
    circ_mv_by_date : dict, optional
        {date: {ts_code: circ_mv_万元}} 映射。min/max_circ_mv_yi>0 时必须提供。
    """
    price_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
    for dt, grp in prices_df.groupby("date"):
        price_by_date[dt] = grp.set_index("symbol")
    trade_dates = sorted(price_by_date.keys())
    td_set = set(trade_dates)

    pred_dates = sorted(predictions["date"].unique())
    valid_pred_dates = [d for d in pred_dates if d in td_set]
    signal_dates = valid_pred_dates[::rebalance_days]
    logger.info(
        "风控回测: %d 信号日, top_%d, 每 %d 天调仓",
        len(signal_dates), top_k, rebalance_days,
    )
    logger.info(
        "  行业上限=%.0f%%, 单票上限=%.0f%%, vol目标=%.0f%%",
        max_industry_pct * 100, max_stock_pct * 100, vol_target * 100,
    )

    nav = initial_capital
    cash = initial_capital
    holdings: dict[str, tuple[float, float]] = {}
    last_close: dict[str, float] = {}  # P1: 停牌时用最后收盘价而非成本价
    nav_history: dict[pd.Timestamp, float] = {}
    total_fees = 0.0
    total_trades = 0
    trade_log: list[dict] = []  # P0#2: 逐笔交易审计日志
    stats = {
        "blocked_limit_up": 0, "blocked_suspended": 0,
        "blocked_volume": 0, "blocked_industry": 0,
        "blocked_mv": 0,
        "mdd_reductions": 0, "vol_adjustments": 0,
    }
    # R131/R133: 市值过滤阈值 (万元)
    min_circ_mv_wan = min_circ_mv_yi * 10000.0 if min_circ_mv_yi > 0 else 0.0
    max_circ_mv_wan = max_circ_mv_yi * 10000.0 if max_circ_mv_yi > 0 else 0.0
    if min_circ_mv_wan > 0 or max_circ_mv_wan > 0:
        logger.info("  市值区间=[%.0f, %.0f]亿",
                    min_circ_mv_yi if min_circ_mv_yi > 0 else 0,
                    max_circ_mv_yi if max_circ_mv_yi > 0 else float('inf'))

    for i, sig_date in enumerate(signal_dates):
        day_preds = predictions[predictions["date"] == sig_date]
        sig_price = price_by_date.get(sig_date)
        if sig_price is None:
            continue

        # 信号日过滤（R133 fix: 市值过滤在top_k*3截断之前，确保tier内选top_k）
        candidates = day_preds.sort_values("score", ascending=False)
        use_mv_filter = (min_circ_mv_wan > 0 or max_circ_mv_wan > 0) and circ_mv_by_date is not None
        mv_map = circ_mv_by_date.get(sig_date, {}) if use_mv_filter else None
        pre_filter: list[str] = []
        for _, row in candidates.iterrows():
            sym = row["symbol"]
            if sym in sig_price.index:
                sp = sig_price.loc[sym]
                if sp.get("is_limit_up", False):
                    stats["blocked_limit_up"] += 1
                    continue
                if sp.get("is_suspended", False):
                    stats["blocked_suspended"] += 1
                    continue
            # 市值过滤在截断之前: 确保收集的top_k*3全部在目标市值区间内
            if use_mv_filter and mv_map is not None:
                mv = mv_map.get(sym, 0)
                if min_circ_mv_wan > 0 and mv < min_circ_mv_wan:
                    stats["blocked_mv"] += 1
                    continue
                if max_circ_mv_wan > 0 and mv > max_circ_mv_wan:
                    stats["blocked_mv"] += 1
                    continue
            pre_filter.append(sym)
            if len(pre_filter) >= top_k * 3:
                break

        # 执行日
        sig_idx = trade_dates.index(sig_date)
        if sig_idx + 1 >= len(trade_dates):
            break
        exec_date = trade_dates[sig_idx + 1]
        exec_price = price_by_date.get(exec_date)
        if exec_price is None:
            continue

        # 执行日过滤
        buyable_raw: list[str] = []
        for sym in pre_filter:
            if sym not in exec_price.index:
                stats["blocked_suspended"] += 1
                continue
            ep = exec_price.loc[sym]
            if ep.get("is_limit_up_open", ep.get("is_limit_up", False)):
                stats["blocked_limit_up"] += 1
                continue
            if ep.get("is_suspended", False):
                stats["blocked_suspended"] += 1
                continue
            op = ep.get("open", 0)
            if pd.isna(op) or op <= 0:
                continue
            buyable_raw.append(sym)
            if len(buyable_raw) >= top_k * 2:
                break

        # 行业中性约束
        orig_len = len(buyable_raw)
        buyable = _apply_industry_constraint(
            buyable_raw, exec_price, industry_map,
            max_industry_pct, top_k,
        )
        stats["blocked_industry"] += orig_len - len(buyable)

        # MDD 和波动率仓位缩放
        nav_s = pd.Series(nav_history).sort_index()
        if len(nav_s) > 1:
            peak = nav_s.cummax()
            current_mdd = float((peak.iloc[-1] - nav_s.iloc[-1]) / peak.iloc[-1])
        else:
            current_mdd = 0.0

        mdd_scale = _get_mdd_position_scale(current_mdd)
        vol_scale = _get_vol_position_scale(nav_s, vol_target, vol_lookback)
        position_scale = min(mdd_scale, vol_scale)

        if mdd_scale < 1.0:
            stats["mdd_reductions"] += 1
        if vol_scale < 0.95:
            stats["vol_adjustments"] += 1

        # 持有期终止日
        if i + 1 < len(signal_dates):
            next_sig = signal_dates[i + 1]
            nsi = trade_dates.index(next_sig)
            end_dt = trade_dates[min(nsi + 1, len(trade_dates) - 1)]
        else:
            end_dt = trade_dates[-1]
        hold_dates = [d for d in trade_dates if exec_date <= d <= end_dt]
        if not hold_dates:
            continue

        # ---- 卖出旧持仓 ----
        for sym in list(holdings.keys()):
            if sym in buyable:
                continue
            shares, cost = holdings[sym]
            if sym not in exec_price.index:
                continue
            sp_data = exec_price.loc[sym]
            if sp_data.get("is_suspended", True):
                continue
            sell_px = sp_data.get("open", 0)
            if pd.isna(sell_px) or sell_px <= 0:
                continue
            day_amt = sp_data.get("amount", 0)
            day_amt_yuan = (day_amt * 1000) if not pd.isna(day_amt) else 0
            net, fee = _calc_sell_cost(shares, sell_px, exec_date, day_amt_yuan)
            cash += net
            total_fees += fee
            total_trades += 1
            trade_log.append({
                "date": str(exec_date.date()), "symbol": sym,
                "side": "SELL", "qty": shares, "price": round(sell_px, 4),
                "fee": round(fee, 4), "reason": "rebalance_exit",
            })
            del holdings[sym]
            last_close.pop(sym, None)

        # ---- 目标配额（含仓位缩放 + 单票上限）----
        n_target = min(len(buyable), top_k)
        if n_target > 0:
            hold_mkt = sum(
                sh * exec_price.loc[s].get("open", c)
                if s in exec_price.index else sh * c
                for s, (sh, c) in holdings.items()
            )
            total_port = hold_mkt + cash
            # 单票上限 vs 等权
            target_per = min(
                total_port * position_scale / n_target,
                total_port * max_stock_pct,
            )
        else:
            target_per = 0

        # ---- 再平衡已持有 ----
        # R44-FIX BUG-2: 再平衡交易应用滑点/市场冲击模型
        for sym in list(holdings.keys()):
            if sym not in buyable:
                continue
            shares, cost = holdings[sym]
            if sym not in exec_price.index:
                continue
            ep_r = exec_price.loc[sym]
            px = ep_r.get("open", cost)
            if pd.isna(px) or px <= 0:
                continue
            val = shares * px
            diff = val - target_per
            if abs(diff) < target_per * 0.10:
                continue
            day_amt_r = ep_r.get("amount", 0)
            day_amt_r_yuan = (
                (day_amt_r * 1000)
                if not pd.isna(day_amt_r) else 0
            )
            if diff > 0:
                sell_sh = int(diff / px / LOT_SIZE) * LOT_SIZE
                if sell_sh <= 0:
                    continue
                sv = sell_sh * px
                sp = (
                    (sv / day_amt_r_yuan)
                    if day_amt_r_yuan > 0 else 0.01
                )
                s_imp = (
                    SLIPPAGE_SPREAD_BPS
                    + IMPACT_COEFF * np.sqrt(sp)
                )
                adj_sell = px * (1 - s_imp / 10000)
                sell_val = sell_sh * adj_sell
                fee = max(
                    sell_val * COMMISSION_RATE, COMMISSION_MIN
                )
                stamp = sell_val * _stamp_tax_rate(exec_date)
                transfer = sell_val * TRANSFER_FEE
                cash += sell_val - fee - stamp - transfer
                total_fees += fee + stamp + transfer
                total_trades += 1
                trade_log.append({
                    "date": str(exec_date.date()), "symbol": sym,
                    "side": "SELL", "qty": sell_sh,
                    "price": round(adj_sell, 4),
                    "fee": round(fee + stamp + transfer, 4),
                    "reason": "rebalance_trim",
                })
                new_sh = shares - sell_sh
                if new_sh <= 0:
                    del holdings[sym]
                    last_close.pop(sym, None)
                else:
                    holdings[sym] = (float(new_sh), cost)
            else:
                add_val = min(-diff, cash - COMMISSION_MIN)
                if add_val < px * LOT_SIZE:
                    continue
                rb_part = (
                    (add_val / day_amt_r_yuan)
                    if day_amt_r_yuan > 0 else 0.01
                )
                rb_imp = (
                    SLIPPAGE_SPREAD_BPS
                    + IMPACT_COEFF * np.sqrt(rb_part)
                )
                adj_buy = px * (1 + rb_imp / 10000)
                add_sh = int(
                    add_val / adj_buy / LOT_SIZE
                ) * LOT_SIZE
                if add_sh <= 0:
                    continue
                buy_val = add_sh * adj_buy
                fee = max(
                    buy_val * COMMISSION_RATE, COMMISSION_MIN
                )
                transfer = buy_val * TRANSFER_FEE
                total_buy = buy_val + fee + transfer
                if total_buy > cash:
                    continue
                cash -= total_buy
                total_fees += fee + transfer
                total_trades += 1
                trade_log.append({
                    "date": str(exec_date.date()), "symbol": sym,
                    "side": "BUY", "qty": add_sh,
                    "price": round(adj_buy, 4),
                    "fee": round(fee + transfer, 4),
                    "reason": "rebalance_add",
                })
                new_sh = shares + add_sh
                avg = (
                    (shares * cost + add_sh * adj_buy) / new_sh
                )
                holdings[sym] = (float(new_sh), avg)

        # ---- 买入新持仓 ----
        new_buys = [s for s in buyable if s not in holdings]
        for sym in new_buys:
            if cash <= COMMISSION_MIN:
                break
            ep = exec_price.loc[sym]
            buy_px = ep["open"]
            if pd.isna(buy_px) or buy_px <= 0:
                continue
            day_amt = ep.get("amount", 0)
            day_amt_yuan = (day_amt * 1000) if not pd.isna(day_amt) else 0
            max_buy = day_amt_yuan * MAX_PARTICIPATION
            alloc = min(target_per, cash, max_buy) if max_buy > 0 else min(target_per, cash)
            if alloc < buy_px * LOT_SIZE:
                stats["blocked_volume"] += 1
                continue
            participation = (alloc / day_amt_yuan) if day_amt_yuan > 0 else 0.01
            impact = SLIPPAGE_SPREAD_BPS + IMPACT_COEFF * np.sqrt(participation)
            adj_px = buy_px * (1 + impact / 10000)
            shares_f = int(alloc / adj_px / LOT_SIZE) * LOT_SIZE
            if shares_f <= 0:
                continue
            cost_total = shares_f * adj_px
            fee = max(cost_total * COMMISSION_RATE, COMMISSION_MIN)
            transfer = cost_total * TRANSFER_FEE
            total_buy = cost_total + fee + transfer
            if total_buy > cash:
                shares_f = int((cash - COMMISSION_MIN) / adj_px / LOT_SIZE) * LOT_SIZE
                if shares_f <= 0:
                    continue
                cost_total = shares_f * adj_px
                fee = max(cost_total * COMMISSION_RATE, COMMISSION_MIN)
                transfer = cost_total * TRANSFER_FEE
                total_buy = cost_total + fee + transfer
            holdings[sym] = (float(shares_f), adj_px)
            last_close[sym] = buy_px  # 初始化最后收盘价
            cash -= total_buy
            total_fees += fee + transfer
            total_trades += 1
            trade_log.append({
                "date": str(exec_date.date()), "symbol": sym,
                "side": "BUY", "qty": shares_f,
                "price": round(adj_px, 4),
                "fee": round(fee + transfer, 4),
                "reason": "new_entry",
            })

        # ---- 逐日 NAV ----
        for hd in hold_dates:
            hp = price_by_date.get(hd)
            if hp is None:
                continue
            port_val = 0.0
            for s, (sh, c) in holdings.items():
                if s in hp.index and not pd.isna(hp.loc[s].get("close")):
                    px = hp.loc[s]["close"]
                    last_close[s] = px  # 更新最后收盘价
                else:
                    # P1 修复: 停牌时用最后收盘价，而非成本价
                    px = last_close.get(s, c)
                port_val += sh * px
            nav_history[hd] = port_val + cash

        if hold_dates and hold_dates[-1] in nav_history:
            nav = nav_history[hold_dates[-1]]

    nav_series = pd.Series(nav_history).sort_index()
    logger.info("风控回测完成: %d 天, %d 笔交易", len(nav_series), len(trade_log))
    for k, v in stats.items():
        logger.info("  %s: %d", k, v)
    return {"nav_series": nav_series, "stats": stats,
            "total_fees": total_fees, "total_trades": total_trades,
            "trade_log": trade_log}


def compute_metrics(nav_series: pd.Series) -> dict:
    """计算绩效指标（复用 enhanced 逻辑）。"""
    rets = nav_series.pct_change().dropna()
    n_days = len(rets)
    n_years = n_days / 244.0
    cum = nav_series.iloc[-1] / nav_series.iloc[0]
    ar = cum ** (1 / max(n_years, 0.25)) - 1
    ar_arith = float(rets.mean()) * 244
    vol = rets.std() * np.sqrt(244)
    sr = (ar_arith - 0.02) / vol if vol > 0 else 0.0
    peak = nav_series.cummax()
    dd = (peak - nav_series) / peak
    mdd = dd.max()
    calmar = ar / mdd if mdd > 0 else 0.0
    # MDD 恢复时间
    dd_start = dd.idxmax()
    recovery_days = 0
    if mdd > 0:
        post_dd = nav_series[nav_series.index >= dd_start]
        recovered = post_dd[post_dd >= peak[dd_start]]
        if not recovered.empty:
            recovery_days = len(nav_series[
                (nav_series.index >= dd_start) &
                (nav_series.index <= recovered.index[0])
            ])
    yearly: dict[str, float] = {}
    for yr, grp in rets.groupby(rets.index.year):
        yearly[str(yr)] = round(float((1 + grp).prod() - 1), 4)
    return {
        "annualized_return": round(float(ar), 4),
        "volatility": round(float(vol), 4),
        "sharpe_ratio": round(float(sr), 2),
        "max_drawdown": round(float(mdd), 4),
        "calmar_ratio": round(float(calmar), 2),
        "mdd_recovery_days": recovery_days,
        "n_years": round(n_years, 2),
        "total_return": round(float(cum - 1), 4),
        "yearly_returns": yearly,
    }


def main() -> None:
    """入口。"""
    parser = argparse.ArgumentParser(description="Risk-Controlled OOS Replay")
    parser.add_argument("--index", default="csi2000_20d_r40",
                        choices=list(INDEX_CONFIGS.keys()))
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--rebalance", type=int, default=None)
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--max-industry-pct", type=float,
                        default=DEFAULT_MAX_INDUSTRY_PCT)
    parser.add_argument("--max-stock-pct", type=float,
                        default=DEFAULT_MAX_STOCK_PCT)
    parser.add_argument("--vol-target", type=float, default=DEFAULT_VOL_TARGET)
    parser.add_argument("--min-circ-mv-yi", type=float, default=0.0,
                        help="Min circ MV in 亿元 (R131 CF-026). 0=no filter.")
    parser.add_argument("--max-circ-mv-yi", type=float, default=0.0,
                        help="Max circ MV in 亿元 (R133 Tier). 0=no filter.")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    cfg = INDEX_CONFIGS[args.index]
    top_k = args.top_k or cfg["top_k"]
    rebalance_days = args.rebalance or cfg["rebalance_days"]
    min_circ_mv_yi = args.min_circ_mv_yi or cfg.get("min_circ_mv_yi", 0.0)
    max_circ_mv_yi = args.max_circ_mv_yi or cfg.get("max_circ_mv_yi", 0.0)

    t0 = time.time()
    pred_path = ROOT / cfg["predictions"]
    allow_bse = cfg.get("allow_bse", False)
    predictions = load_predictions(
        pred_path,
        universe_name="all" if allow_bse else args.index,
        strict_universe=False,
    )

    # 加载行业映射
    logger.info("加载行业映射...")
    industry_map = _load_industry_map()
    logger.info("  行业映射: %d 只股票", len(industry_map))

    # 加载价格
    date_min = predictions["date"].min()
    date_max = predictions["date"].max()
    start_ym = f"{date_min.year:04d}{date_min.month:02d}"
    end_y, end_m = date_max.year, date_max.month + 1
    if end_m > 12:
        end_m, end_y = 1, end_y + 1
    end_ym = f"{end_y:04d}{end_m:02d}"
    cache_dir = ROOT / "data" / "cache" / "equity_tushare"
    prices_df = load_bulk_prices(cache_dir, start_ym, end_ym)
    prices_df = detect_limit_stocks(prices_df)

    # R131/R133: 加载市值数据（仅在需要市值过滤时）
    circ_mv_by_date = None
    if min_circ_mv_yi > 0 or max_circ_mv_yi > 0:
        logger.info("加载流通市值数据 (min=%.0f亿, max=%.0f亿)...",
                    min_circ_mv_yi, max_circ_mv_yi)
        all_trade_dates = sorted(prices_df["date"].unique())
        circ_mv_by_date = _load_circ_mv_map(all_trade_dates)

    result = run_risk_controlled_replay(
        predictions, prices_df, industry_map,
        top_k=top_k, rebalance_days=rebalance_days,
        initial_capital=args.capital,
        max_industry_pct=args.max_industry_pct,
        max_stock_pct=args.max_stock_pct,
        vol_target=args.vol_target,
        min_circ_mv_yi=min_circ_mv_yi,
        max_circ_mv_yi=max_circ_mv_yi,
        circ_mv_by_date=circ_mv_by_date,
    )
    metrics = compute_metrics(result["nav_series"])
    elapsed = time.time() - t0

    out_name = args.output or f"risk_controlled_{args.index}"
    out_dir = ROOT / "output" / out_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── P0#2 修复: 标准四件套 + 审计日志 + SQLite 索引 ──

    # 1) config.yaml 快照
    import yaml
    run_config = {
        "index": args.index, "top_k": top_k,
        "rebalance_days": rebalance_days,
        "initial_capital": args.capital,
        "max_industry_pct": args.max_industry_pct,
        "max_stock_pct": args.max_stock_pct,
        "vol_target": args.vol_target,
        "fee_params": {
            "commission_rate": COMMISSION_RATE,
            "commission_min": COMMISSION_MIN,
            "stamp_tax_sell": STAMP_TAX_SELL,
            "transfer_fee": TRANSFER_FEE,
            "slippage_spread_bps": SLIPPAGE_SPREAD_BPS,
            "impact_coeff": IMPACT_COEFF,
            "max_participation": MAX_PARTICIPATION,
            "lot_size": LOT_SIZE,
        },
        "risk_params": {
            "mdd_levels": MDD_LEVELS,
            "vol_lookback": DEFAULT_VOL_LOOKBACK,
        },
        "predictions_path": cfg["predictions"],
    }
    with open(out_dir / "config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(run_config, f, allow_unicode=True, default_flow_style=False)

    # 2) fingerprint.json
    import hashlib
    import subprocess
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        git_short = git_commit[:8]
        git_dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
        ).strip())
    except Exception:
        git_commit, git_short, git_dirty = "unknown", "unknown", True
    config_hash = hashlib.sha256(
        json.dumps(run_config, sort_keys=True).encode()
    ).hexdigest()[:16]
    fingerprint = {
        "git_commit": git_commit, "git_short": git_short,
        "git_dirty": git_dirty,
        "config_hash": config_hash,
        "run_time": pd.Timestamp.now().isoformat(),
        "python_version": sys.version.split()[0],
        "elapsed_seconds": round(elapsed, 1),
    }
    with open(out_dir / "fingerprint.json", "w", encoding="utf-8") as f:
        json.dump(fingerprint, f, indent=2, ensure_ascii=False)

    # 3) summary.json (保持向后兼容格式)
    summary = {
        "strategy_name": f"risk_controlled_{args.index}",
        "index": args.index, "top_k": top_k,
        "rebalance_days": rebalance_days,
        "max_industry_pct": args.max_industry_pct,
        "max_stock_pct": args.max_stock_pct,
        "vol_target": args.vol_target,
        "min_circ_mv_yi": min_circ_mv_yi,
        "elapsed_seconds": round(elapsed, 1),
        "total_fees": round(result["total_fees"], 2),
        "total_trades": result["total_trades"],
        **result["stats"], **metrics,
    }
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    nav_df = result["nav_series"].reset_index()
    nav_df.columns = ["date", "nav"]
    nav_df["date"] = nav_df["date"].dt.strftime("%Y-%m-%d")
    nav_df.to_csv(out_dir / "daily_nav.csv", index=False)

    # 4) trades.csv 逐笔交易明细
    import csv
    trade_records = result.get("trade_log", [])
    if trade_records:
        trades_path = out_dir / "trades.csv"
        with open(trades_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["date", "symbol", "side", "qty",
                               "price", "fee", "reason"],
            )
            writer.writeheader()
            writer.writerows(trade_records)
        logger.info("交易明细已保存: %s (%d 笔)", trades_path, len(trade_records))

    # 5) audit/ 目录 — 交易日志 JSONL
    audit_dir = out_dir / "audit"
    audit_dir.mkdir(exist_ok=True)
    with open(audit_dir / "trade_log.jsonl", "w", encoding="utf-8") as f:
        for rec in trade_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 5b) backtest_report.html — 可视化报告
    try:
        from aipha.backtest.html_report import generate_html_from_metrics
        fp_path = out_dir / "fingerprint.json"
        fp_data = json.loads(fp_path.read_text("utf-8")) if fp_path.exists() else None
        generate_html_from_metrics(
            strategy_name=f"risk_controlled_{args.index}",
            metrics=metrics,
            nav_series_pd=result["nav_series"],
            output_path=out_dir / "backtest_report.html",
            fingerprint=fp_data,
            total_trades=result["total_trades"],
            total_fees=result["total_fees"],
        )
        logger.info("HTML 报告已生成: %s", out_dir / "backtest_report.html")
    except Exception as e:
        logger.warning("HTML 报告生成失败（不影响结果）: %s", e)

    # 6) SQLite 索引写入
    try:
        from aipha.backtest.result_store import ResultStore
        run_dict = {
            "run_id": out_name,
            "strategy_name": f"risk_controlled_{args.index}",
            "config_path": str(out_dir / "config.yaml"),
            "output_dir": str(out_dir),
            "annualized_return": metrics.get("annualized_return"),
            "sharpe_ratio": metrics.get("sharpe_ratio"),
            "max_drawdown": metrics.get("max_drawdown"),
            "calmar_ratio": metrics.get("calmar_ratio"),
            "volatility": metrics.get("volatility"),
            "total_trades": result["total_trades"],
            "total_fees": round(result["total_fees"], 2),
        }
        store = ResultStore()
        store.upsert_backtest_run(run_dict)
        store.close()
        logger.info("结果已索引到 SQLite: %s", out_name)
    except Exception as e:
        logger.warning("SQLite 索引写入失败: %s", e)

    logger.info("=" * 64)
    logger.info("Risk-Controlled Replay — %s", args.index.upper())
    logger.info("=" * 64)
    for k, v in sorted(summary.items()):
        if k != "yearly_returns":
            logger.info("  %-28s: %s", k, v)
    logger.info("  逐年:")
    for yr, ret in sorted(metrics["yearly_returns"].items()):
        logger.info("    %s: %+.1f%%", yr, ret * 100)
    logger.info("耗时: %.1f 秒", elapsed)


if __name__ == "__main__":
    main()
