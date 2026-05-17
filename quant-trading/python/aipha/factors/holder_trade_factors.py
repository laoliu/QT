"""增减持因子 — 基于股东增减持 (stk_holdertrade).

Phase C / C-2b: A 股基本面因子.

经济学解释 (原则 A):
    重要股东/高管增持 → 内部人看好 → 正向信号.
    重要股东/高管减持 → 内部人看淡 → 负向信号.
    学术支持: Insider trading (Jeng, Metrick, Zeckhauser 2003),
    中国市场高管增减持具有显著预测能力 (IC ~ 2-4%).

因子列表:
    - net_buy_ratio: 净增持比例 (增持股数 - 减持股数) / 总股本
    - insider_sentiment: 内部人情绪 (增持次数 - 减持次数, 取符号)
    - net_buy_amount: 净增持金额 (万元)

数据源: TuShareHolderProvider.load_holdertrade()
PIT 要求: 使用 ann_date (公告日) 做时点对齐.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class HolderTradeFactorRow:
    """单只股票单期增减持因子."""
    ts_code: str
    ann_date: str
    net_buy_ratio: float = 0.0
    insider_sentiment: float = 0.0
    net_buy_amount: float = 0.0


def compute_trade_factors_by_date(
    trade_df: pd.DataFrame,
    ts_code: str,
    ann_date: str,
) -> HolderTradeFactorRow:
    """计算单只股票在指定公告日的增减持因子.

    Args:
        trade_df: 该股票的增减持数据 (已按 ann_date 过滤).
        ts_code: 股票代码.
        ann_date: 公告日期.

    Returns:
        HolderTradeFactorRow.
    """
    if trade_df.empty:
        return HolderTradeFactorRow(ts_code=ts_code, ann_date=ann_date)
    # 增持 vs 减持
    buy_mask = trade_df["in_de"] == "IN"
    sell_mask = trade_df["in_de"] == "DE"
    buy_vol = trade_df.loc[buy_mask, "change_vol"].sum()
    sell_vol = trade_df.loc[sell_mask, "change_vol"].sum()
    net_vol = buy_vol - sell_vol
    # 净增持比例 (相对总股本)
    total_share = trade_df["total_share"].max()
    net_buy_ratio = (
        net_vol / total_share if total_share and total_share > 0 else 0.0
    )
    # 内部人情绪 (增持次数 - 减持次数, 归一化)
    n_buy = buy_mask.sum()
    n_sell = sell_mask.sum()
    total_trades = n_buy + n_sell
    insider_sentiment = (
        (n_buy - n_sell) / total_trades if total_trades > 0 else 0.0
    )
    # 净增持金额 (万元)
    buy_amount = (
        trade_df.loc[buy_mask, "change_vol"]
        * trade_df.loc[buy_mask, "avg_price"]
    ).sum() / 10000
    sell_amount = (
        trade_df.loc[sell_mask, "change_vol"]
        * trade_df.loc[sell_mask, "avg_price"]
    ).sum() / 10000
    net_buy_amount = buy_amount - sell_amount
    return HolderTradeFactorRow(
        ts_code=ts_code,
        ann_date=ann_date,
        net_buy_ratio=float(net_buy_ratio),
        insider_sentiment=float(insider_sentiment),
        net_buy_amount=float(net_buy_amount),
    )


def build_trade_factors_panel(
    trade_panel: pd.DataFrame,
    lookback_days: int = 90,
) -> pd.DataFrame:
    """批量计算增减持因子面板.

    对每只股票, 在每个公告日汇总前 lookback_days 天的
    增减持记录, 计算净增持比例和内部人情绪.

    Args:
        trade_panel: 多只股票的 stk_holdertrade 合并数据.
        lookback_days: 回看天数 (自然日).

    Returns:
        因子面板 DataFrame[ts_code, ann_date, net_buy_ratio,
                           insider_sentiment, net_buy_amount].
    """
    if trade_panel.empty:
        return pd.DataFrame(
            columns=[
                "ts_code", "ann_date", "net_buy_ratio",
                "insider_sentiment", "net_buy_amount",
            ],
        )
    required = {"ts_code", "ann_date", "in_de", "change_vol"}
    missing = required - set(trade_panel.columns)
    if missing:
        raise ValueError(f"缺少必需列: {missing}")
    # 确保 avg_price / total_share 存在 (填 0)
    df = trade_panel.copy()
    if "avg_price" not in df.columns:
        df["avg_price"] = 0.0
    if "total_share" not in df.columns:
        df["total_share"] = 0.0
    df["avg_price"] = df["avg_price"].fillna(0.0)
    df["total_share"] = df["total_share"].fillna(0.0)
    df["change_vol"] = df["change_vol"].fillna(0.0)
    # 按 (ts_code, ann_date) 汇总
    rows: list[dict] = []
    for (ts_code, ann_date), group in df.groupby(
        ["ts_code", "ann_date"],
    ):
        row = compute_trade_factors_by_date(
            group, str(ts_code), str(ann_date),
        )
        rows.append({
            "ts_code": row.ts_code,
            "ann_date": row.ann_date,
            "net_buy_ratio": row.net_buy_ratio,
            "insider_sentiment": row.insider_sentiment,
            "net_buy_amount": row.net_buy_amount,
        })
    return pd.DataFrame(rows)
