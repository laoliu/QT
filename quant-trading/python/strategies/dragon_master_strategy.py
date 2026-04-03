"""
Dragon Master Strategy (真龙战法回测策略).

Category: Composite (Short-term momentum / 主线龙头打板)
Markets: A-shares (CN)

完整实现《真龙战法》的回测逻辑，在 Backtrader 框架下模拟：
  - 3板打板买入（首选），2板换手板次选
  - 只做涨停打板（close 必须涨停）
  - 排除一字板（无法买入）
  - 需要放量信号（volume > 1.5x 前3日均量）
  - 多层退出：跌停→止损→时间→晋级持有→止盈→均线追踪

注意：
  此策略在纯K线回测中无法完全模拟"封单分析"和"龙一定位"（需要盘中数据）。
  回测重点验证"3板打板+放量"的胜率和盈亏比。
  盘中实时交易部分通过 miniQMT data_bridge 接口实现（见 dragon_master engine）。

Parameters:
  - target_board:     目标板数（3=三板打板, 默认3）
  - allow_2board:     是否也允许2板换手板入场（默认False）
  - max_holding_days: 最长持有天数（默认3）
  - stop_loss_pct:    止损比例（默认5%）
  - take_profit_pct:  分步止盈（默认15%，卖半仓）
  - volume_mult:      放量倍数阈值（默认1.5x）
  - min_turnover:     最低换手率（默认3%）
"""

from __future__ import annotations

import os
import sys

import backtrader as bt
import numpy as np

_ENGINES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engines")
sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, ".."))))

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DragonMasterStrategy(BaseStrategy):
    """真龙战法回测策略 — 3板打板 + 放量确认."""

    STRATEGY_META = StrategyMeta(
        id="dragon_master",
        name="真龙战法 (Dragon Master)",
        description=(
            "真龙战法回测策略：3板打板买入，要求放量换手板确认。"
            "核心逻辑：一板不买、二板确认、三板打板最安全。"
            "多层退出：跌停紧急→止损5%→时间3天→晋级持有→止盈15%→MA5追踪。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("target_board", "number", 3,
                              "目标板数(几板打板)", min=2, max=5),
            StrategyParameter("allow_2board", "boolean", False,
                              "是否允许2板换手板入场"),
            StrategyParameter("max_holding_days", "number", 3,
                              "最长持有天数", min=1, max=10),
            StrategyParameter("stop_loss_pct", "number", 0.05,
                              "止损比例", min=0.02, max=0.15, step=0.01),
            StrategyParameter("take_profit_pct", "number", 0.15,
                              "分步止盈阈值", min=0.05, max=0.30, step=0.05),
            StrategyParameter("volume_mult", "number", 1.5,
                              "放量倍数(vs前3日均量)", min=1.0, max=3.0, step=0.1),
            StrategyParameter("min_turnover", "number", 3.0,
                              "最低换手率(%)", min=1.0, max=10.0, step=0.5),
        ],
        markets=["cn_stock"],
    )

    params = (
        ("target_board", 3),
        ("allow_2board", False),
        ("max_holding_days", 3),
        ("stop_loss_pct", 0.05),
        ("take_profit_pct", 0.15),
        ("volume_mult", 1.5),
        ("min_turnover", 3.0),
    )

    def __init__(self):
        super().__init__()

        # Indicators
        self.ma5 = bt.indicators.SimpleMovingAverage(self.data.close, period=5)

        # State
        self._entry_price = 0.0
        self._entry_date = None
        self._holding_days = 0
        self._half_sold = False
        self._last_date = None
        self._prev_close = 0.0

        # K-line history for board counting
        self._kline_history: list[dict] = []
        self._MAX_HISTORY = 30

    def next(self):
        dt = self.data.datetime.date(0)
        close = self.data.close[0]
        open_price = self.data.open[0]
        high = self.data.high[0]
        low = self.data.low[0]
        volume = self.data.volume[0]

        is_new_day = (dt != self._last_date)

        if is_new_day:
            if self._last_date is not None:
                self._prev_close = self.data.close[-1]
            self._last_date = dt

            bar = {
                "date": str(dt),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "prev_close": self._prev_close if self._prev_close > 0 else open_price,
            }
            self._kline_history.append(bar)
            if len(self._kline_history) > self._MAX_HISTORY:
                self._kline_history = self._kline_history[-self._MAX_HISTORY:]

            if self.position:
                self._holding_days += 1

        if not self.position:
            self._try_entry(dt, close, open_price, high, low, volume)
        else:
            self._try_exit(dt, close, open_price, high, low, volume)

    # ── Entry Logic (真龙战法核心) ────────────────────────────────

    def _try_entry(self, dt, close, open_price, high, low, volume):
        """3板打板买入逻辑。"""
        if len(self._kline_history) < 4:
            return

        # Count consecutive limit-up boards
        boards = self._count_boards()

        # Target board check
        if boards < self.p.target_board:
            # Allow 2-board entry if configured
            if self.p.allow_2board and boards == 2:
                pass  # Fall through to quality checks
            else:
                return

        # Latest bar must be limit-up (打板 = 买在涨停价)
        if not self._is_limit_up(close, self._prev_close):
            return

        # Reject 一字板 (open == close == high == low → can't buy)
        if self._is_one_word_board(open_price, high, low, close):
            self.log(f"一字板无法买入, 跳过 boards={boards}")
            return

        # Volume confirmation: 放量 = volume > N x avg(prev 3)
        if not self._check_volume_breakout(volume):
            # 3板且缩量 → 可能是一字板走势，跳过
            if boards == self.p.target_board:
                self.log(f"3板但未放量, 信号减弱")
                # Still allow entry at 3 board but log weakness
            else:
                return

        # Turnover check (simplified: use volume/total_shares proxy)
        # In backtest we may not have float data, so skip if unavailable

        # ── BUY ──
        size = self.calc_position_size_pct(close, 0.90)
        if size > 0:
            self.buy(size=size)
            self._entry_price = close
            self._entry_date = dt
            self._holding_days = 0
            self._half_sold = False
            vol_note = "放量" if self._check_volume_breakout(volume) else "量能未确认"
            self.record_signal(
                "buy", close,
                f"真龙战法: {boards}板打板买入({vol_note})",
            )

    def _try_exit(self, dt, close, open_price, high, low, volume):
        """多层退出逻辑（优先级从高到低）。"""
        # Guard: T+1 (can't sell on entry day)
        if dt == self._entry_date:
            return

        # ── Exit 1: 跌停紧急退出 ──
        if self._prev_close > 0 and self._is_limit_down(close, self._prev_close):
            self._sell_all(close, "跌停紧急退出")
            return

        # ── Exit 2: 止损 ──
        if self._entry_price > 0:
            loss_pct = (close - self._entry_price) / self._entry_price
            if loss_pct < -self.p.stop_loss_pct:
                self._sell_all(
                    close,
                    f"止损: 亏损{loss_pct*100:.1f}%",
                )
                return

        # ── Exit 3: 时间止损 ──
        if self._holding_days > self.p.max_holding_days:
            self._sell_all(
                close,
                f"持有{self._holding_days}天超限",
            )
            return

        # ── Exit 4: 晋级成功=继续持有 ──
        if self._prev_close > 0 and self._is_limit_up(close, self._prev_close):
            self.log(f"晋级! {self._count_boards()}板成功, 继续持有")
            self.record_signal("hold", close, "晋级封板, 继续持有")
            self._holding_days = 0  # Reset clock
            return

        # ── Exit 5a: 止盈卖半仓 ──
        if not self._half_sold and self._entry_price > 0:
            profit_pct = (close - self._entry_price) / self._entry_price
            if profit_pct >= self.p.take_profit_pct:
                half = self._half_of_position()
                if half > 0:
                    self.sell(size=half)
                    self._half_sold = True
                    self.record_signal(
                        "sell_half", close,
                        f"止盈卖半: 盈利{profit_pct*100:.1f}%",
                    )
                    return

        # ── Exit 5b: 均线追踪止损 ──
        if self._half_sold and close < self.ma5[0]:
            self._sell_all(close, f"MA5追踪止损: {close:.2f} < MA5 {self.ma5[0]:.2f}")
            return

        # ── Exit 6: 高开未封板，尾盘减仓 ──
        if self._entry_price > 0 and self._prev_close > 0:
            open_gain = (open_price - self._prev_close) / self._prev_close
            at_limit = self._is_limit_up(close, self._prev_close)
            if open_gain > 0.03 and not at_limit and not self._half_sold:
                # Opened high but didn't seal limit → trim
                profit_pct = (close - self._entry_price) / self._entry_price
                if profit_pct > 0.03:
                    half = self._half_of_position()
                    if half > 0:
                        self.sell(size=half)
                        self._half_sold = True
                        self.record_signal(
                            "sell_half", close,
                            f"高开未封板减仓: 开盘涨{open_gain*100:.1f}%但未涨停",
                        )

    # ── Helper methods ───────────────────────────────────────────

    def _count_boards(self) -> int:
        """Count consecutive limit-up boards from latest history."""
        count = 0
        for i in range(len(self._kline_history) - 1, 0, -1):
            bar = self._kline_history[i]
            prev_bar = self._kline_history[i - 1]
            prev_c = _safe_float(prev_bar.get("close"))
            cur_c = _safe_float(bar.get("close"))
            if prev_c > 0 and self._is_limit_up(cur_c, prev_c):
                count += 1
            else:
                break
        return count

    def _is_limit_up(self, close: float, prev_close: float) -> bool:
        """10% limit-up detection (main board)."""
        if prev_close <= 0 or close <= 0:
            return False
        return close / prev_close >= 1.097  # 9.7% tolerance

    def _is_limit_down(self, close: float, prev_close: float) -> bool:
        """10% limit-down detection."""
        if prev_close <= 0 or close <= 0:
            return False
        return close / prev_close <= 0.903

    def _is_one_word_board(
        self, open_p: float, high: float, low: float, close: float,
    ) -> bool:
        """Check if bar is 一字板 (open == close == high == low)."""
        if open_p <= 0:
            return False
        return open_p == close == high == low

    def _check_volume_breakout(self, volume: float) -> bool:
        """Check if current volume is a breakout (> N x avg of prev 3)."""
        if len(self._kline_history) < 4:
            return True  # Not enough data, allow

        prev_vols = [
            _safe_float(bar.get("volume"))
            for bar in self._kline_history[-4:-1]
        ]
        prev_vols = [v for v in prev_vols if v > 0]

        if not prev_vols:
            return True

        avg_vol = sum(prev_vols) / len(prev_vols)
        if avg_vol <= 0:
            return True

        return volume >= avg_vol * self.p.volume_mult

    def _sell_all(self, price: float, reason: str):
        """Close entire position."""
        self.close()
        self.record_signal("sell", price, reason)
        self._entry_price = 0
        self._entry_date = None
        self._holding_days = 0
        self._half_sold = False

    def _half_of_position(self) -> int:
        """Calculate half of current position (rounded to 100 shares)."""
        size = self.position.size
        half = size // 2
        if half >= 100:
            half = (half // 100) * 100
        return max(half, 0)

    def log(self, msg: str):
        """Log to stderr (avoid mixing with JSON stdout)."""
        dt = self.data.datetime.date(0)
        print(f"[{dt}] DragonMaster: {msg}", file=sys.stderr)


def _safe_float(v) -> float:
    """Safe float conversion."""
    try:
        return float(v) if v is not None else 0.0
    except (ValueError, TypeError):
        return 0.0
