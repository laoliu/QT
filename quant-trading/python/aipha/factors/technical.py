"""技术指标因子 — RSI / MACD / Bollinger 等转化为截面可排序因子"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aipha.factors.base import Factor as FactorBase


class RSIFactor(FactorBase):
    """RSI 指标"""

    name = "rsi"
    category = "technical"

    def __init__(self, window: int = 14) -> None:
        self.window = window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(span=self.window, adjust=False).mean()
        avg_loss = loss.ewm(span=self.window, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100.0 - 100.0 / (1.0 + rs)


class MACDFactor(FactorBase):
    """MACD 柱状图值 / close"""

    name = "macd_hist"
    category = "technical"

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def compute(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        ema_fast = c.ewm(span=self.fast, adjust=False).mean()
        ema_slow = c.ewm(span=self.slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=self.signal, adjust=False).mean()
        hist = dif - dea
        return hist / c.replace(0, np.nan)


class BollingerPctFactor(FactorBase):
    """%B Bollinger: (close - lower) / (upper - lower)"""

    name = "bollinger_pct"
    category = "technical"

    def __init__(self, window: int = 20, num_std: float = 2.0) -> None:
        self.window = window
        self.num_std = num_std

    def compute(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        ma = c.rolling(self.window).mean()
        std = c.rolling(self.window).std()
        upper = ma + self.num_std * std
        lower = ma - self.num_std * std
        width = upper - lower
        return (c - lower) / width.replace(0, np.nan)


class KDJFactor(FactorBase):
    """KDJ J值"""

    name = "kdj_j"
    category = "technical"

    def __init__(self, n: int = 9) -> None:
        self.n = n

    def compute(self, df: pd.DataFrame) -> pd.Series:
        low_n = df["low"].rolling(self.n).min()
        high_n = df["high"].rolling(self.n).max()
        rsv = (df["close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        return 3 * k - 2 * d


class WilliamsRFactor(FactorBase):
    """Williams %R"""

    name = "williams_r"
    category = "technical"

    def __init__(self, window: int = 14) -> None:
        self.window = window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        high_n = df["high"].rolling(self.window).max()
        low_n = df["low"].rolling(self.window).min()
        return (high_n - df["close"]) / (high_n - low_n).replace(0, np.nan) * -100


class ATRFactor(FactorBase):
    """ATR / close (相对 ATR)"""

    name = "atr_ratio"
    category = "technical"

    def __init__(self, window: int = 14) -> None:
        self.window = window

    def compute(self, df: pd.DataFrame) -> pd.Series:
        h, lo, c = df["high"], df["low"], df["close"]
        prev_c = c.shift(1)
        tr = pd.concat([
            h - lo,
            (h - prev_c).abs(),
            (lo - prev_c).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(span=self.window, adjust=False).mean()
        return atr / c.replace(0, np.nan)


class OBVFactor(FactorBase):
    """OBV 动量 (OBV 短期 / 长期 EMA)"""

    name = "obv_momentum"
    category = "technical"

    def __init__(self, short: int = 5, long: int = 20) -> None:
        self.short = short
        self.long = long

    def compute(self, df: pd.DataFrame) -> pd.Series:
        sign = np.sign(df["close"].diff())
        obv = (sign * df["volume"]).cumsum()
        short_ema = obv.ewm(span=self.short, adjust=False).mean()
        long_ema = obv.ewm(span=self.long, adjust=False).mean()
        return short_ema / long_ema.replace(0, np.nan) - 1.0


# ---------------------------------------------------------------------------
# Standalone helper functions (used by TechnicalFactorCalculator)
# ---------------------------------------------------------------------------
import logging as _logging
from typing import Callable

_tech_logger = _logging.getLogger(__name__)


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(span=window, adjust=False).mean()
    avg_loss = loss.ewm(span=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def roc(series: pd.Series, window: int) -> pd.Series:
    return series.pct_change(window)


def momentum(series: pd.Series, window: int) -> pd.Series:
    return series - series.shift(window)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    prev_c = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_c).abs(),
        (low - prev_c).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=window, adjust=False).mean()


def historical_volatility(series: pd.Series, window: int = 20) -> pd.Series:
    return series.pct_change().rolling(window).std() * np.sqrt(252)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    sign = np.sign(close.diff())
    return (sign * volume).cumsum()


def volume_ratio(volume: pd.Series, window: int = 5) -> pd.Series:
    avg = volume.rolling(window).mean()
    return volume / avg.replace(0, np.nan)


def bollinger_bands(
    series: pd.Series,
    window: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """布林带 → (中轨, 上轨, 下轨)"""
    middle = sma(series, window)
    std = series.rolling(window=window, min_periods=1).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return middle, upper, lower


def macd(
    series: pd.Series,
    fast_span: int = 12,
    slow_span: int = 26,
    signal_span: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD 指标 → (MACD线, 信号线, 柱状图)"""
    fast_ema = ema(series, fast_span)
    slow_ema = ema(series, slow_span)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal_span)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


class TechnicalFactorCalculator:
    """技术因子计算器 — 批量计算多个技术指标因子。"""

    def __init__(self) -> None:
        self._indicators: dict[str, Callable] = {}
        self._register_default_indicators()

    def _register_default_indicators(self) -> None:
        self._indicators = {
            "sma_5": lambda df: sma(df["close"], 5),
            "sma_10": lambda df: sma(df["close"], 10),
            "sma_20": lambda df: sma(df["close"], 20),
            "sma_60": lambda df: sma(df["close"], 60),
            "ema_12": lambda df: ema(df["close"], 12),
            "ema_26": lambda df: ema(df["close"], 26),
            "rsi_14": lambda df: rsi(df["close"], 14),
            "rsi_6": lambda df: rsi(df["close"], 6),
            "roc_10": lambda df: roc(df["close"], 10),
            "momentum_10": lambda df: momentum(df["close"], 10),
            "atr_14": lambda df: atr(df["high"], df["low"], df["close"], 14),
            "volatility_20": lambda df: historical_volatility(df["close"], 20),
            "obv": lambda df: obv(df["close"], df["volume"]),
            "volume_ratio_5": lambda df: volume_ratio(df["volume"], 5),
        }

    def register_indicator(self, name: str, calculator: Callable[[pd.DataFrame], pd.Series]) -> None:
        self._indicators[name] = calculator

    def calculate(self, df: pd.DataFrame, indicators: list[str] | None = None) -> pd.DataFrame:
        if indicators is None:
            indicators = list(self._indicators.keys())
        result = df.copy()
        for name in indicators:
            if name not in self._indicators:
                _tech_logger.warning("Unknown indicator: %s", name)
                continue
            try:
                result[name] = self._indicators[name](df)
            except Exception as e:
                _tech_logger.error("Failed to calculate %s: %s", name, e)
                result[name] = np.nan
        return result

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.calculate(df, list(self._indicators.keys()))

    def get_indicator_names(self) -> list[str]:
        return list(self._indicators.keys())
