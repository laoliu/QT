"""
Auction Buy + Multi-Condition Exit Strategy (竞价买入+多条件卖出).

Category: Composite (Short-term / Daily-Reset)
Markets: A-shares (CN), adaptable to other markets

Original design from an experienced trader for QMT platform.
Adapted to backtrader engine with full signal recording.

Core philosophy: Combine systematic daily buying with a layered
exit system that covers gap-down protection, limit-down exits,
momentum decay, and stepped profit-taking.

Entry logic:
  - OPEN BUY: Buy a fixed amount at each day's open (auction buy)
  - ADD POSITION: If intraday price rises >N% vs open → add smaller amount (once/day)

Exit logic (4 independent rules, evaluated in order):
  1. GAP-DOWN + FURTHER DROP: Open drops >3% vs prev close AND price drops
     another >2% vs today's open → SELL ALL (catch falling knife protection)
  2. LIMIT-DOWN: Price hits daily limit-down → SELL ALL (extreme risk exit)
  3. NO NEW HIGH: Today's high ≤ yesterday's high → SELL ALL at 14:55
     (momentum decay filter — if today can't even exceed yesterday's high,
     the uptrend is fading)
  4. STEPPED PROFIT-TAKE: Unrealized gain >25% → sell half;
     remaining shares: sell at 14:55 if price < MA5 (trailing exit)

Parameters:
  - buy_pct:              Fraction of portfolio for daily open buy (default 0.20 = 20%)
  - add_pct:              Fraction for intraday add (default 0.10 = 10%)
  - open_drop_threshold:  Gap-down threshold vs prev close (default -0.03 = -3%)
  - further_drop:         Further drop threshold vs today open (default -0.02 = -2%)
  - add_rise_threshold:   Intraday rise to trigger add (default 0.02 = 2%)
  - profit_half_sell:     Profit % to sell half position (default 0.25 = 25%)
  - ma_period:            MA period for trailing stop after half-sell (default 5)
  - close_bar_minutes:    Minutes before close to evaluate rules 3&4 (default 5)
"""

from __future__ import annotations

import sys

import backtrader as bt
import numpy as np

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class AuctionStrategy(BaseStrategy):
    """Auction-style daily buy with multi-condition exit system."""

    STRATEGY_META = StrategyMeta(
        id="auction",
        name="竞价多条件退出 (Auction Multi-Exit)",
        description=(
            "竞价买入+多条件卖出策略：每日开盘定额买入，盘中涨幅达标加仓。"
            "搭配四重卖出机制——开盘跳空保护、跌停紧急退出、"
            "动量衰退过滤（当日最高价未创新高14:55清仓）、"
            "分步止盈（涨幅>25%卖半仓+MA5跟踪剩余）。"
            "源自资深交易员的实战框架，适用于A股短线交易。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("buy_pct", "number", 0.20, "每日买入比例", min=0.05, max=0.50, step=0.05),
            StrategyParameter("add_pct", "number", 0.10, "加仓比例", min=0.02, max=0.30, step=0.02),
            StrategyParameter(
                "open_drop_threshold", "number", -0.03,
                "开盘跌幅阈值（vs昨收）", min=-0.10, max=-0.01, step=0.01,
            ),
            StrategyParameter(
                "further_drop", "number", -0.02,
                "盘中再跌阈值（vs今开）", min=-0.10, max=-0.01, step=0.01,
            ),
            StrategyParameter(
                "add_rise_threshold", "number", 0.02,
                "加仓涨幅阈值（vs今开）", min=0.01, max=0.10, step=0.01,
            ),
            StrategyParameter(
                "profit_half_sell", "number", 0.25,
                "卖半仓利润阈值", min=0.10, max=0.50, step=0.05,
            ),
            StrategyParameter("ma_period", "number", 5, "跟踪止盈均线周期", min=3, max=20),
        ],
        markets=["cn_stock"],
    )

    params = (
        ("buy_pct", 0.20),
        ("add_pct", 0.10),
        ("open_drop_threshold", -0.03),
        ("further_drop", -0.02),
        ("add_rise_threshold", 0.02),
        ("profit_half_sell", 0.25),
        ("ma_period", 5),
    )

    def __init__(self):
        super().__init__()

        # MA indicator for trailing stop after half-sell
        self.ma = bt.indicators.SimpleMovingAverage(
            self.data.close, period=self.p.ma_period
        )

        # Daily state tracking
        self._prev_close = 0.0       # Yesterday's close
        self._prev_high = 0.0        # Yesterday's high
        self._today_open = 0.0       # Today's open price
        self._today_high = 0.0       # Today's running high
        self._last_date = None       # Last processed date
        self._bought_today = False   # Whether we bought at open today
        self._added_today = False    # Whether we added position today
        self._half_sold = False      # Whether we sold half for profit-taking
        self._open_drop_flag = False # Whether open gapped down >3%
        self._cost_price = 0.0       # Weighted average cost
        self._close_checked = False  # Whether 14:55 logic was executed

    def next(self):
        dt = self.data.datetime.date(0)
        close = self.data.close[0]
        open_price = self.data.open[0]
        high = self.data.high[0]
        low = self.data.low[0]

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 1. New day detection and state reset
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        is_new_day = (dt != self._last_date)
        if is_new_day:
            # Save previous day data before resetting
            if self._last_date is not None:
                self._prev_close = self.data.close[-1]
                self._prev_high = self._today_high
            else:
                # First day — use open as prev_close to avoid false gap-down
                self._prev_close = open_price
                self._prev_high = high

            self._last_date = dt
            self._today_open = open_price
            self._today_high = high
            self._bought_today = False
            self._added_today = False
            self._open_drop_flag = False
            self._close_checked = False
            # Note: _half_sold only resets on full liquidation

        # Update running high
        self._today_high = max(self._today_high, high)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 2. Open bar logic (first bar of day)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if is_new_day and not self._bought_today:
            self._bought_today = True

            # 2a. Auction buy — buy fixed fraction of portfolio at open
            portfolio_value = self.broker.getvalue()
            buy_amount = portfolio_value * self.p.buy_pct
            buy_size = self._calc_lot_size(open_price, buy_amount)

            if buy_size > 0 and self.broker.getcash() >= buy_size * open_price:
                self.buy(size=buy_size)
                self._update_cost(open_price, buy_size)
                self.record_signal(
                    "buy", open_price,
                    f"集合竞价买入: {buy_size}股@{open_price:.2f}, "
                    f"成本{self._cost_price:.2f}",
                )

            # 2b. Check gap-down flag
            if self._prev_close > 0:
                gap = (open_price - self._prev_close) / self._prev_close
                if gap < self.p.open_drop_threshold:
                    self._open_drop_flag = True
                    self.log(
                        f"开盘跌幅 {gap*100:.2f}%, 触发跳空标记"
                    )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 3. Exit Rule 1: Gap-down + further drop → SELL ALL
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if self.position and self._open_drop_flag and self._today_open > 0:
            change_from_open = (close - self._today_open) / self._today_open
            if change_from_open < self.p.further_drop:
                self._sell_all(
                    close,
                    f"开盘跌+再跌: 开盘跌>{abs(self.p.open_drop_threshold)*100:.0f}%, "
                    f"盘中再跌{change_from_open*100:.2f}%",
                )
                return

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 4. Exit Rule 2: Limit-down → SELL ALL
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if self.position and self._prev_close > 0:
            # Simplified: assume 10% limit for all stocks in backtest
            # (backtrader doesn't have board type info)
            limit_ratio = 0.10
            down_limit = round(self._prev_close * (1 - limit_ratio), 2)
            if close <= down_limit:
                self._sell_all(close, f"跌停卖出: 跌停价{down_limit:.2f}")
                return

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 5. Intraday add position: rise >N% vs open
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if (
            self.position
            and not self._added_today
            and not self._half_sold
            and self._today_open > 0
        ):
            rise = (close - self._today_open) / self._today_open
            if rise > self.p.add_rise_threshold:
                portfolio_value = self.broker.getvalue()
                add_amount = portfolio_value * self.p.add_pct
                add_size = self._calc_lot_size(close, add_amount)
                if add_size > 0 and self.broker.getcash() >= add_size * close:
                    self.buy(size=add_size)
                    self._update_cost(close, add_size)
                    self._added_today = True
                    self.record_signal(
                        "buy", close,
                        f"盘中加仓: 涨幅{rise*100:.2f}%>{self.p.add_rise_threshold*100:.0f}%, "
                        f"加仓{add_size}股@{close:.2f}",
                    )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 6. Exit Rule 4a: Profit >25% → sell half
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if self.position and not self._half_sold and self._cost_price > 0:
            profit_ratio = (close - self._cost_price) / self._cost_price
            if profit_ratio > self.p.profit_half_sell:
                half_size = self._half_of_position()
                if half_size > 0:
                    self.sell(size=half_size)
                    self._half_sold = True
                    self.record_signal(
                        "sell", close,
                        f"涨幅{profit_ratio*100:.1f}%>{self.p.profit_half_sell*100:.0f}% "
                        f"卖出半仓{half_size}股@{close:.2f}",
                    )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 7. Close-of-day logic (simulates 14:55 rules)
        #    Use the last bar of each day: if next bar is a new date,
        #    this is the "closing" bar.
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        is_last_bar = self._is_last_bar_of_day()
        if is_last_bar and self.position and not self._close_checked:
            self._close_checked = True

            # 7a. Exit Rule 3: Today's high ≤ yesterday's high → sell all
            if self._prev_high > 0 and self._today_high <= self._prev_high:
                self._sell_all(
                    close,
                    f"最高价未创新高: 今高{self._today_high:.2f} ≤ 昨高{self._prev_high:.2f}",
                )
                return

            # 7b. Exit Rule 4b: Half sold + below MA → sell remainder
            if self._half_sold:
                ma_val = self.ma[0]
                if close < ma_val:
                    self._sell_all(
                        close,
                        f"半仓破MA{self.p.ma_period}: 价格{close:.2f} < MA={ma_val:.2f}",
                    )
                else:
                    self.log(
                        f"14:55 半仓持有: {close:.2f} > MA{self.p.ma_period}={ma_val:.2f}"
                    )

    # ── Helpers ─────────────────────────────────────────────────

    def _is_last_bar_of_day(self) -> bool:
        """Check if this is the last bar of the trading day.

        For daily data: always True (one bar per day).
        For intraday: check if the next bar is on a different date.
        """
        # If we're at the last bar of the dataset, treat as last bar of day
        if len(self.data) - self.data.buflen() >= -1:
            # Check if next bar exists
            try:
                next_date = self.data.datetime.date(1)
                return next_date != self.data.datetime.date(0)
            except (IndexError, Exception):
                return True
        return False

    def _calc_lot_size(self, price: float, amount: float) -> int:
        """Calculate buy size in shares, rounded to 100-lot for A-shares."""
        if price <= 0:
            return 0
        size = int(amount / price)
        if size >= 100:
            size = (size // 100) * 100
        return max(size, 0)

    def _update_cost(self, price: float, shares: int):
        """Update weighted-average cost price after a buy."""
        current_pos = self.position.size if self.position else 0
        # Position is BEFORE this buy is executed by the broker,
        # but we track our own cost for signal accuracy
        old_vol = max(current_pos, 0)
        old_cost = self._cost_price
        total = old_vol + shares
        if total > 0:
            self._cost_price = (old_cost * old_vol + price * shares) / total
        else:
            self._cost_price = price

    def _half_of_position(self) -> int:
        """Calculate half of current position, rounded to 100-lot."""
        pos = self.position.size if self.position else 0
        if pos <= 0:
            return 0
        half = pos // 2
        if half >= 100:
            half = (half // 100) * 100
        return max(half, 0)

    def _sell_all(self, price: float, reason: str):
        """Full liquidation with signal recording."""
        pos = self.position.size if self.position else 0
        if pos <= 0:
            return
        profit = 0.0
        if self._cost_price > 0:
            profit = (price - self._cost_price) / self._cost_price * 100
        self.close()
        self.record_signal(
            "sell", price,
            f"【{reason}】全仓卖出{pos}股, 盈亏{profit:.1f}%",
        )
        # Reset state
        self._cost_price = 0.0
        self._half_sold = False
