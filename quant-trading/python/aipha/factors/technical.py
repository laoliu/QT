"""
技术指标因子

提供常用技术分析指标的因子实现。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import numpy as np
import pandas as pd

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


logger = logging.getLogger(__name__)


class TrendDirection(str, Enum):
    """趋势方向"""

    UP = "up"
    DOWN = "down"
    SIDEWAYS = "sideways"


@dataclass
class TechnicalIndicatorResult:
    """技术指标计算结果"""

    name: str
    values: pd.Series
    signal: pd.Series | None = None
    upper_band: pd.Series | None = None
    lower_band: pd.Series | None = None


# =============================================================================
# 移动平均类
# =============================================================================


def sma(series: pd.Series, window: int) -> pd.Series:
    """简单移动平均

    Args:
        series: 价格序列
        window: 窗口长度

    Returns:
        SMA序列
    """
    return series.rolling(window=window, min_periods=1).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """指数移动平均

    Args:
        series: 价格序列
        span: EMA周期

    Returns:
        EMA序列
    """
    return series.ewm(span=span, adjust=False).mean()


def wma(series: pd.Series, window: int) -> pd.Series:
    """加权移动平均

    Args:
        series: 价格序列
        window: 窗口长度

    Returns:
        WMA序列
    """
    weights = np.arange(1, window + 1, dtype=float)

    def weighted_mean(x: np.ndarray) -> float:
        return np.sum(weights[-len(x) :] * x) / np.sum(weights[-len(x) :])

    return series.rolling(window=window, min_periods=1).apply(weighted_mean, raw=True)


def dema(series: pd.Series, span: int) -> pd.Series:
    """双重指数移动平均

    DEMA = 2 × EMA(series) − EMA(EMA(series))，比普通 EMA 更贴近当前价格。

    Args:
        series: 价格序列
        span: EMA 周期

    Returns:
        DEMA 序列
    """
    ema1 = ema(series, span)
    ema2 = ema(ema1, span)
    return 2 * ema1 - ema2


def tema(series: pd.Series, span: int) -> pd.Series:
    """三重指数移动平均

    TEMA = 3 × EMA − 3 × EMA(EMA) + EMA(EMA(EMA))，滔后更小。

    Args:
        series: 价格序列
        span: EMA 周期

    Returns:
        TEMA 序列
    """
    ema1 = ema(series, span)
    ema2 = ema(ema1, span)
    ema3 = ema(ema2, span)
    return 3 * ema1 - 3 * ema2 + ema3


# =============================================================================
# 动量类
# =============================================================================


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """相对强弱指数

    Args:
        series: 价格序列
        window: RSI周期（默认14）

    Returns:
        RSI序列（0-100）
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(span=window, adjust=False).mean()
    avg_loss = loss.ewm(span=window, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_values = 100 - (100 / (1 + rs))

    return rsi_values.fillna(50)


def macd(
    series: pd.Series,
    fast_span: int = 12,
    slow_span: int = 26,
    signal_span: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD指标

    Args:
        series: 价格序列
        fast_span: 快线周期
        slow_span: 慢线周期
        signal_span: 信号线周期

    Returns:
        (MACD线, 信号线, 柱状图)
    """
    fast_ema = ema(series, fast_span)
    slow_ema = ema(series, slow_span)

    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal_span)
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


def roc(series: pd.Series, window: int = 10) -> pd.Series:
    """变动率

    Args:
        series: 价格序列
        window: 周期

    Returns:
        ROC百分比
    """
    return (series / series.shift(window) - 1) * 100


def momentum(series: pd.Series, window: int = 10) -> pd.Series:
    """动量

    Args:
        series: 价格序列
        window: 周期

    Returns:
        动量值
    """
    return series - series.shift(window)


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_window: int = 14,
    d_window: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """随机指标KD

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        k_window: %K周期
        d_window: %D周期

    Returns:
        (%K, %D)
    """
    lowest_low = low.rolling(window=k_window, min_periods=1).min()
    highest_high = high.rolling(window=k_window, min_periods=1).max()

    k = 100 * (close - lowest_low) / (highest_high - lowest_low + 1e-10)
    d = k.rolling(window=d_window, min_periods=1).mean()

    return k, d


def williams_r(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
) -> pd.Series:
    """威廉指标

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        window: 周期

    Returns:
        %R序列（-100到0）
    """
    highest_high = high.rolling(window=window, min_periods=1).max()
    lowest_low = low.rolling(window=window, min_periods=1).min()

    wr = -100 * (highest_high - close) / (highest_high - lowest_low + 1e-10)

    return wr


def cci(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 20,
) -> pd.Series:
    """商品通道指数

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        window: 周期

    Returns:
        CCI序列
    """
    tp = (high + low + close) / 3
    sma_tp = sma(tp, window)
    mad = tp.rolling(window=window, min_periods=1).apply(
        lambda x: np.mean(np.abs(x - x.mean()))
    )

    cci_values = (tp - sma_tp) / (0.015 * mad + 1e-10)

    return cci_values


# =============================================================================
# 波动率类
# =============================================================================


def bollinger_bands(
    series: pd.Series,
    window: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """布林带

    Args:
        series: 价格序列
        window: 移动平均周期
        num_std: 标准差倍数

    Returns:
        (中轨, 上轨, 下轨)
    """
    middle = sma(series, window)
    std = series.rolling(window=window, min_periods=1).std()

    upper = middle + num_std * std
    lower = middle - num_std * std

    return middle, upper, lower


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
) -> pd.Series:
    """真实波幅

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        window: 周期

    Returns:
        ATR序列
    """
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr_values = tr.ewm(span=window, adjust=False).mean()

    return atr_values


def keltner_channel(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    ema_window: int = 20,
    atr_window: int = 10,
    multiplier: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """肯特纳通道

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        ema_window: EMA周期
        atr_window: ATR周期
        multiplier: ATR倍数

    Returns:
        (中轨, 上轨, 下轨)
    """
    middle = ema(close, ema_window)
    atr_values = atr(high, low, close, atr_window)

    upper = middle + multiplier * atr_values
    lower = middle - multiplier * atr_values

    return middle, upper, lower


def donchian_channel(
    high: pd.Series,
    low: pd.Series,
    window: int = 20,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """唐奇安通道

    Args:
        high: 最高价序列
        low: 最低价序列
        window: 周期

    Returns:
        (中轨, 上轨, 下轨)
    """
    upper = high.rolling(window=window, min_periods=1).max()
    lower = low.rolling(window=window, min_periods=1).min()
    middle = (upper + lower) / 2

    return middle, upper, lower


def historical_volatility(
    close: pd.Series,
    window: int = 20,
    annualize: bool = True,
) -> pd.Series:
    """历史波动率

    Args:
        close: 收盘价序列
        window: 周期
        annualize: 是否年化

    Returns:
        波动率序列
    """
    log_returns = np.log(close / close.shift(1))
    volatility = log_returns.rolling(window=window, min_periods=1).std()

    if annualize:
        volatility = volatility * np.sqrt(TRADING_DAYS_PER_YEAR)

    return volatility


# =============================================================================
# 成交量类
# =============================================================================


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """能量潮

    Args:
        close: 收盘价序列
        volume: 成交量序列

    Returns:
        OBV序列
    """
    direction = np.sign(close.diff())
    direction.iloc[0] = 0

    return (direction * volume).cumsum()


def vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
) -> pd.Series:
    """成交量加权平均价

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        volume: 成交量序列

    Returns:
        VWAP序列
    """
    typical_price = (high + low + close) / 3
    return (typical_price * volume).cumsum() / volume.cumsum()


def volume_ratio(volume: pd.Series, window: int = 5) -> pd.Series:
    """量比

    Args:
        volume: 成交量序列
        window: 参考周期

    Returns:
        量比序列
    """
    avg_volume = volume.shift(1).rolling(window=window, min_periods=1).mean()
    return volume / (avg_volume + 1e-10)


def mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    window: int = 14,
) -> pd.Series:
    """资金流量指标

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        volume: 成交量序列
        window: 周期

    Returns:
        MFI序列（0-100）
    """
    typical_price = (high + low + close) / 3
    raw_money_flow = typical_price * volume

    money_flow_direction = np.sign(typical_price.diff())
    money_flow_direction.iloc[0] = 0

    positive_flow = raw_money_flow.where(money_flow_direction > 0, 0)
    negative_flow = raw_money_flow.where(money_flow_direction < 0, 0)

    positive_sum = positive_flow.rolling(window=window, min_periods=1).sum()
    negative_sum = negative_flow.rolling(window=window, min_periods=1).sum()

    money_ratio = positive_sum / (negative_sum + 1e-10)
    mfi_values = 100 - (100 / (1 + money_ratio))

    return mfi_values


# =============================================================================
# 趋势类
# =============================================================================


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """平均趋向指标

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        window: 周期

    Returns:
        (ADX, +DI, -DI)
    """
    # 计算TR
    atr_values = atr(high, low, close, window)

    # 计算方向移动
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)

    # 平滑DM
    plus_dm_smooth = plus_dm.ewm(span=window, adjust=False).mean()
    minus_dm_smooth = minus_dm.ewm(span=window, adjust=False).mean()

    # 计算DI
    plus_di = 100 * plus_dm_smooth / (atr_values + 1e-10)
    minus_di = 100 * minus_dm_smooth / (atr_values + 1e-10)

    # 计算DX和ADX
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx_values = dx.ewm(span=window, adjust=False).mean()

    return adx_values, plus_di, minus_di


def aroon(
    high: pd.Series,
    low: pd.Series,
    window: int = 25,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """阿隆指标

    Args:
        high: 最高价序列
        low: 最低价序列
        window: 周期

    Returns:
        (Aroon Up, Aroon Down, Aroon Oscillator)
    """

    def days_since_high(x: np.ndarray) -> int:
        return window - x.argmax()

    def days_since_low(x: np.ndarray) -> int:
        return window - x.argmin()

    aroon_up = (
        high.rolling(window=window + 1, min_periods=1).apply(days_since_high, raw=True)
        / window
        * 100
    )
    aroon_down = (
        low.rolling(window=window + 1, min_periods=1).apply(days_since_low, raw=True)
        / window
        * 100
    )
    aroon_osc = aroon_up - aroon_down

    return aroon_up, aroon_down, aroon_osc


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    atr_window: int = 10,
    multiplier: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """超级趋势

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        atr_window: ATR周期
        multiplier: ATR倍数

    Returns:
        (超级趋势线, 趋势方向：1为上升趋势，-1为下降趋势)
    """
    atr_values = atr(high, low, close, atr_window)

    hl2 = (high + low) / 2
    upper_band = hl2 + multiplier * atr_values
    lower_band = hl2 - multiplier * atr_values

    supertrend_values = pd.Series(index=close.index, dtype=float)
    direction = pd.Series(index=close.index, dtype=int)

    supertrend_values.iloc[0] = upper_band.iloc[0]
    direction.iloc[0] = 1

    for i in range(1, len(close)):
        if close.iloc[i] > supertrend_values.iloc[i - 1]:
            supertrend_values.iloc[i] = lower_band.iloc[i]
            direction.iloc[i] = 1
        elif close.iloc[i] < supertrend_values.iloc[i - 1]:
            supertrend_values.iloc[i] = upper_band.iloc[i]
            direction.iloc[i] = -1
        else:
            supertrend_values.iloc[i] = supertrend_values.iloc[i - 1]
            direction.iloc[i] = direction.iloc[i - 1]

            if direction.iloc[i] == 1 and lower_band.iloc[i] > supertrend_values.iloc[i]:
                supertrend_values.iloc[i] = lower_band.iloc[i]
            elif direction.iloc[i] == -1 and upper_band.iloc[i] < supertrend_values.iloc[i]:
                supertrend_values.iloc[i] = upper_band.iloc[i]

    return supertrend_values, direction


# =============================================================================
# 因子计算器
# =============================================================================


class TechnicalFactorCalculator:
    """技术因子计算器

    批量计算多个技术指标因子。
    """

    def __init__(self):
        self._indicators: dict[str, Callable] = {}
        self._register_default_indicators()

    def _register_default_indicators(self) -> None:
        """注册默认指标"""
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

    def register_indicator(
        self,
        name: str,
        calculator: Callable[[pd.DataFrame], pd.Series],
    ) -> None:
        """注册自定义指标"""
        self._indicators[name] = calculator

    def calculate(
        self,
        df: pd.DataFrame,
        indicators: list[str] | None = None,
    ) -> pd.DataFrame:
        """计算技术指标

        Args:
            df: 包含OHLCV数据的DataFrame
            indicators: 要计算的指标列表（默认计算所有）

        Returns:
            包含技术指标的DataFrame
        """
        if indicators is None:
            indicators = list(self._indicators.keys())

        result = df.copy()

        for name in indicators:
            if name not in self._indicators:
                logger.warning(f"Unknown indicator: {name}")
                continue

            try:
                result[name] = self._indicators[name](df)
            except Exception as e:
                logger.error(f"Failed to calculate {name}: {e}")
                result[name] = np.nan

        return result

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算所有已注册指标"""
        return self.calculate(df, list(self._indicators.keys()))

    def get_indicator_names(self) -> list[str]:
        """获取所有已注册指标名称列表。

        Returns:
            指标名称字符串列表
        """
        return list(self._indicators.keys())
