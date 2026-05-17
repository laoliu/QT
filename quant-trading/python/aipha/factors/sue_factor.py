"""SUE 因子 — 标准化意外盈利 (Standardized Unexpected Earnings)。

学术基础:
    Bernard & Thomas (1989): Post-Earnings-Announcement Drift (PEAD)
    是金融学中最稳健的异象之一。

    SUE = (EPS_actual - EPS_consensus) / σ(forecast_errors)

    其中:
    - EPS_actual: 财报实际 EPS (利润表 basic/diluted EPS)
    - EPS_consensus: 分析师一致预期 EPS (report_rc 中位数)
    - σ: 过去 N 期预测误差的标准差 (用于标准化)

信号方向:
    SUE > 0 → 实际盈利超预期 → 正向 PEAD → 做多
    SUE < 0 → 实际盈利低于预期 → 负向 PEAD → 回避

PIT 合规:
    - 实际 EPS 以 ann_date (公告日) 为 PIT 基准
    - 一致预期以 report_date ≤ ann_date - 1 的最新数据为准
    - 避免使用未来信息

数据来源:
    - 实际 EPS: data/cache/advanced/express_YYYYMM.parquet (业绩快报)
                或通过 TuSharePITProvider 获取利润表 EPS
    - 一致预期: data/cache/alternative/analyst_YYYYMM.parquet (report_rc)
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from aipha.strategy.equity_rotation_base import EquityCandidateInfo

logger = logging.getLogger(__name__)


def _load_express_data(
    cache_dir: str | Path, months: list[str],
) -> pd.DataFrame:
    """加载业绩快报缓存数据。

    从 express_YYYYMM.parquet 加载, 包含:
    ts_code, ann_date, end_date, diluted_eps, yoy_net_profit, ...
    """
    cache_dir = Path(cache_dir)
    parts: list[pd.DataFrame] = []
    for ym in months:
        fpath = cache_dir / f"express_{ym}.parquet"
        if fpath.exists():
            try:
                parts.append(pd.read_parquet(fpath))
            except Exception as e:
                logger.warning("读取 express %s 失败: %s", fpath.name, e)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _load_analyst_consensus(
    cache_dir: str | Path, months: list[str],
) -> pd.DataFrame:
    """加载分析师一致预期缓存数据。

    从 analyst_YYYYMM.parquet 加载, 包含:
    ts_code, report_date, eps, target_price, rating, ...
    """
    cache_dir = Path(cache_dir)
    parts: list[pd.DataFrame] = []
    for ym in months:
        fpath = cache_dir / f"analyst_{ym}.parquet"
        if fpath.exists():
            try:
                parts.append(pd.read_parquet(fpath))
            except Exception as e:
                logger.warning("读取 analyst %s 失败: %s", fpath.name, e)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def compute_sue_factor(
    candidates: Sequence[EquityCandidateInfo],
    express_cache_dir: str | Path = "data/cache/advanced",
    analyst_cache_dir: str | Path = "data/cache/alternative",
    trade_date: str = "",
    lookback_months: int = 12,
    min_history: int = 2,
) -> dict[str, float]:
    """标准化意外盈利因子 (SUE)。

    SUE = (EPS_actual - EPS_consensus) / σ(historical_errors)

    流程:
    1. 取 trade_date 前已公告的最新业绩快报 (实际 EPS)
    2. 取该快报 ann_date 前的分析师一致预期 EPS (中位数)
    3. 计算 surprise = actual - consensus
    4. 用过去 N 期 surprise 的标准差做标准化

    PIT 合规:
    - express: ann_date ≤ trade_date
    - analyst: report_date < express.ann_date (避免用快报后的调整)

    Args:
        candidates: 候选标的
        express_cache_dir: 业绩快报缓存目录
        analyst_cache_dir: 分析师数据缓存目录
        trade_date: 交易日 YYYYMMDD
        lookback_months: 回看月数
        min_history: 最少需要的历史观测数

    Returns:
        {symbol: sue_score}
    """
    if not trade_date or not candidates:
        return {}

    from aipha.factors.analyst_factors import _get_months_before

    symbols = {c.symbol for c in candidates}
    months = _get_months_before(trade_date, lookback_months)

    express_df = _load_express_data(express_cache_dir, months)
    analyst_df = _load_analyst_consensus(analyst_cache_dir, months)

    if express_df.empty:
        logger.debug("SUE: express 数据为空")
        return {}

    # PIT: 仅使用 ann_date ≤ trade_date 的快报
    if "ann_date" in express_df.columns:
        express_df = express_df[
            express_df["ann_date"].astype(str) <= trade_date
        ].copy()
    if express_df.empty:
        return {}

    # 确保 EPS 字段
    eps_col = None
    for col in ("diluted_eps", "eps", "s_fa_eps_diluted"):
        if col in express_df.columns:
            eps_col = col
            break
    if eps_col is None:
        logger.debug("SUE: express 缺少 EPS 字段")
        return {}

    express_df["_eps_actual"] = pd.to_numeric(
        express_df[eps_col], errors="coerce",
    )
    express_df = express_df.dropna(subset=["_eps_actual"])

    result: dict[str, float] = {}
    for sym in symbols:
        # 1. 该股票的所有已公告快报
        sub_exp = express_df[express_df["ts_code"] == sym].copy()
        if sub_exp.empty:
            continue
        sub_exp = sub_exp.sort_values("ann_date")
        latest_exp = sub_exp.iloc[-1]
        actual_eps = latest_exp["_eps_actual"]
        exp_ann_date = str(latest_exp.get("ann_date", ""))

        # 2. 该快报公告前的分析师一致预期
        consensus_eps = _get_consensus_eps(
            analyst_df, sym, exp_ann_date,
        )
        if consensus_eps is None:
            continue

        # 3. surprise
        surprise = actual_eps - consensus_eps

        # 4. 标准化: 用历史 surprise 的 std
        hist_surprises = _compute_historical_surprises(
            sub_exp, analyst_df, sym,
        )
        if len(hist_surprises) >= min_history:
            sigma = np.std(hist_surprises, ddof=1)
            if sigma > 1e-8:
                sue = surprise / sigma
            else:
                sue = np.sign(surprise) * 3.0  # cap
        else:
            # 不够历史数据时用 raw surprise (归一化到合理范围)
            sue = np.clip(surprise * 10, -5, 5)

        if math.isfinite(sue):
            result[sym] = float(sue)

    if result:
        logger.info(
            "SUE 因子: %d/%d 有效 (date=%s)",
            len(result), len(symbols), trade_date,
        )
    return result


def _get_consensus_eps(
    analyst_df: pd.DataFrame,
    ts_code: str,
    before_date: str,
    lookback_days: int = 90,
) -> float | None:
    """获取指定日期前的分析师一致预期 EPS 中位数。"""
    if analyst_df.empty or "eps" not in analyst_df.columns:
        return None

    sub = analyst_df[analyst_df["ts_code"] == ts_code].copy()
    if sub.empty:
        return None

    date_col = "report_date" if "report_date" in sub.columns else "ann_date"
    if date_col not in sub.columns:
        return None

    sub = sub[sub[date_col].astype(str) < before_date]
    if sub.empty:
        return None

    # 取最近 lookback_days 的预测
    from datetime import datetime, timedelta
    try:
        bd = datetime.strptime(before_date[:8], "%Y%m%d")
        cutoff = (bd - timedelta(days=lookback_days)).strftime("%Y%m%d")
        sub = sub[sub[date_col].astype(str) >= cutoff]
    except ValueError:
        pass

    eps_vals = pd.to_numeric(sub["eps"], errors="coerce").dropna()
    if eps_vals.empty:
        return None
    return float(eps_vals.median())


def _compute_historical_surprises(
    express_df: pd.DataFrame,
    analyst_df: pd.DataFrame,
    ts_code: str,
) -> list[float]:
    """计算历史各期的 surprise (actual - consensus)。"""
    surprises: list[float] = []
    if express_df.empty:
        return surprises

    for _, row in express_df.iterrows():
        actual = row.get("_eps_actual")
        ann = str(row.get("ann_date", ""))
        if pd.isna(actual) or not ann:
            continue
        consensus = _get_consensus_eps(analyst_df, ts_code, ann)
        if consensus is not None:
            surprises.append(float(actual) - consensus)
    return surprises


def compute_earnings_momentum_factor(
    candidates: Sequence[EquityCandidateInfo],
    express_cache_dir: str | Path = "data/cache/advanced",
    trade_date: str = "",
    lookback_months: int = 12,
) -> dict[str, float]:
    """盈利动量因子 — 连续两期业绩快报 EPS 变化方向。

    EPS 环比增长 → 正信号 (盈利加速)
    EPS 环比下降 → 负信号

    Args:
        candidates: 候选标的
        express_cache_dir: 业绩快报缓存目录
        trade_date: 交易日 YYYYMMDD
        lookback_months: 回看月数

    Returns:
        {symbol: earnings_momentum}
    """
    if not trade_date or not candidates:
        return {}

    from aipha.factors.analyst_factors import _get_months_before

    symbols = {c.symbol for c in candidates}
    months = _get_months_before(trade_date, lookback_months)

    express_df = _load_express_data(express_cache_dir, months)
    if express_df.empty:
        return {}

    if "ann_date" in express_df.columns:
        express_df = express_df[
            express_df["ann_date"].astype(str) <= trade_date
        ].copy()

    eps_col = None
    for col in ("diluted_eps", "eps", "s_fa_eps_diluted"):
        if col in express_df.columns:
            eps_col = col
            break
    if eps_col is None:
        return {}

    express_df["_eps"] = pd.to_numeric(express_df[eps_col], errors="coerce")
    express_df = express_df.dropna(subset=["_eps"])

    result: dict[str, float] = {}
    for sym in symbols:
        sub = express_df[express_df["ts_code"] == sym].copy()
        if len(sub) < 2:
            continue
        sub = sub.sort_values("ann_date")
        eps_latest = sub["_eps"].iloc[-1]
        eps_prev = sub["_eps"].iloc[-2]
        if abs(eps_prev) < 1e-8:
            continue
        momentum = (eps_latest - eps_prev) / abs(eps_prev)
        if math.isfinite(momentum):
            result[sym] = float(np.clip(momentum, -5, 5))

    if result:
        logger.info(
            "盈利动量因子: %d/%d 有效 (date=%s)",
            len(result), len(symbols), trade_date,
        )
    return result


def compute_insider_net_buy_factor(
    candidates: Sequence[EquityCandidateInfo],
    holdertrade_cache_dir: str | Path = "data/cache/holder",
    trade_date: str = "",
    lookback_days: int = 90,
) -> dict[str, float]:
    """管理层增持催化因子。

    经济学解释 (Jeng, Metrick, Zeckhauser 2003):
    管理层/重要股东增持 → 内部人看好 → 正向信号。
    高管增持的信息量高于一般股东。

    计算:
    net_buy_score = (增持金额 - 减持金额) / 流通市值
    聚合过去 lookback_days 天的增减持公告。

    Args:
        candidates: 候选标的
        holdertrade_cache_dir: 增减持缓存目录
        trade_date: 交易日 YYYYMMDD
        lookback_days: 回看天数

    Returns:
        {symbol: net_buy_score}
    """
    if not trade_date or not candidates:
        return {}

    from datetime import datetime, timedelta

    symbols = {c.symbol for c in candidates}
    cache_dir = Path(holdertrade_cache_dir)

    # 计算回看日期
    td = datetime.strptime(trade_date[:8], "%Y%m%d")
    start_dt = td - timedelta(days=lookback_days)
    start_str = start_dt.strftime("%Y%m%d")

    # 加载相关月份的增减持数据
    from aipha.factors.analyst_factors import _get_months_before
    months_needed = _get_months_before(
        trade_date, (lookback_days // 30) + 2,
    )

    parts: list[pd.DataFrame] = []
    for ym in months_needed:
        fpath = cache_dir / f"holdertrade_{ym}.parquet"
        if fpath.exists():
            try:
                parts.append(pd.read_parquet(fpath))
            except Exception:
                pass
    if not parts:
        return {}

    df = pd.concat(parts, ignore_index=True)
    if df.empty or "ts_code" not in df.columns:
        return {}

    # PIT: 仅使用 ann_date 在 [start_str, trade_date] 的数据
    if "ann_date" in df.columns:
        df = df[
            (df["ann_date"].astype(str) >= start_str)
            & (df["ann_date"].astype(str) <= trade_date)
        ].copy()

    if df.empty:
        return {}

    # 确保数值字段
    for col in ("change_vol", "avg_price"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    result: dict[str, float] = {}
    for sym in symbols:
        sub = df[df["ts_code"] == sym]
        if sub.empty:
            continue

        buy_mask = sub["in_de"] == "IN"
        sell_mask = sub["in_de"] == "DE"

        if "avg_price" in sub.columns:
            buy_amt = (
                sub.loc[buy_mask, "change_vol"]
                * sub.loc[buy_mask, "avg_price"]
            ).sum()
            sell_amt = (
                sub.loc[sell_mask, "change_vol"]
                * sub.loc[sell_mask, "avg_price"]
            ).sum()
        else:
            buy_amt = sub.loc[buy_mask, "change_vol"].sum()
            sell_amt = sub.loc[sell_mask, "change_vol"].sum()

        net_amt = buy_amt - sell_amt

        # 归一化: 用候选股的流通市值
        cand = next((c for c in candidates if c.symbol == sym), None)
        if cand and hasattr(cand, "circ_mv") and cand.circ_mv:
            circ_mv = cand.circ_mv * 1e4  # 万元→元
            if circ_mv > 0:
                score = net_amt / circ_mv
            else:
                score = np.sign(net_amt) * 0.01
        else:
            score = np.sign(net_amt) * 0.01 if net_amt != 0 else 0.0

        if math.isfinite(score) and score != 0.0:
            result[sym] = float(score)

    if result:
        logger.info(
            "管理层增持因子: %d/%d 有效 (date=%s)",
            len(result), len(symbols), trade_date,
        )
    return result


# ============================================================================
# ML 批量特征接口 — 供 LightGBM 管线使用
# ============================================================================


def compute_sue_feature_batch(
    symbols: Sequence[str],
    trade_dates: Sequence[str],
    express_cache_dir: str | Path = "data/cache/advanced",
    analyst_cache_dir: str | Path = "data/cache/alternative",
    lookback_months: int = 12,
    min_history: int = 2,
) -> pd.DataFrame:
    """SUE 因子批量计算 — ML 特征管线适配版。

    与 compute_sue_factor 的差异:
    1. 输入为 (symbols, trade_dates) 而非 EquityCandidateInfo
    2. 缺失值返回 NaN (LightGBM 原生处理) 而非跳过
    3. 按日期分组批量计算, 避免重复加载缓存

    Returns:
        DataFrame[ts_code, trade_date, sue_score]
    """
    from aipha.factors.analyst_factors import _get_months_before

    express_cache_dir = Path(express_cache_dir)
    analyst_cache_dir = Path(analyst_cache_dir)
    sym_set = set(symbols)
    rows: list[dict[str, object]] = []
    _cols = ["ts_code", "trade_date", "sue_score"]

    # 按月分组所有日期, 减少重复加载
    date_list = sorted(set(trade_dates))
    if not date_list or not sym_set:
        return pd.DataFrame(columns=_cols)

    # 预加载全部所需月份数据
    all_months = _get_months_before(date_list[-1], lookback_months + 6)
    earliest_months = _get_months_before(date_list[0], lookback_months)
    all_months = sorted(set(all_months + earliest_months))

    express_all = _load_express_data(express_cache_dir, all_months)
    analyst_all = _load_analyst_consensus(analyst_cache_dir, all_months)

    if express_all.empty:
        logger.debug("SUE batch: express 数据为空")
        for td in date_list:
            for sym in sym_set:
                rows.append({"ts_code": sym, "trade_date": td,
                             "sue_score": float("nan")})
        return pd.DataFrame(rows, columns=_cols)

    # 确保 EPS 字段
    eps_col = None
    for col in ("diluted_eps", "eps", "s_fa_eps_diluted"):
        if col in express_all.columns:
            eps_col = col
            break
    if eps_col is None:
        for td in date_list:
            for sym in sym_set:
                rows.append({"ts_code": sym, "trade_date": td,
                             "sue_score": float("nan")})
        return pd.DataFrame(rows, columns=_cols)

    express_all["_eps_actual"] = pd.to_numeric(
        express_all[eps_col], errors="coerce",
    )
    express_all = express_all.dropna(subset=["_eps_actual"])

    for td in date_list:
        # PIT 过滤
        if "ann_date" in express_all.columns:
            exp_pit = express_all[
                express_all["ann_date"].astype(str) <= td
            ]
        else:
            exp_pit = express_all

        for sym in sym_set:
            sub_exp = exp_pit[exp_pit["ts_code"] == sym]
            if sub_exp.empty:
                rows.append({"ts_code": sym, "trade_date": td,
                             "sue_score": float("nan")})
                continue

            sub_exp = sub_exp.sort_values("ann_date")
            latest_exp = sub_exp.iloc[-1]
            actual_eps = latest_exp["_eps_actual"]
            exp_ann_date = str(latest_exp.get("ann_date", ""))

            consensus_eps = _get_consensus_eps(
                analyst_all, sym, exp_ann_date,
            )
            if consensus_eps is None:
                rows.append({"ts_code": sym, "trade_date": td,
                             "sue_score": float("nan")})
                continue

            surprise = actual_eps - consensus_eps
            hist = _compute_historical_surprises(sub_exp, analyst_all, sym)

            if len(hist) >= min_history:
                sigma = np.std(hist, ddof=1)
                sue = (surprise / sigma) if sigma > 1e-8 else (
                    np.sign(surprise) * 3.0
                )
            else:
                sue = np.clip(surprise * 10, -5, 5)

            rows.append({
                "ts_code": sym,
                "trade_date": td,
                "sue_score": float(sue) if math.isfinite(sue) else float("nan"),
            })

    result = pd.DataFrame(rows)
    n_valid = result["sue_score"].notna().sum()
    logger.info(
        "SUE batch: %d/%d 有效 (%d 日期 × %d 标的)",
        n_valid, len(result), len(date_list), len(sym_set),
    )
    return result


def compute_insider_feature_batch(
    symbols: Sequence[str],
    trade_dates: Sequence[str],
    holdertrade_cache_dir: str | Path = "data/cache/holder",
    lookback_days: int = 90,
) -> pd.DataFrame:
    """管理层增持因子批量计算 — ML 特征管线适配版。

    输出列:
    - insider_net_buy_90d: 净增持金额 (正=增持, 负=减持), 对数化
    - insider_txn_count: 交易笔数 (增持+减持总次数)

    缺失值返回 NaN (LightGBM 原生处理)。

    Returns:
        DataFrame[ts_code, trade_date, insider_net_buy_90d, insider_txn_count]
    """
    from datetime import datetime, timedelta
    from aipha.factors.analyst_factors import _get_months_before

    cache_dir = Path(holdertrade_cache_dir)
    sym_set = set(symbols)
    date_list = sorted(set(trade_dates))
    rows: list[dict[str, object]] = []

    if not date_list:
        return pd.DataFrame(
            columns=["ts_code", "trade_date",
                     "insider_net_buy_90d", "insider_txn_count"],
        )

    # 预加载全部可能用到的月份
    max_months = (lookback_days // 30) + 3
    all_months_set: set[str] = set()
    for td in date_list:
        all_months_set.update(_get_months_before(td, max_months))
    all_months = sorted(all_months_set)

    parts: list[pd.DataFrame] = []
    for ym in all_months:
        fpath = cache_dir / f"holdertrade_{ym}.parquet"
        if fpath.exists():
            try:
                parts.append(pd.read_parquet(fpath))
            except Exception:
                pass
    if not parts:
        # 全部返回 NaN
        for td in date_list:
            for sym in sym_set:
                rows.append({
                    "ts_code": sym, "trade_date": td,
                    "insider_net_buy_90d": float("nan"),
                    "insider_txn_count": float("nan"),
                })
        return pd.DataFrame(rows)

    holder_df = pd.concat(parts, ignore_index=True)
    if "ts_code" not in holder_df.columns:
        return pd.DataFrame(
            columns=["ts_code", "trade_date",
                     "insider_net_buy_90d", "insider_txn_count"],
        )

    for col in ("change_vol", "avg_price"):
        if col in holder_df.columns:
            holder_df[col] = pd.to_numeric(
                holder_df[col], errors="coerce",
            ).fillna(0)

    for td in date_list:
        td_dt = datetime.strptime(td[:8], "%Y%m%d")
        start_str = (td_dt - timedelta(days=lookback_days)).strftime("%Y%m%d")

        # PIT 窗口过滤
        if "ann_date" in holder_df.columns:
            window = holder_df[
                (holder_df["ann_date"].astype(str) >= start_str)
                & (holder_df["ann_date"].astype(str) <= td)
            ]
        else:
            window = holder_df

        for sym in sym_set:
            sub = window[window["ts_code"] == sym]
            if sub.empty:
                rows.append({
                    "ts_code": sym, "trade_date": td,
                    "insider_net_buy_90d": float("nan"),
                    "insider_txn_count": float("nan"),
                })
                continue

            txn_count = len(sub)
            buy_mask = sub["in_de"] == "IN"
            sell_mask = sub["in_de"] == "DE"

            if "avg_price" in sub.columns:
                buy_amt = (
                    sub.loc[buy_mask, "change_vol"]
                    * sub.loc[buy_mask, "avg_price"]
                ).sum()
                sell_amt = (
                    sub.loc[sell_mask, "change_vol"]
                    * sub.loc[sell_mask, "avg_price"]
                ).sum()
            else:
                buy_amt = sub.loc[buy_mask, "change_vol"].sum()
                sell_amt = sub.loc[sell_mask, "change_vol"].sum()

            net_amt = buy_amt - sell_amt
            # 对数化处理大金额差异
            if net_amt > 0:
                net_log = np.log1p(net_amt)
            elif net_amt < 0:
                net_log = -np.log1p(-net_amt)
            else:
                net_log = 0.0

            rows.append({
                "ts_code": sym,
                "trade_date": td,
                "insider_net_buy_90d": float(net_log),
                "insider_txn_count": float(txn_count),
            })

    result = pd.DataFrame(rows)
    n_valid = result["insider_net_buy_90d"].notna().sum()
    logger.info(
        "Insider batch: %d/%d 有效 (%d 日期 × %d 标的)",
        n_valid, len(result), len(date_list), len(sym_set),
    )
    return result
