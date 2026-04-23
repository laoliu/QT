"""
Swing Overlay Strategy (持仓波段套利策略).

Category: Range / Hybrid (Grid + Mean-Reversion)
Markets: A-shares (T+1 aware)

A hybrid strategy designed to extract swing profits from oscillating
uptrending stocks while maintaining a core position.

Core concept:
  - Split holdings into CORE (untouched) and SWING portions
  - SWING portion executes buy-low/sell-high within a dynamic grid
  - Grid center follows MA20 (adapts to uptrend, doesn't fall behind)
  - Entry (低吸): BIAS < threshold OR price at grid-down level + volume spike
  - Exit (高抛): BIAS > threshold OR price at grid-up level
  - T+1 aware: cannot sell shares bought today

Parameters:
  - core_ratio:      fraction of position to keep untouched (default 0.6)
  - grid_pct:        grid spacing as percentage (default 3.0%)
  - grid_count:      grids on each side of center (default 3)
  - bias_buy:        BIAS threshold for swing buy (default -5.0%)
  - bias_sell:       BIAS threshold for swing sell (default 3.5%)
  - vol_mult:        volume spike multiplier for buy confirmation (default 1.5)
  - max_hold_days:   max days to hold a swing position (default 8)
  - ma_period:       center MA period (default 20)
"""

from __future__ import annotations

import sys

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class SwingOverlayStrategy(BaseStrategy):
    """Swing overlay: grid + mean-reversion hybrid for A-share holdings."""

    STRATEGY_META = StrategyMeta(
        id="swing-overlay",
        name="持仓波段套利 (Swing Overlay)",
        description=(
            "持仓波段套利策略：保留核心仓位不动，用波段仓位在动态网格内高抛低吸。"
            "网格中心跟随MA20适应上涨趋势，结合乖离率(BIAS)和成交量确认信号。"
            "适用于整体上涨但有规律振荡的持仓股票。"
            "自动适配A股T+1交割制度。"
        ),
        category="range",
        markets=["cn"],
        parameters=[
            StrategyParameter(
                "core_ratio", "number", 0.6,
                "核心仓位比例（不参与波段）", min=0.3, max=0.9, step=0.05,
            ),
            StrategyParameter(
                "grid_pct", "number", 3.0,
                "网格间距（%）", min=1.5, max=8.0, step=0.5,
            ),
            StrategyParameter(
                "grid_count", "number", 3,
                "单侧网格数", min=1, max=6,
            ),
            StrategyParameter(
                "bias_buy", "number", -5.0,
                "买入乖离率阈值（%）", min=-12.0, max=-2.0, step=0.5,
            ),
            StrategyParameter(
                "bias_sell", "number", 3.5,
                "卖出乖离率阈值（%）", min=1.0, max=8.0, step=0.5,
            ),
            StrategyParameter(
                "vol_mult", "number", 1.5,
                "放量确认倍数", min=1.0, max=4.0, step=0.1,
            ),
            StrategyParameter(
                "max_hold_days", "number", 8,
                "波段最大持仓天数", min=2, max=20,
            ),
            StrategyParameter(
                "ma_period", "number", 20,
                "中心均线周期", min=5, max=60,
            ),
        ],
    )

    params = (
        ("core_ratio", 0.6),
        ("grid_pct", 3.0),
        ("grid_count", 3),
        ("bias_buy", -5.0),
        ("bias_sell", 3.5),
        ("vol_mult", 1.5),
        ("max_hold_days", 8),
        ("ma_period", 20),
    )

    def __init__(self):
        super().__init__()
        # Indicators
        self.sma_center = bt.indicators.SMA(self.data.close, period=self.p.ma_period)
        self.vol_sma = bt.indicators.SMA(self.data.volume, period=20)

        # State tracking
        self._initial_shares: int = 0       # total shares at start
        self._core_shares: int = 0          # core position (untouched)
        self._swing_shares: int = 0         # current swing holding
        self._swing_target_per_grid: int = 0  # shares per grid level
        self._grid_levels: list[float] = []
        self._filled_grids: dict[int, int] = {}  # grid_index → shares bought
        self._swing_entry_bar: int = -1     # for T+1 tracking
        self._swing_entry_price: float = 0.0
        self._initialized = False
        self._round_trips: int = 0          # completed swing round-trips

    def _bias(self) -> float:
        """Current BIAS (乖离率) in percent."""
        ma = self.sma_center[0]
        if ma <= 0:
            return 0.0
        return (self.data.close[0] - ma) / ma * 100.0

    def _is_volume_spike(self) -> bool:
        """Check if current volume exceeds threshold."""
        avg = self.vol_sma[0]
        if avg <= 0:
            return False
        return self.data.volume[0] >= avg * self.p.vol_mult

    def _build_grids(self, center: float) -> list[float]:
        """Build symmetric grid levels around center price."""
        pct = self.p.grid_pct / 100.0
        levels: list[float] = []
        for i in range(-self.p.grid_count, self.p.grid_count + 1):
            level = center * (1 + i * pct)
            if level > 0:
                levels.append(level)
        return sorted(levels)

    def _find_grid_index(self, price: float) -> int:
        """Find which grid level the price is nearest to (below)."""
        for i in range(len(self._grid_levels) - 1, -1, -1):
            if price >= self._grid_levels[i]:
                return i
        return 0

    def _swing_holding_days(self) -> int:
        """Days since last swing entry."""
        if self._swing_entry_bar < 0:
            return 0
        return len(self) - self._swing_entry_bar

    def next(self):
        close = self.data.close[0]
        ma_center = self.sma_center[0]

        # Skip until MA is ready
        if ma_center <= 0 or close <= 0:
            return

        # Initialize on first valid bar — simulate holding full position
        if not self._initialized:
            # Calculate position as if we already hold the stock
            equity = self.broker.getvalue()
            total_shares = int(equity * 0.95 / close / 100) * 100  # lot size = 100
            if total_shares <= 0:
                return

            self._initial_shares = total_shares
            self._core_shares = int(total_shares * self.p.core_ratio / 100) * 100
            swing_total = total_shares - self._core_shares
            self._swing_target_per_grid = max(
                100, int(swing_total / max(1, self.p.grid_count) / 100) * 100
            )

            # Buy the full initial position
            self.buy(size=total_shares)
            self._swing_shares = swing_total
            self._initialized = True
            self.record_signal(
                "buy", close,
                f"初始建仓: 总{total_shares}股 (核心{self._core_shares}+波段{swing_total}), "
                f"每格{self._swing_target_per_grid}股",
            )
            return

        # Update dynamic grid center (follows MA)
        self._grid_levels = self._build_grids(ma_center)
        bias = self._bias()
        vol_spike = self._is_volume_spike()
        grid_idx = self._find_grid_index(close)
        center_idx = len(self._grid_levels) // 2

        # ── SELL (高抛) — sell swing shares when price is high ──
        can_sell = self._swing_entry_bar < 0 or len(self) > self._swing_entry_bar  # T+1
        swing_held = self._swing_shares

        if can_sell and swing_held > 0:
            sell_signal = False
            sell_reason = ""

            # Condition 1: BIAS overbought
            if bias >= self.p.bias_sell:
                sell_signal = True
                sell_reason = f"BIAS高抛: BIAS={bias:.2f}% ≥ {self.p.bias_sell}%"

            # Condition 2: Price at or above upper grid level
            elif grid_idx >= center_idx + 1:
                sell_signal = True
                grid_price = self._grid_levels[grid_idx] if grid_idx < len(self._grid_levels) else close
                sell_reason = f"网格高抛: 触及第{grid_idx - center_idx}层上网格 ¥{grid_price:.2f}"

            # Condition 3: Max hold days exceeded
            elif self._swing_holding_days() >= self.p.max_hold_days and self._swing_entry_bar > 0:
                sell_signal = True
                sell_reason = f"持仓超时: 持有{self._swing_holding_days()}天 ≥ {self.p.max_hold_days}天"

            if sell_signal:
                sell_qty = min(swing_held, self._swing_target_per_grid)
                sell_qty = (sell_qty // 100) * 100
                if sell_qty >= 100:
                    self.sell(size=sell_qty)
                    self._swing_shares -= sell_qty
                    pnl = (close - self._swing_entry_price) / self._swing_entry_price * 100 if self._swing_entry_price > 0 else 0
                    self._round_trips += 1
                    self.record_signal(
                        "sell", close,
                        f"[波段高抛] {sell_reason}, 卖出{sell_qty}股, "
                        f"波段损益{pnl:+.1f}%, 累计往返{self._round_trips}次",
                    )
                    if self._swing_shares <= 0:
                        self._swing_entry_bar = -1
                        self._swing_entry_price = 0.0

        # ── BUY (低吸) — buy swing shares when price is low ──
        swing_capacity = (self._initial_shares - self._core_shares) - self._swing_shares
        if swing_capacity >= 100:
            buy_signal = False
            buy_reason = ""

            # Condition 1: BIAS oversold + volume confirmation
            if bias <= self.p.bias_buy and vol_spike:
                buy_signal = True
                vol_ratio = self.data.volume[0] / self.vol_sma[0] if self.vol_sma[0] > 0 else 0
                buy_reason = f"BIAS低吸: BIAS={bias:.2f}% ≤ {self.p.bias_buy}%, 量比={vol_ratio:.1f}x"

            # Condition 2: Price at or below lower grid level
            elif grid_idx <= center_idx - 1:
                grid_price = self._grid_levels[grid_idx] if grid_idx < len(self._grid_levels) else close
                # Require at least mild volume uptick for grid entry
                if self.data.volume[0] > self.vol_sma[0] * 0.8:
                    buy_signal = True
                    buy_reason = f"网格低吸: 触及第{center_idx - grid_idx}层下网格 ¥{grid_price:.2f}"

            if buy_signal:
                buy_qty = min(swing_capacity, self._swing_target_per_grid)
                buy_qty = (buy_qty // 100) * 100
                if buy_qty >= 100:
                    # Check if we have enough cash
                    cash = self.broker.getcash()
                    cost = buy_qty * close * 1.001  # include fees
                    if cash >= cost:
                        self.buy(size=buy_qty)
                        self._swing_shares += buy_qty
                        self._swing_entry_bar = len(self)
                        self._swing_entry_price = close
                        self.record_signal(
                            "buy", close,
                            f"[波段低吸] {buy_reason}, 买入{buy_qty}股, "
                            f"波段持仓{self._swing_shares}/{self._initial_shares - self._core_shares}股",
                        )
