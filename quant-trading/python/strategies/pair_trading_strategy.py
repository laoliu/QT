"""
Pair Trading Strategy (配对交易 / 统计套利).

Category: Arbitrage / Statistical Arbitrage
Markets: All (works best with highly correlated pairs)

A market-neutral strategy that profits from the mean-reversion of the
price spread between two co-integrated assets.

Core concepts:
  - Choose two historically correlated assets (e.g., 中国银行+工商银行)
  - Test for cointegration (Engle-Granger test from statsmodels)
  - Compute the spread (log-price difference or OLS residual)
  - Normalize spread to z-score
  - Long the underperformer & short the outperformer when z > z_entry
  - Close when spread reverts to z < z_exit

Implementation notes:
  - This strategy requires TWO data feeds in backtrader
  - The first feed (data0) is the primary asset, the second (data1) is the pair
  - Cointegration is tested on a rolling lookback window
  - If cointegration breaks down (p-value too high), positions are closed

Parameters:
  - lookback:     Rolling window for spread calculation (default 60)
  - z_entry:      Z-score threshold to enter trade (default 2.0)
  - z_exit:       Z-score threshold to exit trade (default 0.5)
  - coint_pvalue: Maximum p-value to accept cointegration (default 0.05)
"""

from __future__ import annotations

import sys

import backtrader as bt
import numpy as np

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class PairTradingStrategy(BaseStrategy):
    """Statistical Arbitrage: Mean-reversion of cointegrated spread."""

    STRATEGY_META = StrategyMeta(
        id="pair-trading",
        name="配对交易 (Pair Trading)",
        description=(
            "统计套利策略：选择两只高相关性股票，通过Engle-Granger协整检验确认配对关系，"
            "当价差偏离均值超过z_entry个标准差时入场（做多弱势+做空强势），"
            "价差回归至z_exit时平仓。市场中性策略，盈利来自价差的均值回归。"
            "适用于银行股配对（如600036+601166）、ETF与成分股配对等。"
        ),
        category="arbitrage",
        parameters=[
            StrategyParameter("lookback", "number", 60, "回看窗口（交易日）", min=20, max=252),
            StrategyParameter("z_entry", "number", 2.0, "入场z-score阈值", min=1.0, max=4.0, step=0.1),
            StrategyParameter("z_exit", "number", 0.5, "平仓z-score阈值", min=0.0, max=1.5, step=0.1),
            StrategyParameter(
                "coint_pvalue", "number", 0.05, "协整检验p值阈值", min=0.01, max=0.10, step=0.01
            ),
        ],
        markets=["cn_stock", "us_stock", "hk_stock"],
    )

    params = (
        ("lookback", 60),
        ("z_entry", 2.0),
        ("z_exit", 0.5),
        ("coint_pvalue", 0.05),
    )

    def __init__(self):
        super().__init__()

        # Validate: need exactly 2 data feeds
        if len(self.datas) < 2:
            print(
                "PairTrading requires 2 data feeds. "
                "Pass symbols as comma-separated: 'SYM1,SYM2'",
                file=sys.stderr,
            )
            # Still init — engine will catch the error
            self._valid = False
            return

        self._valid = True
        self._spread_long = False   # True if we are long data0, short data1
        self._spread_short = False  # True if we are short data0, long data1

    def next(self):
        if not self._valid:
            return

        lookback = self.p.lookback

        # Need enough data to compute spread
        if len(self.data0) < lookback:
            return

        # Collect close prices for the lookback window
        p0 = np.array([self.data0.close[-i] for i in range(lookback - 1, -1, -1)])
        p1 = np.array([self.data1.close[-i] for i in range(lookback - 1, -1, -1)])

        # Check for zero/nan
        if np.any(p0 <= 0) or np.any(p1 <= 0):
            return

        # Log prices
        log_p0 = np.log(p0)
        log_p1 = np.log(p1)

        # OLS regression: log_p0 = alpha + beta * log_p1 + residual
        beta, alpha = np.polyfit(log_p1, log_p0, 1)

        # Compute spread (residual)
        spread = log_p0 - (alpha + beta * log_p1)

        # Z-score of the current spread value
        spread_mean = np.mean(spread)
        spread_std = np.std(spread)
        if spread_std < 1e-8:
            return

        z_score = (spread[-1] - spread_mean) / spread_std

        # ── Run cointegration test (lazy import for performance) ──
        try:
            from statsmodels.tsa.stattools import coint
            _, p_value, _ = coint(log_p0, log_p1)
        except Exception:
            # Fail closed: without a valid test result we cannot establish
            # cointegration, so do not treat the pair as safe to trade.
            p_value = float("nan")

        cointegrated = np.isfinite(p_value) and p_value < self.p.coint_pvalue

        # ── Trading logic ──
        close0 = self.data0.close[0]
        close1 = self.data1.close[0]

        if self._spread_long:
            # Currently: long data0, short data1
            # Exit when spread reverts
            if abs(z_score) < self.p.z_exit or not cointegrated:
                self.close(data=self.data0)
                self.close(data=self.data1)
                self._spread_long = False
                reason = "价差回归" if abs(z_score) < self.p.z_exit else "协整关系丧失"
                self.record_signal(
                    "sell",
                    close0,
                    f"配对平仓({reason}): z={z_score:.2f}, p={p_value:.4f}",
                )

        elif self._spread_short:
            # Currently: short data0, long data1
            if abs(z_score) < self.p.z_exit or not cointegrated:
                self.close(data=self.data0)
                self.close(data=self.data1)
                self._spread_short = False
                reason = "价差回归" if abs(z_score) < self.p.z_exit else "协整关系丧失"
                self.record_signal(
                    "buy",
                    close0,
                    f"配对平仓({reason}): z={z_score:.2f}, p={p_value:.4f}",
                )

        else:
            # No position — look for entry
            if not cointegrated:
                return  # No trading without cointegration

            equity = self.broker.getvalue()
            # Use 40% of equity per leg
            size0 = int(equity * 0.40 / close0)
            size1 = int(equity * 0.40 / close1)
            if size0 >= 100:
                size0 = (size0 // 100) * 100
            if size1 >= 100:
                size1 = (size1 // 100) * 100

            if size0 <= 0 or size1 <= 0:
                return

            if z_score > self.p.z_entry:
                # Spread is too high: data0 overpriced relative to data1
                # Short data0, long data1
                self.sell(data=self.data0, size=size0)
                self.buy(data=self.data1, size=size1)
                self._spread_short = True
                self.record_signal(
                    "sell",
                    close0,
                    f"配对开仓(做空价差): z={z_score:.2f}, "
                    f"卖{self.data0._name}@{close0:.2f}, 买{self.data1._name}@{close1:.2f}, "
                    f"beta={beta:.3f}, p={p_value:.4f}",
                )

            elif z_score < -self.p.z_entry:
                # Spread is too low: data0 underpriced relative to data1
                # Long data0, short data1
                self.buy(data=self.data0, size=size0)
                self.sell(data=self.data1, size=size1)
                self._spread_long = True
                self.record_signal(
                    "buy",
                    close0,
                    f"配对开仓(做多价差): z={z_score:.2f}, "
                    f"买{self.data0._name}@{close0:.2f}, 卖{self.data1._name}@{close1:.2f}, "
                    f"beta={beta:.3f}, p={p_value:.4f}",
                )
