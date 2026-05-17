"""RSRS 择时因子 — 阻力支撑相对强度 (Resistance Support Relative Strength)

基于光大证券研报的 RSRS 算法实现，用作策略的 timing overlay。

算法步骤:
1. 滚动 N 天窗口对 (最低价, 最高价) 做线性回归，得斜率 β
2. 对 β 做 M 天滚动 Z-Score 标准化 → rsrs_zscore
3. 右偏修正: rsrs_std = rsrs_zscore * R² → 对拟合质量的加权
4. 信号输出: rsrs_std > S_bull → BULL, rsrs_std < S_bear → BEAR, else NEUTRAL

References:
- 光大证券《技术择时系列报告》
- RSRS 标准分指标择时策略 (2017)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ============================================================================
# Constants & Enums
# ============================================================================

DEFAULT_REGRESSION_WINDOW = 18  # β 回归窗口（交易日）
DEFAULT_ZSCORE_WINDOW = 600    # Z-Score 标准化窗口（交易日）
DEFAULT_BULL_THRESHOLD = 0.7   # 看多阈值
DEFAULT_BEAR_THRESHOLD = -0.7  # 看空阈值


class MarketSignal(str, Enum):
    """RSRS 市场信号"""
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class RSRSConfig:
    """RSRS 配置参数

    Attributes:
        regression_window: OLS 回归窗口大小（默认 18 交易日）
        zscore_window: Z-Score 标准化窗口（默认 600 交易日）
        bull_threshold: 看多阈值（默认 0.7）
        bear_threshold: 看空阈值（默认 -0.7）
        use_r2_correction: 是否使用 R² 右偏修正（默认 True）
    """
    regression_window: int = DEFAULT_REGRESSION_WINDOW
    zscore_window: int = DEFAULT_ZSCORE_WINDOW
    bull_threshold: float = DEFAULT_BULL_THRESHOLD
    bear_threshold: float = DEFAULT_BEAR_THRESHOLD
    use_r2_correction: bool = True


@dataclass
class RSRSResult:
    """单个交易日的 RSRS 计算结果

    Attributes:
        beta: OLS 回归斜率
        r_squared: R² 拟合优度
        rsrs_zscore: β 的 Z-Score
        rsrs_std: 右偏修正值 (zscore * R²)
        signal: 市场信号 (BULL/BEAR/NEUTRAL)
    """
    beta: float
    r_squared: float
    rsrs_zscore: float | None = None
    rsrs_std: float | None = None
    signal: MarketSignal = MarketSignal.NEUTRAL


# ============================================================================
# Core Class
# ============================================================================


class RSRSTimingFactor:
    """RSRS 择时因子计算器

    维护内部状态（β 历史序列），支持逐日推送数据。
    """

    def __init__(self, config: RSRSConfig | None = None):
        self.config = config or RSRSConfig()
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._betas: list[float] = []
        self._r2s: list[float] = []
        self._latest_result: RSRSResult | None = None

    def update(self, high: float, low: float) -> RSRSResult:
        """推送单日最高价/最低价，返回当日 RSRS 结果。

        内部维护 highs/lows 缓冲区和 β 历史序列。
        """
        self._highs.append(high)
        self._lows.append(low)

        n = self.config.regression_window

        # 数据不足：无法回归
        if len(self._highs) < n:
            result = RSRSResult(beta=0.0, r_squared=0.0)
            self._latest_result = result
            return result

        # 取最近 N 天的 high/low 做回归
        h_arr = np.array(self._highs[-n:])
        l_arr = np.array(self._lows[-n:])
        beta, r2 = self.compute_beta_r2(h_arr, l_arr)

        self._betas.append(beta)
        self._r2s.append(r2)

        # Z-Score
        beta_arr = np.array(self._betas)
        zscore = self.compute_zscore(beta_arr, self.config.zscore_window)

        # 右偏修正
        rsrs_std = None
        if zscore is not None:
            rsrs_std = zscore * r2 if self.config.use_r2_correction else zscore

        # 信号判定
        signal = MarketSignal.NEUTRAL
        effective_score = rsrs_std if rsrs_std is not None else zscore
        if effective_score is not None:
            if effective_score > self.config.bull_threshold:
                signal = MarketSignal.BULL
            elif effective_score < self.config.bear_threshold:
                signal = MarketSignal.BEAR

        result = RSRSResult(
            beta=beta,
            r_squared=r2,
            rsrs_zscore=zscore,
            rsrs_std=rsrs_std,
            signal=signal,
        )
        self._latest_result = result
        return result

    def get_signal(self) -> MarketSignal:
        """获取最新信号。"""
        if self._latest_result is None:
            return MarketSignal.NEUTRAL
        return self._latest_result.signal

    def reset(self) -> None:
        """重置内部状态。"""
        self._highs.clear()
        self._lows.clear()
        self._betas.clear()
        self._r2s.clear()
        self._latest_result = None

    @staticmethod
    def compute_beta_r2(
        highs: np.ndarray, lows: np.ndarray,
    ) -> tuple[float, float]:
        """对 (low, high) 做 OLS 回归，返回 (β, R²)。

        回归模型: high = α + β * low
        β 表示支撑/阻力的相对强度。

        Args:
            highs: 最高价数组 (y)
            lows: 最低价数组 (x)

        Returns:
            (beta, r_squared) 元组
        """
        n = len(highs)
        if n < 2:
            return 0.0, 0.0

        x = lows.astype(np.float64)
        y = highs.astype(np.float64)

        x_mean = np.mean(x)
        y_mean = np.mean(y)

        ss_xy = np.sum((x - x_mean) * (y - y_mean))
        ss_xx = np.sum((x - x_mean) ** 2)

        if ss_xx < 1e-12:
            return 0.0, 0.0

        beta = ss_xy / ss_xx
        alpha = y_mean - beta * x_mean

        y_pred = alpha + beta * x
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - y_mean) ** 2)

        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
        r_squared = max(0.0, min(1.0, r_squared))

        return float(beta), float(r_squared)

    @staticmethod
    def compute_zscore(
        values: np.ndarray, window: int,
    ) -> float | None:
        """计算值序列最后一个元素的滚动 Z-Score。

        Args:
            values: 历史 β 值序列
            window: Z-Score 计算窗口

        Returns:
            Z-Score，如果数据不足返回 None
        """
        if len(values) < 2:
            return None

        # 取最近 window 个值
        lookback = values[-window:] if len(values) >= window else values
        mean = np.mean(lookback)
        std = np.std(lookback, ddof=1)

        if std < 1e-12:
            return 0.0

        return float((values[-1] - mean) / std)


# ============================================================================
# Convenience Functions
# ============================================================================


def compute_rsrs_series(
    highs: np.ndarray,
    lows: np.ndarray,
    config: RSRSConfig | None = None,
) -> list[RSRSResult]:
    """批量计算 RSRS 序列（用于回测/研究）。

    Args:
        highs: 最高价序列
        lows: 最低价序列
        config: RSRS 配置

    Returns:
        每日 RSRS 结果列表（前 regression_window-1 日为空值）
    """
    factor = RSRSTimingFactor(config)
    results: list[RSRSResult] = []
    for i in range(len(highs)):
        result = factor.update(float(highs[i]), float(lows[i]))
        results.append(result)
    return results


def rsrs_to_position_scale(
    signal: MarketSignal,
    bull_scale: float = 1.0,
    bear_scale: float = 0.0,
    neutral_scale: float = 0.5,
) -> float:
    """将 RSRS 信号转换为仓位缩放比例。

    Args:
        signal: RSRS 信号
        bull_scale: 看多时的仓位比例
        bear_scale: 看空时的仓位比例
        neutral_scale: 中性时的仓位比例

    Returns:
        仓位缩放因子 [0, 1]
    """
    mapping = {
        MarketSignal.BULL: bull_scale,
        MarketSignal.BEAR: bear_scale,
        MarketSignal.NEUTRAL: neutral_scale,
    }
    return mapping.get(signal, neutral_scale)
