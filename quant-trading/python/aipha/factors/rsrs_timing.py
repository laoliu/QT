"""RSRS 择时因子 — 阻力支撑相对强度"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_REGRESSION_WINDOW = 18
DEFAULT_ZSCORE_WINDOW = 600
DEFAULT_BULL_THRESHOLD = 0.7
DEFAULT_BEAR_THRESHOLD = -0.7


class MarketSignal(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    NEUTRAL = "neutral"


@dataclass
class RSRSConfig:
    regression_window: int = DEFAULT_REGRESSION_WINDOW
    zscore_window: int = DEFAULT_ZSCORE_WINDOW
    bull_threshold: float = DEFAULT_BULL_THRESHOLD
    bear_threshold: float = DEFAULT_BEAR_THRESHOLD
    use_r2_correction: bool = True


@dataclass
class RSRSResult:
    beta: float
    r_squared: float
    rsrs_zscore: float | None
    rsrs_std: float | None
    signal: MarketSignal = MarketSignal.NEUTRAL


class RSRSTimingFactor:
    """RSRS（阻力支撑相对强度）择时指标

    用每日最高价 vs 最低价的 OLS 斜率（beta）衡量市场趋势强度。
    zscore(beta) > bull_threshold → 看多
    zscore(beta) < bear_threshold → 看空
    """

    def __init__(self, config: RSRSConfig | None = None):
        self._config = config or RSRSConfig()
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._betas: list[float] = []
        self._r2s: list[float] = []
        self._latest_result: RSRSResult | None = None

    def update(self, high: float, low: float) -> RSRSResult:
        """推入一天数据并返回 RSRS 结果"""
        self._highs.append(high)
        self._lows.append(low)

        cfg = self._config
        n = cfg.regression_window

        if len(self._highs) < n:
            result = RSRSResult(beta=0.0, r_squared=0.0, rsrs_zscore=None, rsrs_std=None)
            self._latest_result = result
            return result

        highs_arr = np.array(self._highs[-n:])
        lows_arr = np.array(self._lows[-n:])
        beta, r2 = self.compute_beta_r2(highs_arr, lows_arr)
        self._betas.append(beta)
        self._r2s.append(r2)

        # Z-Score
        zscore = self.compute_zscore(np.array(self._betas), cfg.zscore_window)
        std = None
        if zscore is not None and cfg.use_r2_correction:
            # R² 修正: rsrs_std = zscore * r2
            zscore_corrected = zscore * r2
            std = zscore
            zscore = zscore_corrected

        # Signal
        signal = MarketSignal.NEUTRAL
        if zscore is not None:
            if zscore > cfg.bull_threshold:
                signal = MarketSignal.BULL
            elif zscore < cfg.bear_threshold:
                signal = MarketSignal.BEAR

        result = RSRSResult(beta=beta, r_squared=r2, rsrs_zscore=zscore,
                            rsrs_std=std, signal=signal)
        self._latest_result = result
        return result

    def get_signal(self) -> MarketSignal:
        if self._latest_result:
            return self._latest_result.signal
        return MarketSignal.NEUTRAL

    def reset(self) -> None:
        self._highs.clear()
        self._lows.clear()
        self._betas.clear()
        self._r2s.clear()
        self._latest_result = None

    @staticmethod
    def compute_beta_r2(highs: np.ndarray, lows: np.ndarray) -> tuple[float, float]:
        """OLS: high = alpha + beta * low + eps"""
        n = len(highs)
        if n < 3:
            return 0.0, 0.0
        x = lows
        y = highs
        mx = x.mean()
        my = y.mean()
        cov = ((x - mx) * (y - my)).sum()
        var = ((x - mx) ** 2).sum()
        if var < 1e-15:
            return 0.0, 0.0
        beta = cov / var
        alpha = my - beta * mx
        y_hat = alpha + beta * x
        ss_res = ((y - y_hat) ** 2).sum()
        ss_tot = ((y - my) ** 2).sum()
        r2 = 1 - ss_res / ss_tot if ss_tot > 1e-15 else 0.0
        return float(beta), float(max(0, r2))

    @staticmethod
    def compute_zscore(values: np.ndarray, window: int) -> float | None:
        if len(values) < 2:
            return None
        recent = values[-min(window, len(values)):]
        mean = recent.mean()
        std = recent.std()
        if std < 1e-10:
            return 0.0
        return float((values[-1] - mean) / std)


def compute_rsrs_series(
    highs: np.ndarray,
    lows: np.ndarray,
    config: RSRSConfig | None = None,
) -> list[RSRSResult]:
    """批量计算 RSRS 时间序列"""
    factor = RSRSTimingFactor(config)
    return [factor.update(float(h), float(l)) for h, l in zip(highs, lows)]


def rsrs_to_position_scale(
    signal: MarketSignal,
    bull_scale: float = 1.0,
    bear_scale: float = 0.0,
    neutral_scale: float = 0.5,
) -> float:
    """将 RSRS 信号转为仓位比例"""
    if signal == MarketSignal.BULL:
        return bull_scale
    elif signal == MarketSignal.BEAR:
        return bear_scale
    return neutral_scale
