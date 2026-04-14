"""动态仓位管理 — 波动率目标叠加 + 回撤减仓"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)


# ── 波动率目标叠加 ──────────────────────────────────────────────────

@dataclass
class VolatilityTargetConfig:
    target_vol: float = 0.15          # 年化目标波动率
    lookback: int = 60                # 回看窗口
    half_life: int = 20               # 指数加权半衰期
    max_leverage: float = 1.5         # 最大杠杆
    min_exposure: float = 0.20        # 最小暴露
    annualize: float = 252.0
    smooth_days: int = 5              # 缩放平滑（避免频繁跳变）


class VolatilityTargetOverlay:
    """波动率目标仓位叠加器。

    使用指数加权波动率估计，将仓位缩放到目标波动率水平。
    """

    def __init__(self, config: VolatilityTargetConfig | None = None) -> None:
        self._cfg = config or VolatilityTargetConfig()
        self._returns: list[float] = []
        self._scale_history: list[float] = []

    def add_return(self, daily_return: float) -> None:
        self._returns.append(daily_return)

    def get_scale(self) -> float:
        cfg = self._cfg
        if len(self._returns) < cfg.lookback:
            return 1.0

        # 指数加权波动率
        rets = np.array(self._returns[-cfg.lookback:])
        alpha = 1 - np.exp(-np.log(2) / cfg.half_life)
        weights = np.array([(1 - alpha) ** i for i in range(len(rets) - 1, -1, -1)])
        weights /= weights.sum()
        weighted_mean = float(np.sum(weights * rets))
        weighted_var = float(np.sum(weights * (rets - weighted_mean) ** 2))
        ewm_vol = np.sqrt(weighted_var * cfg.annualize)

        if ewm_vol < 1e-8:
            raw_scale = cfg.max_leverage
        else:
            raw_scale = cfg.target_vol / ewm_vol

        raw_scale = max(cfg.min_exposure, min(cfg.max_leverage, raw_scale))

        # 平滑
        self._scale_history.append(raw_scale)
        if len(self._scale_history) >= cfg.smooth_days:
            smoothed = float(np.mean(self._scale_history[-cfg.smooth_days:]))
        else:
            smoothed = raw_scale

        return smoothed

    def reset(self) -> None:
        self._returns.clear()
        self._scale_history.clear()


# ── 回撤仓位递减器 ──────────────────────────────────────────────────

@dataclass
class DrawdownControlConfig:
    """阶梯式回撤减仓配置"""
    progressive_thresholds: list[tuple[float, float]] = field(default_factory=lambda: [
        (0.03, 0.90),   # 3%回撤 → 仓位90%
        (0.05, 0.70),   # 5%回撤 → 仓位70%
        (0.08, 0.50),   # 8%回撤 → 仓位50%
        (0.12, 0.30),   # 12%回撤 → 仓位30%
        (0.15, 0.10),   # 15%回撤 → 仓位10%
        (0.20, 0.00),   # 20%回撤 → 清仓
    ])
    recovery_buffer: float = 0.01  # 回撤改善多少才恢复


class DrawdownPositionReducer:
    """根据回撤阶梯式减仓。

    阈值必须按回撤升序排列。
    在阈值之间线性插值。
    """

    def __init__(self, config: DrawdownControlConfig | None = None) -> None:
        self._cfg = config or DrawdownControlConfig()
        self._peak_nav = 0.0
        self._current_target_scale = 1.0
        # 验证阈值递增
        thresholds = self._cfg.progressive_thresholds
        for i in range(1, len(thresholds)):
            if thresholds[i][0] <= thresholds[i - 1][0]:
                raise ValueError("progressive_thresholds must be sorted by drawdown ascending")

    def update(self, nav: float) -> float:
        """更新净值，返回目标仓位缩放 [0, 1]。"""
        if nav > self._peak_nav:
            self._peak_nav = nav

        if self._peak_nav <= 0:
            return 1.0

        dd = (self._peak_nav - nav) / self._peak_nav
        thresholds = self._cfg.progressive_thresholds

        if not thresholds or dd < thresholds[0][0]:
            target = 1.0
        elif dd >= thresholds[-1][0]:
            target = thresholds[-1][1]
        else:
            # 线性插值
            for i in range(len(thresholds) - 1):
                dd_lo, scale_lo = thresholds[i]
                dd_hi, scale_hi = thresholds[i + 1]
                if dd_lo <= dd < dd_hi:
                    t = (dd - dd_lo) / (dd_hi - dd_lo)
                    target = scale_lo + t * (scale_hi - scale_lo)
                    break
            else:
                target = thresholds[-1][1]

        # 恢复缓冲：仓位只有在回撤明显改善时才恢复
        if target > self._current_target_scale:
            if (self._current_target_scale - target) < -self._cfg.recovery_buffer:
                self._current_target_scale = target
        else:
            self._current_target_scale = target

        return max(0.0, min(1.0, self._current_target_scale))

    def reset(self) -> None:
        self._peak_nav = 0.0
        self._current_target_scale = 1.0


# ── 综合仓位管理叠加器 ──────────────────────────────────────────────

class PositionSizingOverlay:
    """综合仓位管理：波动率目标 + 回撤控制叠加。

    最终缩放 = vol_scale × dd_scale
    """

    def __init__(
        self,
        vol_config: VolatilityTargetConfig | None = None,
        dd_config: DrawdownControlConfig | None = None,
    ) -> None:
        self.vol_overlay = VolatilityTargetOverlay(vol_config)
        self.dd_reducer = DrawdownPositionReducer(dd_config)

    def update(self, nav: float, daily_return: float) -> float:
        """每日更新，返回综合仓位缩放系数。"""
        self.vol_overlay.add_return(daily_return)
        vol_scale = self.vol_overlay.get_scale()
        dd_scale = self.dd_reducer.update(nav)
        final = vol_scale * dd_scale
        return max(0.0, min(2.0, final))  # 允许最大2倍杠杆

    def reset(self) -> None:
        self.vol_overlay.reset()
        self.dd_reducer.reset()
