"""Regime 检测器 — 牛/熊/震荡市场状态识别。"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class MarketRegime(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    NEUTRAL = "neutral"


class RegimeDetector:
    """市场 Regime 检测器

    使用移动均线斜率 + 波动率判断市场状态。
    """

    def __init__(
        self,
        ma_short: int = 20,
        ma_long: int = 60,
        vol_window: int = 20,
        vol_threshold: float = 0.25,
    ):
        self._ma_short = ma_short
        self._ma_long = ma_long
        self._vol_window = vol_window
        self._vol_threshold = vol_threshold

    def detect(self, prices: pd.Series) -> MarketRegime:
        """检测当前市场状态。

        Args:
            prices: 指数/基准价格序列

        Returns:
            MarketRegime
        """
        if len(prices) < self._ma_long + 10:
            return MarketRegime.NEUTRAL

        ma_short = prices.rolling(self._ma_short).mean()
        ma_long = prices.rolling(self._ma_long).mean()
        returns = prices.pct_change()
        vol = returns.rolling(self._vol_window).std() * np.sqrt(252)

        current_vol = vol.iloc[-1]
        short_above_long = ma_short.iloc[-1] > ma_long.iloc[-1]
        short_slope = (ma_short.iloc[-1] - ma_short.iloc[-5]) / ma_short.iloc[-5] if ma_short.iloc[-5] > 0 else 0

        if short_above_long and short_slope > 0:
            return MarketRegime.BULL
        elif not short_above_long and short_slope < 0:
            return MarketRegime.BEAR
        else:
            return MarketRegime.NEUTRAL

    def detect_series(self, prices: pd.Series) -> pd.Series:
        """检测时间序列的 regime。"""
        regimes = []
        for i in range(len(prices)):
            if i < self._ma_long + 10:
                regimes.append(MarketRegime.NEUTRAL)
            else:
                window = prices.iloc[:i + 1]
                regimes.append(self.detect(window))
        return pd.Series(regimes, index=prices.index, name="regime")
