"""支柱级绩效监控与自动降权

实现策略支柱（如 CB 支柱）的滚动绩效监控，
当滚动 Sharpe 持续低于阈值时触发自动降权。

外部方案借鉴：
- 12 个月滚动 SR < 0 连续 6 个月 → 降权 30%
- McLean & Pontiff (2016) alpha 衰减检验原理

用于组合层面的支柱权重动态调整。
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from aipha.utils.metrics import compute_sharpe_ratio

logger = logging.getLogger(__name__)


@dataclass
class DownweightRule:
    """单条降权规则。"""

    sr_threshold: float = 0.0
    consecutive_months: int = 6
    weight_reduction: float = 0.30
    recovery_months: int = 3
    rolling_window_days: int = 252


@dataclass
class PillarMonitorState:
    """支柱监控状态。"""

    current_scale: float = 1.0
    rolling_sr: float = 0.0
    months_below_threshold: int = 0
    is_downweighted: bool = False
    recovery_counter: int = 0

    def to_dict(self) -> dict:
        return {
            "current_scale": round(self.current_scale, 4),
            "rolling_sr": round(self.rolling_sr, 4),
            "months_below": self.months_below_threshold,
            "is_downweighted": self.is_downweighted,
        }


class PillarDownweightMonitor:
    """支柱级绩效监控器。

    用法::

        monitor = PillarDownweightMonitor(
            rule=DownweightRule(
                sr_threshold=0.0,
                consecutive_months=6,
                weight_reduction=0.30,
            ),
        )

        for daily_return in returns:
            monitor.update(daily_return)
            scale = monitor.get_scale()
            pillar_weight = base_weight * scale
    """

    def __init__(
        self,
        rule: DownweightRule | None = None,
        rebalance_interval: int = 21,
    ) -> None:
        self._rule = rule or DownweightRule()
        self._interval = rebalance_interval
        self._returns: deque[float] = deque(
            maxlen=self._rule.rolling_window_days + 10,
        )
        self._state = PillarMonitorState()
        self._bar_count = 0

    @property
    def state(self) -> PillarMonitorState:
        return self._state

    def get_scale(self) -> float:
        return self._state.current_scale

    def update(self, daily_return: float) -> float:
        """更新日收益率，在再平衡日检查降权条件。

        Returns:
            当前权重缩放因子 (0, 1.0]
        """
        self._returns.append(daily_return)
        self._bar_count += 1

        if self._bar_count % self._interval != 0:
            return self._state.current_scale

        if len(self._returns) < self._rule.rolling_window_days // 2:
            return 1.0

        sr = self._compute_rolling_sr()
        self._state.rolling_sr = sr
        self._check_downweight(sr)
        return self._state.current_scale

    def _compute_rolling_sr(self) -> float:
        """计算滚动 Sharpe Ratio。"""
        window = self._rule.rolling_window_days
        arr = np.array(list(self._returns))
        recent = arr[-window:]
        if len(recent) < 20:
            return 0.0
        sr = compute_sharpe_ratio(recent)
        # 极低波动特殊信号：零波动方向信号（保留原行为）
        if sr == 0.0 and len(recent) >= 2:
            mu = float(np.mean(recent))
            if abs(mu) > 1e-15:
                return math.copysign(10.0, mu)
        return sr

    def _check_downweight(self, sr: float) -> None:
        """检查是否触发降权或恢复。"""
        rule = self._rule
        st = self._state

        if sr < rule.sr_threshold:
            st.months_below_threshold += 1
            st.recovery_counter = 0
        else:
            if st.is_downweighted:
                st.recovery_counter += 1
                if st.recovery_counter >= rule.recovery_months:
                    st.is_downweighted = False
                    st.months_below_threshold = 0
                    st.current_scale = 1.0
                    logger.info(
                        "支柱降权恢复: SR=%.2f, 连续恢复 %d 月",
                        sr, st.recovery_counter,
                    )
            else:
                st.months_below_threshold = max(
                    0, st.months_below_threshold - 1,
                )

        if (
            not st.is_downweighted
            and st.months_below_threshold >= rule.consecutive_months
        ):
            st.is_downweighted = True
            st.current_scale = 1.0 - rule.weight_reduction
            logger.warning(
                "支柱降权触发: SR=%.2f, 连续 %d 月 < %.2f, "
                "权重缩放至 %.1f%%",
                sr, st.months_below_threshold,
                rule.sr_threshold,
                st.current_scale * 100,
            )

    def reset(self) -> None:
        """重置监控器状态。"""
        self._returns.clear()
        self._state = PillarMonitorState()
        self._bar_count = 0
