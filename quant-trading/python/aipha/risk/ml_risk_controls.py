"""ML 风险控制 — MDD 梯度控制器 + 波动率目标缩放器"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import numpy as np

logger = logging.getLogger(__name__)


# ── MDD 梯度控制器 ──────────────────────────────────────────────────

class MddState(Enum):
    NORMAL = "normal"
    CAUTION = "caution"       # MDD 接近阈值
    REDUCTION = "reduction"   # 主动减仓
    LOCKDOWN = "lockdown"     # 完全锁仓


@dataclass
class MddConfig:
    caution_mdd: float = 0.05       # 5% 回撤进入警戒
    reduction_mdd: float = 0.10     # 10% 回撤开始减仓
    lockdown_mdd: float = 0.15      # 15% 回撤完全锁仓
    caution_scale: float = 0.80     # 警戒时仓位缩放
    reduction_base: float = 0.50    # 减仓基准缩放
    linear_slope: float = 5.0       # 线性减仓斜率
    min_scale: float = 0.10         # 最小仓位缩放


class LinearMddController:
    """基于当前回撤的线性仓位缩放控制器。

    在 caution ~ lockdown 区间内，仓位缩放线性递减：
      scale = max(min_scale, base - slope * (mdd - caution_mdd))
    """

    def __init__(self, config: MddConfig | None = None) -> None:
        self._cfg = config or MddConfig()
        self._peak_nav = 0.0
        self._state = MddState.NORMAL

    @property
    def state(self) -> MddState:
        return self._state

    def update(self, nav: float) -> float:
        """更新净值并返回仓位缩放系数 [0, 1]。"""
        cfg = self._cfg
        if nav > self._peak_nav:
            self._peak_nav = nav

        mdd = (self._peak_nav - nav) / self._peak_nav if self._peak_nav > 0 else 0.0

        if mdd >= cfg.lockdown_mdd:
            self._state = MddState.LOCKDOWN
            return 0.0
        elif mdd >= cfg.reduction_mdd:
            self._state = MddState.REDUCTION
            # 线性递减
            excess = mdd - cfg.reduction_mdd
            scale = cfg.reduction_base - cfg.linear_slope * excess
            return max(cfg.min_scale, min(1.0, scale))
        elif mdd >= cfg.caution_mdd:
            self._state = MddState.CAUTION
            return cfg.caution_scale
        else:
            self._state = MddState.NORMAL
            return 1.0

    def reset(self) -> None:
        self._peak_nav = 0.0
        self._state = MddState.NORMAL


# ── 波动率目标缩放器 ────────────────────────────────────────────────

@dataclass
class VolTargetConfig:
    target_vol: float = 0.15       # 年化目标波动率
    lookback: int = 20             # 回看窗口（交易日）
    vol_floor: float = 0.05        # 波动率下限（防止杠杆过大）
    vol_cap: float = 0.60          # 波动率上限
    max_leverage: float = 2.0      # 最大杠杆
    annualize_factor: float = 252.0


class VolTargetScaler:
    """波动率目标缩放器。

    思路: scale = target_vol / realized_vol
    确保组合波动率始终接近目标值。
    """

    def __init__(self, config: VolTargetConfig | None = None) -> None:
        self._cfg = config or VolTargetConfig()
        self._returns: list[float] = []

    def add_return(self, daily_return: float) -> None:
        self._returns.append(daily_return)

    def get_scale(self) -> float:
        """计算当前缩放比例。"""
        cfg = self._cfg
        lookback = cfg.lookback

        if len(self._returns) < lookback:
            return 1.0  # 数据不足时不缩放

        recent = np.array(self._returns[-lookback:])
        realized_vol = float(np.std(recent, ddof=1)) * np.sqrt(cfg.annualize_factor)

        # 限制波动率范围
        realized_vol = max(cfg.vol_floor, min(cfg.vol_cap, realized_vol))

        scale = cfg.target_vol / realized_vol
        scale = min(scale, cfg.max_leverage)
        return max(0.0, scale)

    def reset(self) -> None:
        self._returns.clear()


# ── 综合 ML 风险控制叠加器 ──────────────────────────────────────────

class MLRiskOverlay:
    """将 MDD 控制和波动率目标叠加到仓位上。

    最终缩放 = min(mdd_scale, vol_scale)
    """

    def __init__(
        self,
        mdd_config: MddConfig | None = None,
        vol_config: VolTargetConfig | None = None,
    ) -> None:
        self.mdd_ctrl = LinearMddController(mdd_config)
        self.vol_scaler = VolTargetScaler(vol_config)

    def update(self, nav: float, daily_return: float) -> float:
        """每日更新，返回最终仓位缩放系数。"""
        mdd_scale = self.mdd_ctrl.update(nav)
        self.vol_scaler.add_return(daily_return)
        vol_scale = self.vol_scaler.get_scale()
        final = min(mdd_scale, vol_scale)
        return final

    def reset(self) -> None:
        self.mdd_ctrl.reset()
        self.vol_scaler.reset()
