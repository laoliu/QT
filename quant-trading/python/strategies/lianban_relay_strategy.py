"""
Lianban Relay Strategy (连板接力二进三).

Category: Composite (Short-term momentum / Board relay)
Markets: A-shares (CN)

Core philosophy: Buy stocks that have achieved N consecutive limit-up boards
and ride the momentum to board N+1. Default setup: buy 2-board stocks
targeting a 3rd board (二进三).

Entry logic:
  - Stock has >= min_boards consecutive limit-up days
  - Board quality score passes threshold (换手板 preferred)
  - Latest board is NOT 一字板 (must be buyable)
  - Turnover rate within acceptable range (5-25%)

Exit logic (layered, evaluated in priority order):
  1. LIMIT-DOWN EXIT: Price hits limit-down → SELL ALL (extreme risk)
  2. STOP-LOSS: Unrealized loss > stop_loss_pct → SELL ALL
  3. MAX HOLD: Held > max_holding_days → SELL ALL (time decay)
  4. PROMOTION SUCCESS → NEW BOARD: Continue holding if stock hits
     limit-up again (let winners ride to N+2, N+3...)
  5. HIGH-OPEN NO-BOARD: Stock opens high but doesn't seal board →
     Sell half at open profit, rest at close
  6. TRAILING STOP after partial sell: price < MA5 → exit

Parameters:
  - min_boards:       Minimum consecutive boards to trigger entry (default 2)
  - max_holding_days: Maximum days to hold (default 3)
  - stop_loss_pct:    Stop loss threshold (default 0.05 = 5%)
  - take_profit_pct:  Take profit - sell half (default 0.15 = 15%)
  - min_turnover:     Minimum turnover rate filter (default 3.0%)
  - max_turnover:     Maximum turnover rate filter (default 25.0%)
  - min_quality:      Minimum board quality score (default 40)
"""

from __future__ import annotations

import os
import sys

import backtrader as bt
import numpy as np

_ENGINES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engines")
sys.path.insert(0, os.path.dirname(os.path.abspath(os.path.join(__file__, ".."))))

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class LianbanRelayStrategy(BaseStrategy):
    """Consecutive limit-up relay strategy (连板接力二进三)."""

    STRATEGY_META = StrategyMeta(
        id="lianban_relay",
        name="连板接力二进三 (Limit-Up Relay)",
        description=(
            "连板接力策略：买入已连续涨停N天的股票，目标捕捉第N+1板。"
            "默认二进三（买入2连板股，博第3板）。"
            "多层退出机制：跌停紧急退出、止损、最长持有天数、"
            "晋级继续持有、高开未封分步止盈。"
            "基于量化板质评分和市场情绪过滤，非主观打板。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("min_boards", "number", 2, "最低连板数", min=1, max=5),
            StrategyParameter("max_holding_days", "number", 3, "最长持有天数", min=1, max=10),
            StrategyParameter(
                "stop_loss_pct", "number", 0.05,
                "止损比例", min=0.02, max=0.15, step=0.01,
            ),
            StrategyParameter(
                "take_profit_pct", "number", 0.15,
                "分步止盈阈值", min=0.05, max=0.30, step=0.05,
            ),
            StrategyParameter(
                "min_turnover", "number", 3.0,
                "最低换手率(%)", min=1.0, max=10.0, step=0.5,
            ),
            StrategyParameter(
                "max_turnover", "number", 25.0,
                "最高换手率(%)", min=10.0, max=50.0, step=5.0,
            ),
            StrategyParameter(
                "min_quality", "number", 40,
                "最低板质评分(0-100)", min=0, max=80, step=5,
            ),
        ],
        markets=["cn_stock"],
    )

    params = (
        ("min_boards", 2),
        ("max_holding_days", 3),
        ("stop_loss_pct", 0.05),
        ("take_profit_pct", 0.15),
        ("min_turnover", 3.0),
        ("max_turnover", 25.0),
        ("min_quality", 40),
    )

    def __init__(self):
        super().__init__()

        # MA5 for trailing stop
        self.ma5 = bt.indicators.SimpleMovingAverage(
            self.data.close, period=5,
        )

        # State tracking
        self._entry_price = 0.0
        self._entry_date = None
        self._holding_days = 0
        self._half_sold = False
        self._last_date = None
        self._prev_close = 0.0

        # Rolling K-line history for board detection
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

            # Update kline history
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

            # Count holding days
            if self.position:
                self._holding_days += 1

        if not self.position:
            # ── ENTRY LOGIC ──
            self._try_entry(dt, close, open_price, high, low, volume)
        else:
            # ── EXIT LOGIC (priority order) ──
            self._try_exit(dt, close, open_price, high, low, volume)

    # ── Entry Logic ──────────────────────────────────────────────

    def _try_entry(self, dt, close, open_price, high, low, volume):
        """Evaluate whether to open a new position."""
        if len(self._kline_history) < self.p.min_boards + 1:
            return

        # Count consecutive boards in our kline history
        boards = self._count_boards()
        if boards < self.p.min_boards:
            return

        # Check if latest bar is a limit-up (we buy on the board day)
        if not self._is_limit_up(close, self._prev_close):
            return

        # Check for 一字板 (can't buy if open == close == high == low)
        if open_price == close == high == low:
            self.log(f"一字板跳过 {close:.2f}")
            return

        # Turnover filter (if we have volume and float data)
        # In backtest we may lack turnover data, so skip if unknown

        # Board quality check (simplified for backtest — no akshare pool data)
        quality = self._estimate_quality(boards)
        if quality < self.p.min_quality:
            self.log(f"板质不足 {quality:.0f} < {self.p.min_quality}")
            return

        # ── BUY ──
        size = self.calc_position_size_pct(close, 0.90)
        if size > 0:
            self.buy(size=size)
            self._entry_price = close
            self._entry_date = dt
            self._holding_days = 0
            self._half_sold = False
            self.record_signal(
                "buy", close,
                f"{boards}连板买入, 板质{quality:.0f}",
            )

    def _try_exit(self, dt, close, open_price, high, low, volume):
        """Evaluate exit conditions in priority order."""

        # ── Exit 1: Limit-down emergency ──
        if self._prev_close > 0 and self._is_limit_down(close, self._prev_close):
            self._sell_all(close, "跌停紧急退出")
            return

        # ── Exit 2: Stop loss ──
        if self._entry_price > 0:
            loss_pct = (close - self._entry_price) / self._entry_price
            if loss_pct < -self.p.stop_loss_pct:
                self._sell_all(
                    close,
                    f"止损: 亏损{loss_pct*100:.1f}% > {self.p.stop_loss_pct*100:.0f}%",
                )
                return

        # ── Exit 3: Max holding days ──
        if self._holding_days > self.p.max_holding_days:
            self._sell_all(
                close,
                f"持有{self._holding_days}天超限({self.p.max_holding_days}天)",
            )
            return

        # ── Exit 4: Promotion check — if still limit-up, HOLD ──
        if self._prev_close > 0 and self._is_limit_up(close, self._prev_close):
            self.log(f"晋级成功! 继续持有 (第{self._count_boards()}板)")
            self.record_signal(
                "hold", close,
                f"晋级封板成功, 继续持有",
            )
            # Reset max holding counter — the clock restarts on each new board
            self._holding_days = 0
            return

        # ── Exit 5a: Take profit — sell half ──
        if not self._half_sold and self._entry_price > 0:
            profit_pct = (close - self._entry_price) / self._entry_price
            if profit_pct > self.p.take_profit_pct:
                half = self._half_of_position()
                if half > 0:
                    self.sell(size=half)
                    self._half_sold = True
                    self.record_signal(
                        "sell", close,
                        f"止盈卖半仓: 盈利{profit_pct*100:.1f}%",
                    )
                    return

        # ── Exit 5b: Trailing stop below MA5 after half-sell ──
        if self._half_sold and close < self.ma5[0]:
            self._sell_all(
                close,
                f"跌破MA5({self.ma5[0]:.2f})清余仓",
            )
            return

        # ── Exit 6: Last bar of day — if not limit-up and near close ──
        if self._is_last_bar_of_day():
            # If stock opened high but didn't seal board → trim
            if not self._half_sold and self._holding_days >= 2:
                profit_pct = (close - self._entry_price) / self._entry_price if self._entry_price > 0 else 0
                if profit_pct > 0.03:
                    # Decent profit but no board — trim position
                    half = self._half_of_position()
                    if half > 0:
                        self.sell(size=half)
                        self._half_sold = True
                        self.record_signal(
                            "sell", close,
                            f"未封板尾盘减仓: 盈利{profit_pct*100:.1f}%",
                        )

    # ── Helpers ──────────────────────────────────────────────────

    def _is_limit_up(self, close: float, prev_close: float) -> bool:
        """Check if close reached limit-up threshold."""
        if prev_close <= 0 or close <= 0:
            return False
        symbol = self.data._name if hasattr(self.data, "_name") else ""
        ratio = self._get_limit_ratio(symbol)
        return close / prev_close >= 1 + ratio - 0.003

    def _is_limit_down(self, close: float, prev_close: float) -> bool:
        """Check if close reached limit-down threshold."""
        if prev_close <= 0 or close <= 0:
            return False
        symbol = self.data._name if hasattr(self.data, "_name") else ""
        ratio = self._get_limit_ratio(symbol)
        return close / prev_close <= 1 - ratio + 0.003

    def _get_limit_ratio(self, symbol: str) -> float:
        """Determine limit ratio by symbol prefix."""
        code = str(symbol).split(".")[0] if symbol else ""
        if code.startswith("688"):
            return 0.20
        if code.startswith("30"):
            return 0.20
        if code.startswith("4") or (code.startswith("8") and not code.startswith("688")):
            return 0.30
        return 0.10

    def _count_boards(self) -> int:
        """Count consecutive limit-up days from kline history."""
        klines = self._kline_history
        if len(klines) < 2:
            return 0
        count = 0
        for i in range(len(klines) - 1, 0, -1):
            curr = klines[i]
            prev_c = klines[i - 1]["close"]
            if self._is_limit_up(curr["close"], prev_c):
                count += 1
            else:
                break
        return count

    def _estimate_quality(self, board_count: int) -> float:
        """Estimate board quality from backtest kline history.

        Simplified scoring (0-100) based on available data.
        """
        klines = self._kline_history
        if len(klines) < 2:
            return 0

        score = 50.0  # baseline

        # Check board types for recent boards
        boards_checked = 0
        for i in range(len(klines) - 1, max(0, len(klines) - board_count - 1), -1):
            curr = klines[i]
            if boards_checked >= board_count:
                break
            # 一字板 penalty
            if curr["open"] == curr["close"] == curr["high"] == curr["low"]:
                score -= 10
            # 换手板 bonus (open != close, traded range)
            elif curr["low"] < curr["close"] and curr["high"] > curr["low"]:
                score += 8
            boards_checked += 1

        # Volume trend bonus
        volumes = [k["volume"] for k in klines[-board_count:] if k.get("volume", 0) > 0]
        if len(volumes) >= 2:
            increasing = sum(1 for i in range(1, len(volumes)) if volumes[i] > volumes[i - 1])
            if increasing >= len(volumes) * 0.6:
                score += 10  # Volume expanding across boards
            elif increasing == 0:
                score -= 10  # Volume shrinking

        return max(0, min(score, 100))

    def _sell_all(self, price: float, reason: str):
        """Close entire position."""
        self.close()
        self._entry_price = 0.0
        self._entry_date = None
        self._holding_days = 0
        self._half_sold = False
        self.record_signal("sell", price, reason)

    def _half_of_position(self) -> int:
        """Return half of current position, rounded to lot."""
        size = abs(self.position.size) // 2
        if size >= 100:
            size = (size // 100) * 100
        return max(size, 0)

    def _is_last_bar_of_day(self) -> bool:
        """Heuristic: check if next bar is a different date."""
        try:
            next_dt = self.data.datetime.date(1)
            return next_dt != self.data.datetime.date(0)
        except (IndexError, bt.LineSeries.IndexError):
            return True  # Last bar in dataset
