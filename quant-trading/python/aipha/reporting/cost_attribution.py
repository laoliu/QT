"""交易成本归因 — 拆解执行成本

将实际执行成本分解为:
  - 佣金/税费
  - 滑点 (实际价 vs 决策价)
  - 市场冲击 (timing cost)
  - 机会成本 (未成交部分)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class CostBreakdown:
    commission: float       # 佣金 + 印花税
    slippage: float         # 滑点
    market_impact: float    # 冲击成本
    opportunity: float      # 机会成本
    total: float
    detail: pd.DataFrame    # 逐笔明细


class CostAttribution:
    """交易成本归因引擎。

    Parameters
    ----------
    trades : DataFrame
        columns: datetime, symbol, side, quantity, decision_price,
                 fill_price, fill_qty, commission, arrival_price
    """

    def __init__(self, trades: pd.DataFrame) -> None:
        self.trades = trades.copy()

    def run(self) -> CostBreakdown:
        df = self.trades
        n = len(df)

        # 佣金 (直接来源)
        total_commission = df["commission"].sum() if "commission" in df.columns else 0.0

        # 滑点: fill_price vs decision_price
        if "decision_price" in df.columns and "fill_price" in df.columns:
            side_sign = df["side"].map({"buy": 1, "sell": -1}).fillna(1)
            slippage_per = side_sign * (df["fill_price"] - df["decision_price"])
            qty = df["fill_qty"] if "fill_qty" in df.columns else df["quantity"]
            slip_cost = (slippage_per * qty).sum()
        else:
            slip_cost = 0.0

        # 市场冲击: fill_price vs arrival_price (VWAP window)
        if "arrival_price" in df.columns and "fill_price" in df.columns:
            side_sign = df["side"].map({"buy": 1, "sell": -1}).fillna(1)
            impact_per = side_sign * (df["fill_price"] - df["arrival_price"])
            qty = df["fill_qty"] if "fill_qty" in df.columns else df["quantity"]
            impact_cost = (impact_per * qty).sum()
        else:
            impact_cost = 0.0

        # 机会成本: 未成交部分 × 价格变动
        if "fill_qty" in df.columns and "quantity" in df.columns:
            unfilled = df["quantity"] - df["fill_qty"]
            if "close_price" in df.columns and "decision_price" in df.columns:
                side_sign = df["side"].map({"buy": 1, "sell": -1}).fillna(1)
                missed_return = side_sign * (df["close_price"] - df["decision_price"])
                opp_cost = (unfilled * missed_return).clip(lower=0).sum()
            else:
                opp_cost = 0.0
        else:
            opp_cost = 0.0

        total = total_commission + slip_cost + impact_cost + opp_cost

        detail = df[["datetime", "symbol"]].copy() if "symbol" in df.columns else df[["datetime"]].copy()
        detail["commission"] = df.get("commission", 0)
        detail["slippage"] = slippage_per * qty if slip_cost else 0
        detail["impact"] = impact_per * qty if impact_cost else 0

        return CostBreakdown(
            commission=float(total_commission),
            slippage=float(slip_cost),
            market_impact=float(impact_cost),
            opportunity=float(opp_cost),
            total=float(total),
            detail=detail,
        )
