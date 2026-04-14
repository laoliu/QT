"""
技术指标因子测试

测试覆盖：
- 移动平均: sma/ema/wma/dema/tema
- 振荡器: rsi/macd/stochastic/williams_r/cci/mfi
- 动量: roc/momentum
- 波动率: bollinger_bands/atr/keltner_channel/donchian_channel/historical_volatility
- 成交量: obv/vwap/volume_ratio
- 趋势: adx/aroon/supertrend
- TechnicalFactorCalculator: 注册/计算/批量
"""

import numpy as np
import pandas as pd
import pytest

from aipha.factors.technical import (
    TechnicalFactorCalculator,
    TechnicalIndicatorResult,
    TrendDirection,
    adx,
    aroon,
    atr,
    bollinger_bands,
    cci,
    dema,
    donchian_channel,
    ema,
    historical_volatility,
    keltner_channel,
    macd,
    mfi,
    momentum,
    obv,
    roc,
    rsi,
    sma,
    stochastic,
    supertrend,
    tema,
    volume_ratio,
    vwap,
    williams_r,
    wma,
)


@pytest.fixture
def price_series() -> pd.Series:
    """100日随机价格序列"""
    rng = np.random.default_rng(42)
    prices = 100 + np.cumsum(rng.standard_normal(100) * 0.5)
    return pd.Series(prices, name="close")


@pytest.fixture
def ohlcv_df() -> pd.DataFrame:
    """100日OHLCV数据"""
    rng = np.random.default_rng(42)
    n = 100
    close = 100 + np.cumsum(rng.standard_normal(n) * 0.5)
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close + rng.standard_normal(n) * 0.3
    volume = rng.uniform(1e6, 5e6, n)
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


# ============================================================
# 移动平均
# ============================================================


class TestMovingAverages:
    def test_sma_length(self, price_series):
        result = sma(price_series, 10)
        assert len(result) == len(price_series)

    def test_sma_first_values_not_nan(self, price_series):
        """SMA uses min_periods=1, so first value is valid."""
        result = sma(price_series, 10)
        assert not pd.isna(result.iloc[0])

    def test_sma_valid_after_window(self, price_series):
        result = sma(price_series, 10)
        assert not pd.isna(result.iloc[9])

    def test_ema_length(self, price_series):
        result = ema(price_series, 12)
        assert len(result) == len(price_series)

    def test_ema_valid_values(self, price_series):
        result = ema(price_series, 12)
        assert not pd.isna(result.iloc[-1])

    def test_wma_length(self, price_series):
        result = wma(price_series, 10)
        assert len(result) == len(price_series)

    def test_dema_length(self, price_series):
        result = dema(price_series, 12)
        assert len(result) == len(price_series)

    def test_tema_length(self, price_series):
        result = tema(price_series, 12)
        assert len(result) == len(price_series)


# ============================================================
# 振荡器
# ============================================================


class TestOscillators:
    def test_rsi_range(self, price_series):
        result = rsi(price_series, 14)
        valid = result.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_default_window(self, price_series):
        result = rsi(price_series)
        assert len(result) == len(price_series)

    def test_macd_returns_three(self, price_series):
        macd_line, signal_line, histogram = macd(price_series)
        assert len(macd_line) == len(price_series)
        assert len(signal_line) == len(price_series)
        assert len(histogram) == len(price_series)

    def test_macd_custom_params(self, price_series):
        m, s, h = macd(price_series, fast_span=8, slow_span=21, signal_span=5)
        assert len(m) == len(price_series)

    def test_stochastic_range(self, ohlcv_df):
        k, d = stochastic(
            ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"],
        )
        valid_k = k.dropna()
        assert (valid_k >= 0).all() and (valid_k <= 100).all()

    def test_williams_r_range(self, ohlcv_df):
        result = williams_r(
            ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"],
        )
        valid = result.dropna()
        assert (valid >= -100).all() and (valid <= 0).all()

    def test_cci_length(self, ohlcv_df):
        result = cci(
            ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"],
        )
        assert len(result) == len(ohlcv_df)

    def test_mfi_range(self, ohlcv_df):
        result = mfi(
            ohlcv_df["high"], ohlcv_df["low"],
            ohlcv_df["close"], ohlcv_df["volume"],
        )
        valid = result.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()


# ============================================================
# 动量
# ============================================================


class TestMomentum:
    def test_roc_length(self, price_series):
        result = roc(price_series, 10)
        assert len(result) == len(price_series)

    def test_momentum_length(self, price_series):
        result = momentum(price_series, 10)
        assert len(result) == len(price_series)

    def test_roc_first_values_nan(self, price_series):
        result = roc(price_series, 10)
        assert pd.isna(result.iloc[0])


# ============================================================
# 波动率 / 通道
# ============================================================


class TestVolatilityAndChannels:
    def test_bollinger_bands_three_series(self, price_series):
        mid, upper, lower = bollinger_bands(price_series)
        assert len(mid) == len(price_series)
        # upper >= mid >= lower (skip NaN from std)
        mask = upper.notna() & mid.notna() & lower.notna()
        assert (upper[mask] >= mid[mask]).all()
        assert (mid[mask] >= lower[mask]).all()

    def test_atr_positive(self, ohlcv_df):
        result = atr(
            ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"],
        )
        valid = result.dropna()
        assert (valid >= 0).all()

    def test_keltner_channel(self, ohlcv_df):
        mid, upper, lower = keltner_channel(
            ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"],
        )
        assert len(mid) == len(ohlcv_df)

    def test_donchian_channel(self, ohlcv_df):
        mid, upper, lower = donchian_channel(
            ohlcv_df["high"], ohlcv_df["low"],
        )
        valid_idx = upper.dropna().index
        assert (upper[valid_idx] >= lower[valid_idx]).all()

    def test_historical_volatility_positive(self, price_series):
        result = historical_volatility(price_series, window=20)
        valid = result.dropna()
        assert (valid >= 0).all()

    def test_historical_volatility_annualized(self, price_series):
        ann = historical_volatility(price_series, window=20, annualize=True)
        raw = historical_volatility(price_series, window=20, annualize=False)
        # 年化应 > 非年化（假设 factor > 1）
        valid_ann = ann.dropna()
        valid_raw = raw.dropna()
        common = valid_ann.index.intersection(valid_raw.index)
        if len(common) > 0:
            assert (ann[common] >= raw[common]).all()


# ============================================================
# 成交量
# ============================================================


class TestVolume:
    def test_obv_length(self, ohlcv_df):
        result = obv(ohlcv_df["close"], ohlcv_df["volume"])
        assert len(result) == len(ohlcv_df)

    def test_vwap_length(self, ohlcv_df):
        result = vwap(
            ohlcv_df["high"], ohlcv_df["low"],
            ohlcv_df["close"], ohlcv_df["volume"],
        )
        assert len(result) == len(ohlcv_df)

    def test_volume_ratio_positive(self, ohlcv_df):
        result = volume_ratio(ohlcv_df["volume"], window=5)
        valid = result.dropna()
        assert (valid >= 0).all()


# ============================================================
# 趋势
# ============================================================


class TestTrend:
    def test_adx_returns_three(self, ohlcv_df):
        adx_val, plus_di, minus_di = adx(
            ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"],
        )
        assert len(adx_val) == len(ohlcv_df)
        assert len(plus_di) == len(ohlcv_df)
        assert len(minus_di) == len(ohlcv_df)

    def test_adx_range(self, ohlcv_df):
        adx_val, _, _ = adx(
            ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"],
        )
        valid = adx_val.dropna()
        assert (valid >= 0).all()

    def test_aroon_returns_three(self, ohlcv_df):
        up, down, osc = aroon(ohlcv_df["high"], ohlcv_df["low"])
        assert len(up) == len(ohlcv_df)
        assert len(osc) == len(ohlcv_df)

    def test_supertrend_returns_two(self, ohlcv_df):
        trend_line, direction = supertrend(
            ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"],
        )
        assert len(trend_line) == len(ohlcv_df)
        assert len(direction) == len(ohlcv_df)

    def test_supertrend_direction_values(self, ohlcv_df):
        _, direction = supertrend(
            ohlcv_df["high"], ohlcv_df["low"], ohlcv_df["close"],
        )
        valid = direction.dropna()
        assert all(v in (1.0, -1.0) for v in valid)


# ============================================================
# TechnicalFactorCalculator
# ============================================================


class TestTechnicalFactorCalculator:
    def test_default_indicators(self):
        calc = TechnicalFactorCalculator()
        names = calc.get_indicator_names()
        assert len(names) >= 14
        assert "sma_5" in names
        assert "rsi_14" in names

    def test_register_custom(self):
        calc = TechnicalFactorCalculator()
        calc.register_indicator("custom", lambda df: df["close"] * 2)
        assert "custom" in calc.get_indicator_names()

    def test_calculate_specific(self, ohlcv_df):
        calc = TechnicalFactorCalculator()
        result = calc.calculate(ohlcv_df, indicators=["sma_5", "rsi_14"])
        assert "sma_5" in result.columns
        assert "rsi_14" in result.columns

    def test_calculate_all(self, ohlcv_df):
        calc = TechnicalFactorCalculator()
        result = calc.calculate_all(ohlcv_df)
        assert len(result.columns) > len(ohlcv_df.columns)


class TestTrendDirection:
    def test_values(self):
        assert TrendDirection.UP == "up"
        assert TrendDirection.DOWN == "down"
        assert TrendDirection.SIDEWAYS == "sideways"


class TestTechnicalIndicatorResult:
    def test_basic(self):
        s = pd.Series([1.0, 2.0, 3.0])
        r = TechnicalIndicatorResult(name="test", values=s)
        assert r.name == "test"
        assert len(r.values) == 3
        assert r.signal is None
