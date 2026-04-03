"""
Dragon Return Strategy (龙回头 / 龙头回调二次启动).

Category: Composite (Short-term momentum pullback)
Markets: A-shares (CN)

Core philosophy: Capture the second wave of leading stocks (龙头股) after
their first major rally. After a strong first rally featuring multiple
limit-up boards, the stock pulls back on shrinking volume, finding support
at key technical levels (Fibonacci retracement, moving averages, chip
concentration zone). When it stabilizes and volume returns, enter for the
second leg up.

The strategy combines four pillars:

1. Dragon Gene (龙头基因):
   - At least N limit-up boards within lookback period (excluding 一字板)
   - First wave gain >= min_first_wave_gain (default 30%)

2. Volume Structure (量能结构):
   - Rally phase: average turnover rate relatively high
   - Pullback phase: volume shrinks to <= vol_shrink_ratio of peak volume
     (exhaustion of selling pressure)

3. Time-Space Rule (时空规律):
   - Pullback duration within acceptable window (3-10 days)
   - Pullback depth <= max_retrace of the first wave gain (default 38.2%,
     i.e. Fibonacci 0.382)

4. Support Confluence (支撑体系):
   - MA support: price near 5/10/20-day moving average
   - Fibonacci retracement levels (0.382, 0.5, 0.618)
   - Volume recovery signal: current volume > vol_recovery_mult × pullback
     average volume

Entry signal: all four conditions met simultaneously.

Exit logic (layered):
  1. STOP-LOSS: unrealized loss > stop_loss_pct
  2. TIME STOP: held > max_hold_days
  3. TAKE-PROFIT: price gain >= take_profit_pct (sell half)
  4. TRAILING STOP: after partial sell, price drops below MA5 → exit rest
  5. SECOND LIMIT-UP: new limit-up board → hold (let winners ride)

Parameters:
  - lookback:            Period to scan for dragon gene (default 20)
  - min_boards:          Min limit-up boards in lookback (default 3)
  - min_first_wave_gain: Min first-wave gain (default 0.30 = 30%)
  - max_retrace:         Max pullback as fraction of first wave (default 0.382)
  - min_pullback_days:   Min pullback days (default 3)
  - max_pullback_days:   Max pullback days (default 10)
  - vol_shrink_ratio:    Pullback vol / peak vol threshold (default 0.30)
  - vol_recovery_mult:   Volume recovery multiple vs pullback avg (default 1.5)
  - stop_loss_pct:       Stop loss (default 0.05 = 5%)
  - take_profit_pct:     Take profit sell-half threshold (default 0.15 = 15%)
  - max_hold_days:       Max holding days (default 8)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DragonReturnStrategy(BaseStrategy):
    """龙回头 — Leading stock pullback re-entry strategy."""

    STRATEGY_META = StrategyMeta(
        id="dragon-return",
        name="龙回头 (Dragon Return)",
        description=(
            "龙回头策略：捕捉龙头股首波主升浪后，缩量回调至关键支撑位的二次启动机会。"
            "核心条件：龙头基因（N日内≥3涨停+首波涨幅≥30%）、缩量回调（量能萎缩至峰值30%以下）、"
            "时空规律（回调3-10天、幅度≤首波的38.2%）、支撑验证（均线+黄金分割+量能回升三重确认）。"
            "适用于A股龙头股波段交易。"
        ),
        category="composite",
        markets=["cn_stock"],
        parameters=[
            StrategyParameter("lookback", "number", 20, "龙头基因回看周期", min=10, max=40),
            StrategyParameter("min_boards", "number", 3, "最低涨停板数（排除一字板）", min=2, max=6),
            StrategyParameter(
                "min_first_wave_gain", "number", 0.30,
                "首波最低涨幅", min=0.15, max=0.60, step=0.05,
            ),
            StrategyParameter(
                "max_retrace", "number", 0.382,
                "最大回调比例（相对首波涨幅）", min=0.20, max=0.618, step=0.01,
            ),
            StrategyParameter("min_pullback_days", "number", 3, "最短回调天数", min=1, max=5),
            StrategyParameter("max_pullback_days", "number", 10, "最长回调天数", min=5, max=20),
            StrategyParameter(
                "vol_shrink_ratio", "number", 0.30,
                "缩量比例（回调量/峰值量）", min=0.15, max=0.50, step=0.05,
            ),
            StrategyParameter(
                "vol_recovery_mult", "number", 1.5,
                "放量回升倍数（vs回调均量）", min=1.2, max=3.0, step=0.1,
            ),
            StrategyParameter(
                "stop_loss_pct", "number", 0.05,
                "止损比例", min=0.02, max=0.10, step=0.01,
            ),
            StrategyParameter(
                "take_profit_pct", "number", 0.15,
                "止盈减仓阈值", min=0.08, max=0.30, step=0.01,
            ),
            StrategyParameter("max_hold_days", "number", 8, "最长持仓天数", min=3, max=20),
        ],
    )

    params = (
        ("lookback", 20),
        ("min_boards", 3),
        ("min_first_wave_gain", 0.30),
        ("max_retrace", 0.382),
        ("min_pullback_days", 3),
        ("max_pullback_days", 10),
        ("vol_shrink_ratio", 0.30),
        ("vol_recovery_mult", 1.5),
        ("stop_loss_pct", 0.05),
        ("take_profit_pct", 0.15),
        ("max_hold_days", 8),
    )

    def __init__(self):
        super().__init__()

        # Moving averages for support detection
        self.ma5 = bt.indicators.SMA(self.data.close, period=5)
        self.ma10 = bt.indicators.SMA(self.data.close, period=10)
        self.ma20 = bt.indicators.SMA(self.data.close, period=20)
        self.vol_sma20 = bt.indicators.SMA(self.data.volume, period=20)

        # Rolling K-line history for dragon gene / wave detection
        self._kline_history: list[dict] = []
        self._MAX_HISTORY = 60

        # Position state
        self._entry_price = 0.0
        self._entry_bar = -1
        self._half_sold = False
        self._prev_close = 0.0
        self._last_date = None

    # ══════════════════════════════════════════════════════════════
    #  Core Logic
    # ══════════════════════════════════════════════════════════════

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

        if not self.position:
            self._try_entry(close, volume)
        else:
            # T+1 guard
            if len(self) <= self._entry_bar:
                return
            self._try_exit(close)

    # ══════════════════════════════════════════════════════════════
    #  Entry: Dragon Return Pattern Detection
    # ══════════════════════════════════════════════════════════════

    def _try_entry(self, close: float, volume: float):
        klines = self._kline_history
        lookback = self.p.lookback

        if len(klines) < lookback + 2:
            return

        # ── Step 1: Find the first wave (rally phase) ──
        # Current bar is the last in kline_history; pullback = bars between
        # the peak and the current bar (exclusive of current).
        wave = self._detect_first_wave(klines, lookback)
        if wave is None:
            return

        wave_base, wave_peak, wave_gain, board_count, peak_idx = wave

        # ── Step 2: Check dragon gene ──
        if board_count < self.p.min_boards:
            return
        if wave_gain < self.p.min_first_wave_gain:
            return

        # ── Step 3: Check pullback structure ──
        # Pullback = bars between peak and current bar (exclude current bar)
        pullback_klines = klines[peak_idx + 1:-1]
        pullback = self._check_pullback(
            pullback_klines, wave_base, wave_peak, wave_gain,
            klines, peak_idx,
        )
        if pullback is None:
            return

        pullback_days, pullback_low, retrace_ratio, vol_shrunk = pullback

        if pullback_days < self.p.min_pullback_days:
            return
        if pullback_days > self.p.max_pullback_days:
            return
        if retrace_ratio > self.p.max_retrace:
            return
        if not vol_shrunk:
            return

        # ── Step 4: Check support confluence + volume recovery ──
        support_score = self._check_support(close, wave_base, wave_peak)
        if support_score < 1:
            return

        # Volume recovery: today's volume vs pullback average
        if not pullback_klines:
            return
        avg_pb_vol = sum(k["volume"] for k in pullback_klines) / len(pullback_klines)
        if avg_pb_vol <= 0:
            return
        vol_recovery = volume / avg_pb_vol
        if vol_recovery < self.p.vol_recovery_mult:
            return

        # ── All conditions met → BUY ──
        size = self.calc_position_size_pct(close, 0.90)
        if size <= 0:
            return

        self.buy(size=size)
        self._entry_price = close
        self._entry_bar = len(self)
        self._half_sold = False

        self.record_signal(
            "buy", close,
            f"龙回头买入: 首波涨幅{wave_gain*100:.1f}%, {board_count}板, "
            f"回调{pullback_days}天/{retrace_ratio*100:.1f}%, "
            f"支撑{support_score}重, 量比{vol_recovery:.1f}x",
        )

    def _detect_first_wave(
        self, klines: list[dict], lookback: int
    ) -> tuple[float, float, float, int, int] | None:
        """Detect the first rally wave within the lookback window.

        Returns (wave_base, wave_peak, wave_gain, board_count, peak_idx)
        or None if no qualifying wave found.
        """
        # Work on the recent `lookback` bars
        window = klines[-lookback:]
        full_len = len(klines)
        window_start = full_len - lookback

        # Find the highest close in the window → that's the wave peak
        peak_close = 0.0
        peak_local_idx = 0
        for i, k in enumerate(window):
            if k["close"] > peak_close:
                peak_close = k["close"]
                peak_local_idx = i

        if peak_close <= 0:
            return None

        # The peak must not be too close to the end of window.
        # After peak we need: min_pullback_days of pullback + the current bar (last in window)
        if peak_local_idx >= len(window) - self.p.min_pullback_days - 1:
            return None

        # Find wave base: lowest close before the peak
        wave_base = peak_close
        for i in range(0, peak_local_idx):
            if window[i]["close"] < wave_base:
                wave_base = window[i]["close"]

        if wave_base <= 0:
            return None

        wave_gain = (peak_close - wave_base) / wave_base

        # Count limit-up boards in the rally phase (base to peak), excluding 一字板
        board_count = 0
        for i in range(1, peak_local_idx + 1):
            curr = window[i]
            prev_c = window[i - 1]["close"]
            if self._is_limit_up(curr["close"], prev_c):
                # Exclude 一字板 (open == close == high == low)
                if curr["open"] == curr["close"] == curr["high"] == curr["low"]:
                    continue
                board_count += 1

        peak_idx = window_start + peak_local_idx
        return (wave_base, peak_close, wave_gain, board_count, peak_idx)

    def _check_pullback(
        self,
        pb_klines: list[dict],
        wave_base: float,
        wave_peak: float,
        wave_gain: float,
        all_klines: list[dict],
        peak_idx: int,
    ) -> tuple[int, float, float, bool] | None:
        """Check if the bars between peak and current bar form a valid pullback.

        Args:
            pb_klines: bars between peak and current bar (exclusive of both)
            wave_base: price at start of rally
            wave_peak: price at rally peak
            wave_gain: (wave_peak - wave_base) / wave_base
            all_klines: full kline history (for rally volume lookup)
            peak_idx: index of peak in all_klines

        Returns (pullback_days, pullback_low, retrace_ratio, vol_shrunk)
        or None if insufficient data.
        """
        if not pb_klines:
            return None

        pullback_days = len(pb_klines)

        # Find lowest close during pullback
        pullback_low = min(k["close"] for k in pb_klines)

        # Retrace ratio = how much of the first wave was given back
        wave_rise = wave_peak - wave_base
        if wave_rise <= 0:
            return None
        retrace_ratio = (wave_peak - pullback_low) / wave_rise

        # Volume shrinkage check: avg pullback volume vs peak rally volume
        rally_start = max(0, peak_idx - 20)
        rally_klines = all_klines[rally_start:peak_idx + 1]
        if not rally_klines:
            return None
        peak_volume = max(k["volume"] for k in rally_klines)
        if peak_volume <= 0:
            return None

        avg_pullback_vol = sum(k["volume"] for k in pb_klines) / len(pb_klines)
        vol_shrunk = (avg_pullback_vol / peak_volume) <= self.p.vol_shrink_ratio

        return (pullback_days, pullback_low, retrace_ratio, vol_shrunk)

    def _check_support(self, close: float, wave_base: float, wave_peak: float) -> int:
        """Count how many support factors are present (0-4).

        Supports checked:
          1. Near MA support (MA5/MA10/MA20 within 2%)
          2. Near Fibonacci 0.382 retracement
          3. Near Fibonacci 0.5 retracement
          4. Near Fibonacci 0.618 retracement
        """
        score = 0
        tolerance = 0.02  # 2% proximity

        # 1. MA support — price near or just above one of MA5/MA10/MA20
        for ma in (self.ma5[0], self.ma10[0], self.ma20[0]):
            if ma > 0:
                deviation = (close - ma) / ma
                if -tolerance <= deviation <= tolerance * 2:
                    score += 1
                    break  # count MA support as one factor

        # Fibonacci levels from wave peak to wave base
        wave_range = wave_peak - wave_base
        if wave_range > 0:
            fib_382 = wave_peak - wave_range * 0.382
            fib_500 = wave_peak - wave_range * 0.500
            fib_618 = wave_peak - wave_range * 0.618

            # 2. Near Fib 0.382
            if fib_382 > 0 and abs(close - fib_382) / fib_382 <= tolerance:
                score += 1

            # 3. Near Fib 0.5
            if fib_500 > 0 and abs(close - fib_500) / fib_500 <= tolerance:
                score += 1

            # 4. Near Fib 0.618
            if fib_618 > 0 and abs(close - fib_618) / fib_618 <= tolerance:
                score += 1

        return score

    # ══════════════════════════════════════════════════════════════
    #  Exit Logic
    # ══════════════════════════════════════════════════════════════

    def _try_exit(self, close: float):
        hold_days = self._holding_days()

        # ── Exit 1: Stop loss ──
        if self._entry_price > 0:
            loss_pct = (close - self._entry_price) / self._entry_price
            if loss_pct < -self.p.stop_loss_pct:
                self.close()
                self._reset_state()
                self.record_signal(
                    "sell", close,
                    f"止损: 亏损{loss_pct*100:.1f}% > {self.p.stop_loss_pct*100:.0f}%",
                )
                return

        # ── Exit 2: Time stop ──
        if hold_days >= self.p.max_hold_days:
            self.close()
            self._reset_state()
            self.record_signal(
                "sell", close,
                f"时间止损: 持仓{hold_days}天 >= {self.p.max_hold_days}天",
            )
            return

        # ── Exit 3: Second limit-up → hold (let winners ride) ──
        if self._prev_close > 0 and self._is_limit_up(close, self._prev_close):
            self.record_signal(
                "hold", close,
                f"二次涨停! 继续持有",
            )
            return

        # ── Exit 4: Take profit — sell half ──
        if not self._half_sold and self._entry_price > 0:
            profit_pct = (close - self._entry_price) / self._entry_price
            if profit_pct >= self.p.take_profit_pct:
                half = self.position.size // 2
                # Round to lot of 100
                if half >= 100:
                    half = (half // 100) * 100
                if half > 0:
                    self.sell(size=half)
                    self._half_sold = True
                    self.record_signal(
                        "sell", close,
                        f"止盈减半仓: 盈利{profit_pct*100:.1f}%",
                    )
                    return

        # ── Exit 5: Trailing stop below MA5 after half-sell ──
        if self._half_sold and close < self.ma5[0]:
            self.close()
            self._reset_state()
            self.record_signal(
                "sell", close,
                f"跌破MA5({self.ma5[0]:.2f})清余仓",
            )

    # ══════════════════════════════════════════════════════════════
    #  Helpers
    # ══════════════════════════════════════════════════════════════

    def _holding_days(self) -> int:
        if self._entry_bar < 0:
            return 0
        return len(self) - self._entry_bar

    def _reset_state(self):
        self._entry_price = 0.0
        self._entry_bar = -1
        self._half_sold = False

    def _is_limit_up(self, close: float, prev_close: float) -> bool:
        """Check if close reached limit-up threshold."""
        if prev_close <= 0 or close <= 0:
            return False
        symbol = self.data._name if hasattr(self.data, "_name") else ""
        ratio = self._get_limit_ratio(symbol)
        # Allow 0.3% tolerance for rounding
        return close / prev_close >= 1 + ratio - 0.003

    def _get_limit_ratio(self, symbol: str) -> float:
        """Determine limit ratio by symbol prefix."""
        code = str(symbol).split(".")[0] if symbol else ""
        if code.startswith("688"):
            return 0.20  # 科创板
        if code.startswith("30"):
            return 0.20  # 创业板
        if code.startswith("4") or (code.startswith("8") and not code.startswith("688")):
            return 0.30  # 北交所
        return 0.10  # 主板
