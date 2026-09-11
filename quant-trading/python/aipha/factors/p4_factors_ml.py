"""P4 因子: 分析师一致预期修正率 (ML 管道集成).

来自 Phase 0 门禁验证 (R132+):
  - Analyst Revision IC=3.25% @50-130B, IC_IR=0.81, 87.2%正向
  - 唯一通过中盘门禁的 P0 因子

集成方式: 从 data/cache/alternative/analyst_YYYYMM.parquet 加载,
聚合为月度一致预期修正率, PIT 合并到日线.

用法: 在 train_ml_factor.py 中通过 --p4-factors 启用.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

ROOT = PROJECT_ROOT
ALT_CACHE = ROOT / "data" / "cache" / "alternative"


def add_p4_factors(df: pd.DataFrame) -> int:
    """将 P4 因子(分析师修正)加入 df (原地修改).

    Returns: 新增列数.
    """
    added = 0
    n = _add_analyst_revision(df)
    added += n
    logger.info("P4 因子: 共新增 %d 列", added)
    return added


def _load_analyst_monthly() -> pd.DataFrame:
    """加载全部分析师月度缓存 → 聚合为每月每股的平均 EPS 预测."""
    parts = []
    for fp in sorted(ALT_CACHE.glob("analyst_*.parquet")):
        try:
            p = pd.read_parquet(fp)
            parts.append(p)
        except Exception:
            continue
    if not parts:
        logger.warning("无分析师数据 (%s)", ALT_CACHE)
        return pd.DataFrame()
    raw = pd.concat(parts, ignore_index=True)
    raw["report_date"] = pd.to_datetime(raw["report_date"], format="%Y%m%d", errors="coerce")
    raw["eps"] = pd.to_numeric(raw["eps"], errors="coerce")
    raw = raw.dropna(subset=["report_date", "eps", "ts_code"])
    raw = raw.rename(columns={"ts_code": "symbol"})
    # 月度聚合: 每只股票每月 EPS 平均预测 + 覆盖分析师数
    raw["year_month"] = raw["report_date"].dt.to_period("M")
    monthly = raw.groupby(["symbol", "year_month"]).agg(
        eps_mean=("eps", "mean"),
        eps_std=("eps", "std"),
        n_analysts=("eps", "count"),
    ).reset_index()
    monthly = monthly.sort_values(["symbol", "year_month"])
    # 修正率 = (当月EPS - 上月EPS) / |上月EPS|
    monthly["eps_prev"] = monthly.groupby("symbol")["eps_mean"].shift(1)
    monthly["analyst_rev"] = (
        (monthly["eps_mean"] - monthly["eps_prev"])
        / monthly["eps_prev"].abs().clip(lower=0.01)
    ).clip(-5, 5)
    # 分歧度 = std / |mean|
    monthly["analyst_disp"] = (
        monthly["eps_std"] / monthly["eps_mean"].abs().clip(lower=0.01)
    ).clip(0, 10)
    # 转为 datetime 月初 便于 merge_asof
    monthly["month_start"] = monthly["year_month"].dt.to_timestamp()
    logger.info("分析师月度聚合: %d 条, %d 只, %s~%s",
                len(monthly), monthly["symbol"].nunique(),
                monthly["year_month"].min(), monthly["year_month"].max())
    return monthly


def _add_analyst_revision(df: pd.DataFrame) -> int:
    """将分析师修正因子 PIT 合并到日线 df.

    步骤:
    1. 加载月度分析师聚合数据
    2. 按 (symbol, month) 合并到日线 df
    3. 前向填充至多 3 个月
    4. 对 NaN 填 0 (无覆盖 = 无修正)
    5. 加截面排名因子 analyst_rev_rank
    """
    monthly = _load_analyst_monthly()
    if monthly.empty:
        logger.warning("分析师数据为空, P4 因子跳过")
        return 0

    # df 中日期列可能叫 datetime 或 trade_date
    date_col = None
    for col in ("datetime", "trade_date", "date"):
        if col in df.columns:
            date_col = col
            break
    if date_col is None:
        logger.error("df 缺少日期列 (datetime/trade_date/date)")
        return 0

    td = pd.to_datetime(df[date_col])
    df["_p4_month"] = td.dt.to_period("M")

    monthly_key = monthly[
        ["symbol", "year_month", "analyst_rev", "analyst_disp", "n_analysts"]
    ].copy()
    monthly_key = monthly_key.rename(columns={"year_month": "_p4_month"})

    before = set(df.columns)
    df_merged = df.merge(monthly_key, on=["symbol", "_p4_month"], how="left")

    for col in ["analyst_rev", "analyst_disp", "n_analysts"]:
        df[col] = df_merged[col].values

    # 前向填充 — 最多 63 个交易日(≈3月)
    for col in ["analyst_rev", "analyst_disp", "n_analysts"]:
        df[col] = df.groupby("symbol")[col].ffill(limit=63)

    df["analyst_rev"] = df["analyst_rev"].fillna(0.0)
    df["analyst_disp"] = df["analyst_disp"].fillna(0.0)
    df["n_analysts"] = df["n_analysts"].fillna(0.0)

    # 截面排名 (按每天)
    df["analyst_rev_rank"] = df.groupby(date_col)["analyst_rev"].rank(
        pct=True, method="average"
    )

    df.drop(columns=["_p4_month"], inplace=True, errors="ignore")

    new_cols = {"analyst_rev", "analyst_disp", "n_analysts", "analyst_rev_rank"}
    added = len(new_cols - before)
    cov = (df["analyst_rev"] != 0).mean()
    logger.info("P4 分析师因子: 新增 %d 列, 非零覆盖率 %.1f%%", added, cov * 100)
    return added
