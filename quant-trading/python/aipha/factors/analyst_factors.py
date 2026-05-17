"""分析师预测与替代数据因子模块

从 TuShare 缓存数据中计算：
- 分析师一致预期 EPS 修正率因子 (Analyst EPS Revision)
- 股东户数变化因子 (Shareholder Count Change)
- 业绩预告超预期因子 (Earnings Forecast Surprise)

数据来源（均为本地 Parquet 缓存，无需 API 调用）：
- 分析师预测: data/cache/alternative/analyst_YYYYMM.parquet
- 股东户数: data/cache/reference/stk_holdernumber/stk_holdernumber_{code}.parquet
- 业绩预告: data/cache/advanced/forecast_YYYYMM.parquet

所有因子遵循 PIT (Point-in-Time) 原则：仅使用 ann_date/report_date ≤ trade_date 的数据。
因子方向：值越大 = 越看好。

References:
    - Jegadeesh & Kim (2010): analyst revision factor IC 1-3%
    - McLean & Pontiff (2016): post-publication alpha decay
    - Bernard & Thomas (1989): PEAD (Post-Earnings-Announcement Drift)
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence

import pandas as pd

if TYPE_CHECKING:
    from aipha.strategy.equity_rotation_base import EquityCandidateInfo

logger = logging.getLogger(__name__)

# 前向声明 — 避免在模块加载时报缺失
_analyst_cache: dict[str, pd.DataFrame] = {}
_holder_cache: dict[str, pd.DataFrame] = {}


# ============================================================================
# 数据加载工具
# ============================================================================


def load_analyst_months(
    cache_dir: str | Path,
    months: list[str],
) -> pd.DataFrame:
    """加载指定月份的分析师预测缓存。

    Args:
        cache_dir: 缓存根目录（如 data/cache/alternative）
        months: YYYYMM 月份列表

    Returns:
        合并后的 DataFrame（可能为空）
    """
    cache_dir = Path(cache_dir)
    parts: list[pd.DataFrame] = []
    for ym in months:
        key = f"analyst_{ym}"
        if key in _analyst_cache:
            parts.append(_analyst_cache[key])
            continue
        fpath = cache_dir / f"analyst_{ym}.parquet"
        if fpath.exists():
            try:
                df = pd.read_parquet(fpath)
                _analyst_cache[key] = df
                parts.append(df)
            except Exception as e:
                logger.warning("读取分析师数据 %s 失败: %s", fpath.name, e)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def load_holder_data(
    cache_dir: str | Path,
    ts_code: str,
) -> pd.DataFrame:
    """加载单只股票的股东户数数据。

    Args:
        cache_dir: 缓存目录（如 data/cache/reference/stk_holdernumber）
        ts_code: 股票代码（如 000001.SZ）

    Returns:
        DataFrame with columns [ts_code, ann_date, end_date, holder_num]
    """
    cache_dir = Path(cache_dir)
    key = f"holder_{ts_code}"
    if key in _holder_cache:
        return _holder_cache[key]
    fname = f"stk_holdernumber_{ts_code.replace('.', '_')}.parquet"
    fpath = cache_dir / fname
    if not fpath.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(fpath)
        if "ann_date" in df.columns:
            df = df.sort_values("ann_date").reset_index(drop=True)
        _holder_cache[key] = df
        return df
    except Exception as e:
        logger.warning("读取股东户数 %s 失败: %s", ts_code, e)
        return pd.DataFrame()


# ============================================================================
# 分析师一致预期 EPS 修正率因子
# ============================================================================


def _get_months_before(trade_date: str, n_months: int) -> list[str]:
    """获取 trade_date 前 n 个月的 YYYYMM 列表。"""
    from datetime import datetime

    dt = datetime.strptime(trade_date[:8], "%Y%m%d")
    months: list[str] = []
    for i in range(n_months):
        y = dt.year
        m = dt.month - i
        while m <= 0:
            m += 12
            y -= 1
        months.append(f"{y:04d}{m:02d}")
    return months


def compute_analyst_revision_factor(
    candidates: Sequence[EquityCandidateInfo],
    cache_dir: str | Path = "data/cache/alternative",
    trade_date: str = "",
    lookback_months: int = 3,
    min_analysts: int = 2,
) -> dict[str, float]:
    """分析师 EPS 一致预期修正率因子。

    PIT 合规：使用 report_date ≤ trade_date 过滤。

    Args:
        candidates: 候选标的列表
        cache_dir: 分析师数据缓存目录
        trade_date: 交易日期 YYYYMMDD
        lookback_months: 回看月数
        min_analysts: 最少分析师覆盖数

    Returns:
        {symbol: revision_rate} — 正值=上调，负值=下调
    """
    if not trade_date or not candidates:
        return {}

    symbols = {c.symbol for c in candidates}
    months = _get_months_before(trade_date, lookback_months + 1)

    df = load_analyst_months(cache_dir, months)
    if df.empty or "ts_code" not in df.columns:
        return {}

    df = df[df["ts_code"].isin(symbols)].copy()
    if "report_date" in df.columns:
        df = df[df["report_date"].astype(str) <= trade_date]
    if df.empty or "eps" not in df.columns:
        return {}

    df["eps"] = pd.to_numeric(df["eps"], errors="coerce")
    df = df.dropna(subset=["eps"])
    if "report_date" in df.columns:
        df["ym"] = df["report_date"].astype(str).str[:6]
    else:
        return {}

    result: dict[str, float] = {}
    for sym in symbols:
        sub = df[df["ts_code"] == sym]
        if len(sub) < min_analysts:
            continue
        monthly = sub.groupby("ym")["eps"].agg(["median", "count"])
        monthly = monthly[monthly["count"] >= min_analysts]
        monthly = monthly.sort_index()
        if len(monthly) < 2:
            continue
        eps_recent = monthly["median"].iloc[-1]
        eps_prev = monthly["median"].iloc[-2]
        if abs(eps_prev) < 1e-8:
            continue
        revision = (eps_recent - eps_prev) / abs(eps_prev)
        if math.isfinite(revision):
            result[sym] = revision

    if result:
        logger.info(
            "分析师修正因子: %d/%d 有效 (date=%s)",
            len(result), len(symbols), trade_date,
        )
    return result

# ============================================================================
# 业绩预告超预期因子
# ============================================================================


def load_forecast_months(
    cache_dir: str | Path,
    months: list[str],
) -> pd.DataFrame:
    """加载指定月份的业绩预告缓存数据。"""
    cache_dir = Path(cache_dir)
    parts: list[pd.DataFrame] = []
    for ym in months:
        fpath = cache_dir / f"forecast_{ym}.parquet"
        if fpath.exists():
            try:
                parts.append(pd.read_parquet(fpath))
            except Exception as e:
                logger.warning("读取业绩预告 %s 失败: %s", fpath.name, e)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def compute_forecast_surprise_factor(
    candidates: Sequence[EquityCandidateInfo],
    cache_dir: str | Path = "data/cache/advanced",
    trade_date: str = "",
    lookback_months: int = 6,
) -> dict[str, float]:
    """业绩预告超预期因子。

    利用业绩预告的 type 字段和预告盈利变化幅度。
    预增/扭亏/略增为正面信号，预减/续亏/略减为负面信号。

    PIT 合规：仅使用 ann_date ≤ trade_date。

    Args:
        candidates: 候选标的列表
        cache_dir: 业绩预告缓存目录
        trade_date: 交易日期 YYYYMMDD
        lookback_months: 回看月数

    Returns:
        {symbol: surprise_score} — 正面预告得正分
    """
    if not trade_date or not candidates:
        return {}

    symbols = {c.symbol for c in candidates}
    months = _get_months_before(trade_date, lookback_months)

    df = load_forecast_months(cache_dir, months)
    if df.empty or "ts_code" not in df.columns:
        return {}

    df = df[df["ts_code"].isin(symbols)].copy()
    if "ann_date" in df.columns:
        df = df[df["ann_date"].astype(str) <= trade_date]
    if df.empty:
        return {}

    # type 字段映射分数
    type_scores = {
        "预增": 2.0, "扭亏": 2.0, "续盈": 1.0, "略增": 1.0,
        "略减": -1.0, "预减": -2.0, "续亏": -2.0, "首亏": -2.0,
        "不确定": 0.0,
    }

    result: dict[str, float] = {}
    for sym in symbols:
        sub = df[df["ts_code"] == sym]
        if sub.empty:
            continue
        # 取最新的业绩预告
        if "ann_date" in sub.columns:
            sub = sub.sort_values("ann_date")
        latest = sub.iloc[-1]
        tp = str(latest.get("type", "")).strip()
        score = type_scores.get(tp, 0.0)
        # 增强：用 p_change_min/max 的均值调整分数
        pmin = pd.to_numeric(latest.get("p_change_min"), errors="coerce")
        pmax = pd.to_numeric(latest.get("p_change_max"), errors="coerce")
        if pd.notna(pmin) and pd.notna(pmax):
            avg_change = (pmin + pmax) / 200.0  # 转为小数
            score += avg_change  # 变化幅度越大，分数越高
        if math.isfinite(score) and score != 0.0:
            result[sym] = score

    if result:
        logger.info(
            "业绩预告因子: %d/%d 有效 (date=%s)",
            len(result), len(symbols), trade_date,
        )
    return result
# ============================================================================
# 股东户数变化因子
# ============================================================================


def compute_holder_change_factor(
    candidates: Sequence[EquityCandidateInfo],
    cache_dir: str | Path = "data/cache/reference/stk_holdernumber",
    trade_date: str = "",
) -> dict[str, float]:
    """股东户数变化因子。

    户数减少 → 筹码集中（机构吸筹）→ 正面信号。
    返回 -1 × 变化率，使得户数减少 = 因子值为正。

    PIT 合规：仅使用 ann_date ≤ trade_date 的报告。

    Args:
        candidates: 候选标的列表
        cache_dir: 股东户数缓存目录
        trade_date: 交易日期 YYYYMMDD

    Returns:
        {symbol: -holder_change_rate} — 正值=户数减少=集中
    """
    if not trade_date or not candidates:
        return {}

    result: dict[str, float] = {}
    for c in candidates:
        df = load_holder_data(cache_dir, c.symbol)
        if df.empty or "ann_date" not in df.columns:
            continue
        if "holder_num" not in df.columns:
            continue
        # PIT: 仅使用公告日 ≤ 交易日的数据
        pit = df[df["ann_date"].astype(str) <= trade_date]
        if len(pit) < 2:
            continue
        latest = pit.iloc[-1]["holder_num"]
        prev = pit.iloc[-2]["holder_num"]
        if prev is None or prev <= 0:
            continue
        try:
            change = (float(latest) - float(prev)) / float(prev)
            if math.isfinite(change):
                result[c.symbol] = -change  # 负变化=集中=正信号
        except (TypeError, ValueError):
            continue

    if result:
        logger.info(
            "股东户数因子: %d/%d 有效 (date=%s)",
            len(result), len(candidates), trade_date,
        )
    return result


# ============================================================================
# ML 批量特征接口 — 供 LightGBM 管线使用
# ============================================================================


def compute_analyst_feature_batch(
    symbols: list[str],
    trade_dates: list[str],
    analyst_cache_dir: str | Path = "data/cache/alternative",
    forecast_cache_dir: str | Path = "data/cache/advanced",
    analyst_lookback_months: int = 3,
    forecast_lookback_months: int = 6,
) -> pd.DataFrame:
    """分析师+预告因子批量计算 — ML 特征管线适配版。

    输出列:
    - analyst_revision_3m: EPS 一致预期修正率 (无 min_analysts 过滤)
    - forecast_surprise: 业绩预告超预期分数
    - analyst_coverage_count: 分析师覆盖数 (作为置信度权重)

    与策略版本的关键差异:
    1. **去除 min_analysts 硬过滤**: ML 能处理低覆盖度数据
    2. 缺失值返回 NaN (LightGBM 原生处理)
    3. 新增 analyst_coverage_count 作为辅助特征

    Returns:
        DataFrame[ts_code, trade_date, analyst_revision_3m,
                  forecast_surprise, analyst_coverage_count]
    """
    sym_set = set(symbols)
    date_list = sorted(set(trade_dates))
    rows: list[dict[str, object]] = []

    cols = ["ts_code", "trade_date", "analyst_revision_3m",
            "forecast_surprise", "analyst_coverage_count"]

    if not date_list:
        return pd.DataFrame(columns=cols)

    # 预加载分析师数据
    all_analyst_months: set[str] = set()
    for td in date_list:
        all_analyst_months.update(
            _get_months_before(td, analyst_lookback_months + 2),
        )
    analyst_df = load_analyst_months(
        analyst_cache_dir, sorted(all_analyst_months),
    )

    # 预加载预告数据
    all_forecast_months: set[str] = set()
    for td in date_list:
        all_forecast_months.update(
            _get_months_before(td, forecast_lookback_months + 1),
        )
    forecast_df = load_forecast_months(
        forecast_cache_dir, sorted(all_forecast_months),
    )

    # type 字段映射分数 (与策略版一致)
    type_scores = {
        "预增": 2.0, "扭亏": 2.0, "续盈": 1.0, "略增": 1.0,
        "略减": -1.0, "预减": -2.0, "续亏": -2.0, "首亏": -2.0,
        "不确定": 0.0,
    }

    for td in date_list:
        # --- 分析师修正 (PIT 过滤) ---
        a_revision: dict[str, float] = {}
        a_coverage: dict[str, float] = {}

        if not analyst_df.empty and "ts_code" in analyst_df.columns:
            a_sub = analyst_df[analyst_df["ts_code"].isin(sym_set)].copy()
            if "report_date" in a_sub.columns:
                a_sub = a_sub[a_sub["report_date"].astype(str) <= td]
            if "eps" in a_sub.columns:
                a_sub["eps"] = pd.to_numeric(a_sub["eps"], errors="coerce")
                a_sub = a_sub.dropna(subset=["eps"])
                if "report_date" in a_sub.columns:
                    a_sub["ym"] = a_sub["report_date"].astype(str).str[:6]

                    for sym in sym_set:
                        s = a_sub[a_sub["ts_code"] == sym]
                        a_coverage[sym] = float(len(s))
                        if s.empty:
                            continue
                        monthly = s.groupby("ym")["eps"].agg(
                            ["median", "count"],
                        )
                        # 不做 min_analysts 过滤
                        monthly = monthly.sort_index()
                        if len(monthly) < 2:
                            continue
                        eps_r = monthly["median"].iloc[-1]
                        eps_p = monthly["median"].iloc[-2]
                        if abs(eps_p) < 1e-8:
                            continue
                        rev = (eps_r - eps_p) / abs(eps_p)
                        if math.isfinite(rev):
                            a_revision[sym] = rev

        # --- 预告超预期 (PIT 过滤) ---
        f_surprise: dict[str, float] = {}

        if not forecast_df.empty and "ts_code" in forecast_df.columns:
            f_sub = forecast_df[forecast_df["ts_code"].isin(sym_set)].copy()
            if "ann_date" in f_sub.columns:
                f_sub = f_sub[f_sub["ann_date"].astype(str) <= td]
            for sym in sym_set:
                sf = f_sub[f_sub["ts_code"] == sym]
                if sf.empty:
                    continue
                if "ann_date" in sf.columns:
                    sf = sf.sort_values("ann_date")
                latest = sf.iloc[-1]
                tp = str(latest.get("type", "")).strip()
                score = type_scores.get(tp, 0.0)
                pmin = pd.to_numeric(
                    latest.get("p_change_min"), errors="coerce",
                )
                pmax = pd.to_numeric(
                    latest.get("p_change_max"), errors="coerce",
                )
                if pd.notna(pmin) and pd.notna(pmax):
                    score += (pmin + pmax) / 200.0
                if math.isfinite(score):
                    f_surprise[sym] = score

        # --- 组装行 ---
        for sym in sym_set:
            rows.append({
                "ts_code": sym,
                "trade_date": td,
                "analyst_revision_3m": a_revision.get(
                    sym, float("nan"),
                ),
                "forecast_surprise": f_surprise.get(
                    sym, float("nan"),
                ),
                "analyst_coverage_count": a_coverage.get(sym, 0.0),
            })

    result = pd.DataFrame(rows, columns=cols)
    n_rev = result["analyst_revision_3m"].notna().sum()
    n_fct = result["forecast_surprise"].notna().sum()
    logger.info(
        "Analyst batch: revision=%d forecast=%d (%d 日期 × %d 标的)",
        n_rev, n_fct, len(date_list), len(sym_set),
    )
    return result