"""组合波动率目标 — 组合层面的波动率管理。

两条路径共用一份配置 ``VolTargetConfig``：

- **增量路径**（``update`` / ``get_scale`` / ``scale_weights``）：
  逐日喂入组合收益率，按已实现波动率缩放仓位。策略在 bar 循环里用这条。
- **协方差路径**（``adjust``）：一次性传入权重和协方差矩阵，按组合事前
  波动率缩放权重。调仓时一次性算目标权重用这条。

两条路径的钳位参数是分开的（增量用 ``scale_floor``/``scale_cap``，协方差用
``min_exposure``/``max_leverage``），因为二者的历史默认值不同，合并会悄悄
改变其中一条的行为。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)


@dataclass
class VolTargetConfig:
    """波动率目标配置。"""

    target_vol: float = 0.15            # 年化目标波动率
    lookback: int = 60                  # 已实现波动率回看窗口（交易日）
    scale_floor: float = 0.3            # 增量路径缩放下限
    scale_cap: float = 1.5              # 增量路径缩放上限
    use_ewm: bool = False               # True = 指数加权波动率
    ewm_halflife: int = 20              # 指数加权半衰期（交易日）
    annualize: float = float(TRADING_DAYS_PER_YEAR)
    # -- 协方差路径专用 --
    max_leverage: float = 1.5
    min_exposure: float = 0.20
    cash_buffer: float = 0.0            # 保留的现金比例


@dataclass
class VolTargetState:
    """增量路径的对外状态快照（可序列化，用于日报/诊断）。"""

    current_scale: float = 1.0
    realized_vol: float = 0.0
    target_vol: float = 0.0
    n_observations: int = 0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass
class VolTargetResult:
    target_vol: float
    realized_vol: float
    scale_factor: float
    original_weights: dict[str, float]
    adjusted_weights: dict[str, float]


class VolatilityTargeter:
    """组合波动率目标管理器。

    根据目标波动率和实现波动率的比值来缩放仓位/权重：
      scale = target_vol / realized_vol

    支持：
    - 杠杆上限
    - 现金缓冲（Cash buffer）
    - 缩放平滑
    """

    def __init__(self, config: VolTargetConfig | None = None) -> None:
        self._config = config or VolTargetConfig()
        self._returns: list[float] = []
        self._state = VolTargetState(target_vol=self._config.target_vol)
        self._prev_scale: float | None = None

    @property
    def config(self) -> VolTargetConfig:
        return self._config

    @property
    def state(self) -> VolTargetState:
        return self._state

    # ------------------------------------------------------------------
    # 增量路径
    # ------------------------------------------------------------------

    def update(self, daily_return: float) -> float:
        """喂入一个日收益率，返回更新后的缩放因子。"""
        cfg = self._config
        self._returns.append(float(daily_return))
        self._state.n_observations = len(self._returns)

        # 样本不足时不缩放。阈值与 §13.2 的 VolTargetScaler.update 保持一致，
        # 否则同一个组合在两处会算出不同的仓位。
        min_obs = max(5, cfg.lookback // 2)
        if self._state.n_observations < min_obs:
            self._state.realized_vol = 0.0
            self._state.current_scale = 1.0
            return 1.0

        realized_vol = self._realized_vol(self._returns[-cfg.lookback:])
        self._state.realized_vol = realized_vol
        if realized_vol < 1e-10:
            # 波动率为 0（常数收益/数据卡住）时 target/vol 会炸成 inf，
            # 直接取上限，由 scale_cap 兜住。
            scale = cfg.scale_cap
        else:
            scale = cfg.target_vol / realized_vol
        scale = float(np.clip(scale, cfg.scale_floor, cfg.scale_cap))
        self._state.current_scale = scale
        return scale

    def get_scale(self) -> float:
        """当前缩放因子（不触发计算）。"""
        return self._state.current_scale

    def scale_weights(
        self,
        weights: dict[str, float],
        daily_return: float | None = None,
    ) -> dict[str, float]:
        """按当前缩放因子缩放目标权重。

        Args:
            weights: 目标权重 {symbol: weight}
            daily_return: 可选的最新日收益率；给了就先 ``update`` 再缩放，
                省掉调用方「先 update 再 scale」两步走。

        Returns:
            缩放后的权重。总和小于原总和的部分即为现金。
        """
        if daily_return is not None:
            self.update(daily_return)
        scale = self._state.current_scale
        return {sym: w * scale for sym, w in weights.items()}

    def reset(self) -> None:
        """清空收益率历史与状态（新回测/新交易日序列开始时调用）。"""
        self._returns.clear()
        self._state = VolTargetState(target_vol=self._config.target_vol)
        self._prev_scale = None

    def _realized_vol(self, returns: list[float]) -> float:
        """年化已实现波动率。"""
        cfg = self._config
        rets = np.asarray(returns, dtype=float)
        if rets.size < 2:
            return 0.0
        if cfg.use_ewm:
            alpha = 1.0 - np.exp(-np.log(2.0) / max(1, cfg.ewm_halflife))
            weights = np.array(
                [(1 - alpha) ** i for i in range(rets.size - 1, -1, -1)]
            )
            weights /= weights.sum()
            weighted_mean = float(np.sum(weights * rets))
            daily_var = float(np.sum(weights * (rets - weighted_mean) ** 2))
        else:
            daily_var = float(np.var(rets, ddof=1))
        return float(np.sqrt(max(daily_var, 0.0) * cfg.annualize))

    # ------------------------------------------------------------------
    # 协方差路径
    # ------------------------------------------------------------------

    def adjust(
        self,
        weights: dict[str, float],
        covariance: pd.DataFrame,
        smooth: float = 0.5,
    ) -> VolTargetResult:
        """根据协方差矩阵调整权重到目标波动率。

        Args:
            weights: 原始目标权重 {symbol: weight}
            covariance: 日频协方差矩阵
            smooth: 与上一期缩放系数的平滑度 (0=不平滑, 1=完全沿用上期)
        """
        cfg = self._config
        assets = [a for a in weights if a in covariance.columns]
        if not assets:
            return VolTargetResult(
                target_vol=cfg.target_vol,
                realized_vol=0.0,
                scale_factor=1.0,
                original_weights=weights,
                adjusted_weights=weights,
            )

        w = np.array([weights[a] for a in assets])
        cov = covariance.loc[assets, assets].values

        # 组合波动率（年化）
        port_var = float(w @ cov @ w) * cfg.annualize
        port_vol = np.sqrt(max(port_var, 0))

        if port_vol < 1e-8:
            raw_scale = 1.0
        else:
            raw_scale = cfg.target_vol / port_vol

        # 限制杠杆
        raw_scale = max(cfg.min_exposure, min(cfg.max_leverage, raw_scale))

        # 考虑现金缓冲
        raw_scale *= (1.0 - cfg.cash_buffer)

        # 平滑
        if self._prev_scale is not None:
            scale = (1 - smooth) * raw_scale + smooth * self._prev_scale
        else:
            scale = raw_scale
        self._prev_scale = scale

        # 调整权重
        adjusted = {a: weights[a] * scale for a in assets}
        # 不在协方差矩阵中的资产保持原样
        for a in weights:
            if a not in assets:
                adjusted[a] = weights[a]

        return VolTargetResult(
            target_vol=cfg.target_vol,
            realized_vol=port_vol,
            scale_factor=scale,
            original_weights=weights,
            adjusted_weights=adjusted,
        )
