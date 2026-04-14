"""组合波动率目标 — 基于协方差的组合层面波动率管理"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class VolTargetResult:
    target_vol: float
    realized_vol: float
    scale_factor: float
    original_weights: dict[str, float]
    adjusted_weights: dict[str, float]


class VolatilityTargeter:
    """组合波动率目标管理器。

    根据目标波动率和实现波动率的比值来缩放权重：
      scale = target_vol / realized_vol
      adjusted_weights = scale * original_weights

    支持：
    - 杠杆上限
    - 现金缓冲（Cash buffer）
    - 缩放平滑
    """

    def __init__(
        self,
        target_vol: float = 0.15,       # 年化目标波动率
        max_leverage: float = 1.5,
        min_exposure: float = 0.20,
        cash_buffer: float = 0.0,       # 保留的现金比例
        annualize: float = 252.0,
    ) -> None:
        self._target_vol = target_vol
        self._max_leverage = max_leverage
        self._min_exposure = min_exposure
        self._cash_buffer = cash_buffer
        self._annualize = annualize
        self._prev_scale: float | None = None

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
        assets = [a for a in weights if a in covariance.columns]
        if not assets:
            return VolTargetResult(
                target_vol=self._target_vol,
                realized_vol=0.0,
                scale_factor=1.0,
                original_weights=weights,
                adjusted_weights=weights,
            )

        w = np.array([weights[a] for a in assets])
        cov = covariance.loc[assets, assets].values

        # 组合波动率（年化）
        port_var = float(w @ cov @ w) * self._annualize
        port_vol = np.sqrt(max(port_var, 0))

        if port_vol < 1e-8:
            raw_scale = 1.0
        else:
            raw_scale = self._target_vol / port_vol

        # 限制杠杆
        raw_scale = max(self._min_exposure, min(self._max_leverage, raw_scale))

        # 考虑现金缓冲
        raw_scale *= (1.0 - self._cash_buffer)

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
            target_vol=self._target_vol,
            realized_vol=port_vol,
            scale_factor=scale,
            original_weights=weights,
            adjusted_weights=adjusted,
        )
