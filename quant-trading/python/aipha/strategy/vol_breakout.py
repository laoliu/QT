"""可转债波动率压缩突破策略

Alpha 来源：
1. 波动率压缩（ATR 收窄）通常预示大行情即将到来
2. 可转债有面值保底，下行受限 → 突破更大概率是向上
3. 配合成交量放大确认突破有效性

策略逻辑：
1. 计算每只转债的 ATR（N 日平均真实波幅）
2. 计算短期 ATR / 长期 ATR 的比值（压缩度）
3. 当价格突破近期高点 + 压缩度低（波动率在收窄后展开）→ 买入信号
4. 可选：配合成交量放大作为确认
5. 持有 top_k 只

依赖字段：close, high, low（Bar 自带但现有策略未使用）
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


@dataclass
class VolBreakoutConfig(CBRotationConfig):
    """波动率压缩突破配置"""

    top_k: int = 10
    rebalance_days: int = 3

    # ATR 参数
    atr_short: int = 5      # 短期 ATR 窗口
    atr_long: int = 20      # 长期 ATR 窗口
    squeeze_threshold: float = 0.6  # 短/长 ATR 比值低于此 = 压缩

    # 突破确认
    breakout_lookback: int = 10  # 突破参考的高点回看天数

    # 筛选
    max_price: float = 135.0
    stop_loss_pct: float = 0.10


@StrategyRegistry().register("vol_breakout")
class VolBreakoutStrategy(CBRotationStrategy):
    """可转债波动率压缩突破策略

    在波动率收窄后突破高点时买入。
    利用 CB 面值锚定使突破概率向上偏。
    """

    name = "vol_breakout"

    def __init__(self, config: VolBreakoutConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or VolBreakoutConfig(**kwargs)
        self._hlc_history: dict[str, deque[tuple[float, float, float]]] = {}

    @property
    def config(self) -> VolBreakoutConfig:
        """策略配置。"""
        return self._config

    def _on_start_extra(self) -> None:
        self._hlc_history.clear()

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        max_len = self._config.atr_long + 5
        for symbol, bar in bars.items():
            if bar.close > 0 and bar.high > 0 and bar.low > 0:
                if symbol not in self._hlc_history:
                    self._hlc_history[symbol] = deque(maxlen=max_len)
                self._hlc_history[symbol].append((bar.high, bar.low, bar.close))

    def _should_rebalance(self) -> bool:
        cfg = self._config
        if self._bar_count < cfg.atr_long + 5:
            return False
        return (self._bar_count - 1) % cfg.rebalance_days == 0

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """需要足够历史 + 波动率压缩 + 价格突破近期高点。"""
        cfg = self._config
        hist = self._hlc_history.get(candidate.symbol)
        if hist is None or len(hist) < cfg.atr_long + 1:
            return False

        data = list(hist)
        atr_short = self._calc_atr(data, cfg.atr_short)
        atr_long = self._calc_atr(data, cfg.atr_long)
        if atr_long <= 0 or atr_short <= 0:
            return False

        squeeze_ratio = atr_short / atr_long
        if squeeze_ratio >= cfg.squeeze_threshold:
            return False

        recent_highs = [h for h, _, _ in data[-cfg.breakout_lookback:-1]]
        if not recent_highs:
            return False
        return candidate.close > max(recent_highs)

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按突破信号强度降序，取 top_k。"""
        cfg = self._config
        scored: list[tuple[str, float]] = []
        for c in candidates:
            data = list(self._hlc_history[c.symbol])
            atr_short = self._calc_atr(data, cfg.atr_short)
            recent_highs = [h for h, _, _ in data[-cfg.breakout_lookback:-1]]
            recent_high = max(recent_highs) if recent_highs else c.close
            strength = (c.close - recent_high) / atr_short if atr_short > 0 else 0
            scored.append((c.symbol, strength))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[: cfg.top_k]]

    @staticmethod
    def _calc_atr(data: list[tuple[float, float, float]], period: int) -> float:
        """计算 ATR（平均真实波幅）"""
        if len(data) < period + 1:
            return 0.0
        trs = []
        for i in range(-period, 0):
            h, l, c = data[i]
            prev_c = data[i - 1][2]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
        return sum(trs) / len(trs) if trs else 0.0
