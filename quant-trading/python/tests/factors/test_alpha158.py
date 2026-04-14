"""
Alpha158 因子库单元测试

覆盖: 正常计算、配置驱动、空数据、极端值、输出格式
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# OHLCV 保留列(不算因子)
_META_COLS = {"datetime", "symbol", "open", "high", "low", "close", "volume"}

from aipha.factors.alpha158 import (
    Alpha158,
    Alpha158Config,
    compute_alpha158,
    get_alpha158_factor_names,
)


# ─── Fixture: 构造 OHLCV 面板 ─────────────────────────────

@pytest.fixture
def sample_panel() -> pd.DataFrame:
    """2 只股票 x 60 日 OHLCV 数据"""
    np.random.seed(123)
    dates = pd.bdate_range("2023-01-01", periods=60)
    symbols = ["000001.SZ", "600000.SH"]
    rows = []
    for sym in symbols:
        base = 15.0 if sym == "000001.SZ" else 10.0
        prices = base + np.cumsum(np.random.randn(60) * 0.3)
        for i, dt in enumerate(dates):
            c = prices[i]
            o = c + np.random.randn() * 0.1
            h = max(o, c) + abs(np.random.randn()) * 0.2
            lo = min(o, c) - abs(np.random.randn()) * 0.2
            v = np.random.uniform(1e6, 5e6)
            rows.append({"datetime": dt, "symbol": sym, "open": o, "high": h,
                         "low": lo, "close": c, "volume": v})
    return pd.DataFrame(rows)


# ─── 正常计算 ─────────────────────────────────────────────

class TestAlpha158Normal:
    def test_compute_returns_dataframe(self, sample_panel):
        result = compute_alpha158(sample_panel)
        assert isinstance(result, pd.DataFrame)
        assert "datetime" in result.columns
        assert "symbol" in result.columns

    def test_factor_count_default(self, sample_panel):
        result = compute_alpha158(sample_panel)
        factor_cols = [c for c in result.columns if c not in _META_COLS]
        assert len(factor_cols) == 158

    def test_factor_names_list(self):
        names = get_alpha158_factor_names()
        assert len(names) == 158
        assert "kmid" in names
        assert "corr_5" in names or any("corr" in n for n in names)

    def test_all_stocks_present(self, sample_panel):
        result = compute_alpha158(sample_panel)
        assert set(result["symbol"].unique()) == {"000001.SZ", "600000.SH"}

    def test_no_inf_values(self, sample_panel):
        result = compute_alpha158(sample_panel)
        factor_cols = [c for c in result.columns if c not in _META_COLS]
        for col in factor_cols:
            assert not np.isinf(result[col]).any(), f"{col} contains inf"


# ─── 配置驱动 ─────────────────────────────────────────────

class TestAlpha158Config:
    def test_custom_windows(self, sample_panel):
        config = Alpha158Config(windows=[5, 10])
        calc = Alpha158(config=config)
        result = calc.compute(sample_panel)
        factor_cols = [c for c in result.columns if c not in _META_COLS]
        # 窗口缩减,因子数应减少
        default_count = len(get_alpha158_factor_names())
        assert len(factor_cols) < default_count

    def test_default_config_has_all_windows(self):
        config = Alpha158Config()
        assert 5 in config.windows
        assert 20 in config.windows


# ─── 边界条件 ─────────────────────────────────────────────

class TestAlpha158Edge:
    def test_single_stock(self, sample_panel):
        df = sample_panel[sample_panel["symbol"] == "000001.SZ"].copy()
        calc = Alpha158()
        result = calc.compute(df)
        assert len(result) > 0
        assert result["symbol"].unique().tolist() == ["000001.SZ"]

    def test_short_history(self):
        """仅 5 天数据，虽然大量 NaN 但不应报错"""
        dates = pd.bdate_range("2023-01-01", periods=5)
        rows = []
        for dt in dates:
            rows.append({"datetime": dt, "symbol": "TEST", "open": 10.0,
                         "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1e6})
        df = pd.DataFrame(rows)
        result = compute_alpha158(df)
        assert len(result) == 5

    def test_constant_prices(self):
        """价格全相同时不应出现 inf"""
        dates = pd.bdate_range("2023-01-01", periods=30)
        rows = [{"datetime": dt, "symbol": "FLAT", "open": 10.0, "high": 10.0,
                 "low": 10.0, "close": 10.0, "volume": 1e6} for dt in dates]
        df = pd.DataFrame(rows)
        result = compute_alpha158(df)
        factor_cols = [c for c in result.columns if c not in _META_COLS]
        for col in factor_cols:
            assert not np.isinf(result[col]).any(), f"{col} contains inf"

    def test_missing_volume_column_raises(self):
        """缺少必要列应报错"""
        df = pd.DataFrame({"datetime": [pd.Timestamp("2023-01-01")],
                           "symbol": ["A"], "open": [1], "high": [2], "low": [0.5]})
        with pytest.raises((KeyError, ValueError)):
            compute_alpha158(df)


class TestAlpha158Extreme:
    def test_extreme_prices(self, sample_panel):
        """极端价格（如涨停）"""
        df = sample_panel.copy()
        df.loc[df.index[:5], "close"] = 1e6
        result = compute_alpha158(df)
        assert len(result) > 0

    def test_zero_volume(self, sample_panel):
        """0 成交量不应崩溃"""
        df = sample_panel.copy()
        df.loc[df.index[:10], "volume"] = 0.0
        result = compute_alpha158(df)
        assert len(result) > 0
