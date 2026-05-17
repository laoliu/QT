"""A 股公司治理因子 (Phase C / C-2).

基于 TuShareCorporateProvider 数据构建横截面因子：

C-2a 筹码集中度:
    holder_num_chg_pct  — 股东户数季度变动率 (负=集中, alpha 方向:负)
    holder_concentration — 户均持股变动方向 (集中→1, 分散→-1)

C-2b 增减持信号:
    net_change_ratio    — 净增减持比例 (增持>0)
    insider_buy_count   — 增持笔数

C-2c 分红因子:
    dividend_yield      — 近12月现金股息率
    div_continuity      — 连续分红年数

C-2d 质押风险:
    pledge_ratio        — 总质押比例
    pledge_change       — 质押比例变动
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------
# C-2a: 筹码集中度因子
# ----------------------------------------------------------------
def compute_holder_concentration(
    holder_df: pd.DataFrame,
) -> pd.DataFrame:
    """从股东户数数据计算筹码集中度因子。

    Args:
        holder_df: 须含 ts_code, end_date, holder_num 列。
            通常是多只股票的合并数据。

    Returns:
        DataFrame[ts_code, end_date, holder_num_chg_pct,
                  holder_concentration]
    """
    if holder_df.empty or "holder_num" not in holder_df.columns:
        return _empty("holder_num_chg_pct", "holder_concentration")

    df = holder_df[["ts_code", "end_date", "holder_num"]].copy()
    df = df.sort_values(["ts_code", "end_date"])
    df["holder_num"] = pd.to_numeric(df["holder_num"], errors="coerce")

    grouped = df.groupby("ts_code")["holder_num"]
    df["prev_num"] = grouped.shift(1)
    df["holder_num_chg_pct"] = (
        (df["holder_num"] - df["prev_num"]) / df["prev_num"]
    ).replace([np.inf, -np.inf], np.nan)

    # concentration: -1 if dispersing, +1 if concentrating
    df["holder_concentration"] = np.sign(-df["holder_num_chg_pct"])

    return df[["ts_code", "end_date",
               "holder_num_chg_pct", "holder_concentration"]].copy()


# ----------------------------------------------------------------
# C-2b: 增减持因子
# ----------------------------------------------------------------
def compute_insider_trading(
    trade_df: pd.DataFrame,
) -> pd.DataFrame:
    """从增减持明细计算净增减持因子。

    Args:
        trade_df: 须含 ts_code, ann_date, in_de, change_vol 列。
            in_de: 'IN'=增持, 'DE'=减持。

    Returns:
        DataFrame[ts_code, ann_date, net_change_vol,
                  insider_buy_count, net_change_ratio]
    """
    if trade_df.empty:
        return _empty("net_change_vol", "insider_buy_count", "net_change_ratio")

    df = trade_df.copy()
    df["change_vol"] = pd.to_numeric(df.get("change_vol", 0), errors="coerce").fillna(0)

    # 标记方向
    df["signed_vol"] = df["change_vol"]
    if "in_de" in df.columns:
        df.loc[df["in_de"].str.upper() == "DE", "signed_vol"] *= -1

    # 按 ts_code + ann_date 汇总
    agg = df.groupby(["ts_code", "ann_date"]).agg(
        net_change_vol=("signed_vol", "sum"),
        insider_buy_count=("signed_vol", lambda x: (x > 0).sum()),
    ).reset_index()

    # 净变动比例（需总股本，此处简化为原始值）
    agg["net_change_ratio"] = agg["net_change_vol"]  # 后续可除以总股本
    return agg


# ----------------------------------------------------------------
# C-2c: 分红因子
# ----------------------------------------------------------------
def compute_dividend_factors(
    div_df: pd.DataFrame,
    price_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """从分红方案计算股息率和连续性因子。

    Args:
        div_df: 须含 ts_code, end_date, cash_div_tax 列。
        price_df: 可选，含 ts_code, close 用于计算股息率。

    Returns:
        DataFrame[ts_code, end_date, cash_div, div_continuity,
                  dividend_yield]
    """
    if div_df.empty:
        return _empty("cash_div", "div_continuity", "dividend_yield")

    df = div_df.copy()
    cash_col = "cash_div_tax" if "cash_div_tax" in df.columns else "cash_div"
    if cash_col not in df.columns:
        return _empty("cash_div", "div_continuity", "dividend_yield")

    df["cash_div"] = pd.to_numeric(df[cash_col], errors="coerce").fillna(0)
    df = df.sort_values(["ts_code", "end_date"])

    # 连续分红年数
    def _continuity(g: pd.DataFrame) -> int:
        years = sorted(g["end_date"].str[:4].unique(), reverse=True)
        count = 0
        for y in years:
            if g[g["end_date"].str[:4] == y]["cash_div"].sum() > 0:
                count += 1
            else:
                break
        return count

    cont = df.groupby("ts_code").apply(
        _continuity, include_groups=False,
    ).reset_index()
    cont.columns = ["ts_code", "div_continuity"]

    # 最近一次分红
    latest = df.groupby("ts_code").last().reset_index()
    latest = latest[["ts_code", "end_date", "cash_div"]]
    latest = latest.merge(cont, on="ts_code", how="left")

    # 股息率 = cash_div / close (需价格)
    latest["dividend_yield"] = np.nan
    if price_df is not None and "close" in price_df.columns:
        merged = latest.merge(
            price_df[["ts_code", "close"]].drop_duplicates("ts_code"),
            on="ts_code", how="left",
        )
        merged["dividend_yield"] = merged["cash_div"] / merged["close"]
        latest = merged.drop(columns=["close"], errors="ignore")

    return latest


# ----------------------------------------------------------------
# C-2d: 质押因子
# ----------------------------------------------------------------
def compute_pledge_factors(
    pledge_df: pd.DataFrame,
) -> pd.DataFrame:
    """从股权质押统计计算质押风险因子。

    Args:
        pledge_df: 须含 ts_code, end_date, pledge_count,
                   unrest_pledge_ratio 或 total_share_ratio 列。

    Returns:
        DataFrame[ts_code, end_date, pledge_ratio, pledge_change]
    """
    if pledge_df.empty:
        return _empty("pledge_ratio", "pledge_change")

    df = pledge_df.copy()
    ratio_col = (
        "total_share_ratio"
        if "total_share_ratio" in df.columns
        else "unrest_pledge_ratio"
    )
    if ratio_col not in df.columns:
        return _empty("pledge_ratio", "pledge_change")

    df["pledge_ratio"] = pd.to_numeric(df[ratio_col], errors="coerce")
    df = df.sort_values(["ts_code", "end_date"])

    grouped = df.groupby("ts_code")["pledge_ratio"]
    df["pledge_change"] = grouped.diff(1)

    return df[["ts_code", "end_date",
               "pledge_ratio", "pledge_change"]].copy()


# ----------------------------------------------------------------
# 工具
# ----------------------------------------------------------------
def _empty(*cols: str) -> pd.DataFrame:
    base = ["ts_code", "end_date"]
    return pd.DataFrame(columns=base + list(cols))
