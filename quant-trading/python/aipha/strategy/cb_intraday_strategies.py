"""CB 日内 T+0 具体策略实现。

包含两个日内策略：
1. CBVWAPDeviation: VWAP 偏离均值回归
   - 价格低于 VWAP 一定阈值时买入，回归时卖出
2. CBOvernightGapDecay: 隔夜跳空衰减
   - 大幅跳高/跳低开盘后的日内反转获利

两个策略均继承 CBIntradayBase，实现 on_minute_bar()。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aipha.data.stream import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_intraday_base import (
    CBIntradayBase,
    CBIntradayConfig,
    IntradaySignal,
    IntradayState,
)

logger = logging.getLogger(__name__)


# ==================================================================
# 策略 1：VWAP 偏离均值回归
# ==================================================================

@dataclass
class VWAPDeviationConfig(CBIntradayConfig):
    """VWAP 偏离策略配置。

    Attributes:
        entry_threshold: 低于 VWAP 多少比例时买入
        exit_threshold: 回归到 VWAP 附近比例时卖出
        min_volume_bars: 至少累积多少根bar后才交易
        max_deviation: 偏离过大不交易（避免追断崖）
    """
    entry_threshold: float = -0.008   # -0.8%
    exit_threshold: float = -0.001    # -0.1% (接近VWAP)
    min_volume_bars: int = 30         # 前30根bar不交易
    max_deviation: float = -0.03      # -3% 不交易


class CBVWAPDeviation(CBIntradayBase):
    """VWAP 偏离均值回归策略。

    经济逻辑：
    - CB 日内价格围绕 VWAP 波动
    - 价格显著低于 VWAP 时，大概率向 VWAP 回归
    - T+0 允许日内买卖，捕捉回归收益

    信号规则：
    - BUY: deviation < entry_threshold（低于VWAP 0.8%+）
    - SELL: deviation > exit_threshold（回归到VWAP附近）
    - HOLD: 其他情况
    """

    name = "cb_vwap_deviation"

    def __init__(self, config: VWAPDeviationConfig | None = None) -> None:
        super().__init__(config or VWAPDeviationConfig())
        self._vwap_config = self.config  # type: VWAPDeviationConfig

    def on_minute_bar(
        self,
        bar: Bar,
        state: IntradayState,
        context: StrategyContext,
    ) -> IntradaySignal:
        """VWAP 偏离信号：低买高卖。"""
        cfg: VWAPDeviationConfig = self._vwap_config  # type: ignore

        # 累积bar不足，不交易
        if state.cumulative_volume < cfg.min_volume_bars:
            return IntradaySignal.HOLD

        if state.vwap <= 0:
            return IntradaySignal.HOLD

        deviation = (bar.close - state.vwap) / state.vwap

        # 偏离过大，可能断崖，不交易
        if deviation < cfg.max_deviation:
            return IntradaySignal.HOLD

        # 无持仓时寻找买入机会
        if state.qty <= 0 and deviation < cfg.entry_threshold:
            return IntradaySignal.BUY

        # 有持仓时寻找卖出（回归）
        if state.qty > 0 and deviation > cfg.exit_threshold:
            return IntradaySignal.SELL

        return IntradaySignal.HOLD


# ==================================================================
# 策略 2：隔夜跳空衰减
# ==================================================================

@dataclass
class OvernightGapConfig(CBIntradayConfig):
    """隔夜跳空衰减策略配置。

    Attributes:
        gap_threshold: 跳空幅度阈值（绝对值）
        decay_target_pct: 预期回填比例
        entry_delay_bars: 开盘后等待几根bar再入场
        stop_loss_pct: 止损比例
    """
    gap_threshold: float = 0.015    # 1.5% 跳空
    decay_target_pct: float = 0.5   # 预期回填50%
    entry_delay_bars: int = 5       # 开盘5根bar后入场
    stop_loss_pct: float = 0.01     # 1% 止损


class CBOvernightGapDecay(CBIntradayBase):
    """隔夜跳空衰减策略。

    经济逻辑：
    - CB 隔夜大幅跳高/跳低后，日内往往有回填动作
    - 跳高开盘→做空方向（卖出已有持仓或不参与）
    - 跳低开盘→做多方向（买入等待回填）
    - T+0 日内完成，无隔夜风险

    信号规则：
    - 跳低缺口 > gap_threshold: 延迟entry_delay_bars后BUY
    - 回填 >= decay_target_pct: SELL
    - 触发止损: SELL (FLAT)
    """

    name = "cb_overnight_gap_decay"

    def __init__(self, config: OvernightGapConfig | None = None) -> None:
        super().__init__(config or OvernightGapConfig())
        self._gap_config = self.config  # type: OvernightGapConfig
        # 记录每个 symbol 的前日收盘和开盘信息
        self._prev_close: dict[str, float] = {}
        self._open_price: dict[str, float] = {}
        self._bar_count_today: dict[str, int] = {}

    def on_start(self) -> None:
        """初始化。"""
        self._prev_close.clear()
        self._open_price.clear()
        self._bar_count_today.clear()

    def on_minute_bar(
        self,
        bar: Bar,
        state: IntradayState,
        context: StrategyContext,
    ) -> IntradaySignal:
        """跳空衰减信号。"""
        cfg: OvernightGapConfig = self._gap_config  # type: ignore
        symbol = bar.symbol

        # 计数今日 bar
        self._bar_count_today[symbol] = self._bar_count_today.get(symbol, 0) + 1
        bar_idx = self._bar_count_today[symbol]

        # 记录开盘价（首根bar）
        if bar_idx == 1:
            self._open_price[symbol] = bar.open

        prev_close = self._prev_close.get(symbol, 0.0)
        open_price = self._open_price.get(symbol, 0.0)

        # 无前日收盘，无法判断缺口
        if prev_close <= 0 or open_price <= 0:
            return IntradaySignal.HOLD

        gap = (open_price - prev_close) / prev_close

        # 只处理跳低缺口（买入方向）
        if gap > -cfg.gap_threshold:
            return IntradaySignal.HOLD

        # 等待 entry_delay
        if bar_idx < cfg.entry_delay_bars:
            return IntradaySignal.HOLD

        # 无持仓：入场
        if state.qty <= 0 and bar_idx == cfg.entry_delay_bars:
            return IntradaySignal.BUY

        # 有持仓：检查回填或止损
        if state.qty > 0:
            fill_pct = (bar.close - open_price) / abs(gap * prev_close)
            if fill_pct >= cfg.decay_target_pct:
                return IntradaySignal.SELL
            # 止损
            if state.entry_price > 0:
                loss = (bar.close - state.entry_price) / state.entry_price
                if loss < -cfg.stop_loss_pct:
                    return IntradaySignal.FLAT

        return IntradaySignal.HOLD

    def _reset_daily(self, now) -> None:
        """日切时保存前日收盘、清空bar计数。"""
        # 从 state 中提取前日收盘
        for sym, st in self._states.items():
            if st.high_of_day > 0:
                # 用最后状态的 vwap 近似收盘
                self._prev_close[sym] = st.vwap if st.vwap > 0 else st.high_of_day
        self._bar_count_today.clear()
        self._open_price.clear()
        super()._reset_daily(now)