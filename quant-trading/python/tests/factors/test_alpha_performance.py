"""Alpha158/Alpha101 性能优化测试

验证 DataFrame 碎片化修复后因子计算结果正确性。
"""

import warnings

import numpy as np
import pandas as pd
import pytest

from aipha.factors.alpha158 import Alpha158, Alpha158Config, compute_alpha158
from aipha.factors.alpha101 import Alpha101, SKIPPED_ALPHAS


def _make_ohlcv(n_symbols=3, n_days=70):
    """构造测试 OHLCV 面板数据。"""
    rng = np.random.RandomState(42)
    rows = []
    for sym_idx in range(n_symbols):
        base_price = 10.0 + sym_idx * 5
        for d in range(n_days):
            c = base_price + rng.randn() * 0.5
            h = c + abs(rng.randn()) * 0.3
            lo = c - abs(rng.randn()) * 0.3
            o = c + rng.randn() * 0.2
            v = max(1000 + rng.randn() * 200, 100)
            rows.append({
                "datetime": f"2024-01-{d+1:02d}" if d < 31 else f"2024-02-{d-30:02d}",
                "symbol": f"SH{sym_idx:04d}",
                "open": max(o, 0.1),
                "high": max(h, 0.1),
                "low": max(lo, 0.01),
                "close": max(c, 0.1),
                "volume": max(v, 1),
            })
    return pd.DataFrame(rows)


class TestAlpha158Performance:
    """Alpha158 碎片化修复后结果验证。"""

    def test_no_fragmentation_warning(self):
        """修复后不应触发 PerformanceWarning。"""
        df = _make_ohlcv(n_symbols=2, n_days=65)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = compute_alpha158(df)
            perf_warnings = [
                x for x in w
                if issubclass(x.category, pd.errors.PerformanceWarning)
            ]
            assert len(perf_warnings) == 0, (
                f"仍有 PerformanceWarning: {[str(x.message) for x in perf_warnings]}"
            )

    def test_result_columns_unchanged(self):
        """修复后因子列名和数量不变。"""
        df = _make_ohlcv()
        result = compute_alpha158(df)
        calc = Alpha158()
        expected_names = calc.factor_names
        for name in expected_names:
            assert name in result.columns, f"缺少因子列: {name}"

    def test_result_values_finite(self):
        """因子值应为有限数（非全 NaN）。"""
        df = _make_ohlcv(n_symbols=2, n_days=65)
        result = compute_alpha158(df)
        factor_cols = [c for c in result.columns if c not in [
            "datetime", "symbol", "open", "high", "low", "close", "volume"
        ]]
        # 至少部分因子应有非 NaN 值
        non_nan_counts = result[factor_cols].notna().sum()
        assert (non_nan_counts > 0).any(), "所有因子全 NaN"

    def test_original_columns_preserved(self):
        """原始列（OHLCV）应保留。"""
        df = _make_ohlcv()
        result = compute_alpha158(df)
        for col in ["datetime", "symbol", "open", "high", "low", "close", "volume"]:
            assert col in result.columns

    def test_candlestick_only(self):
        """仅计算 K 线因子。"""
        df = _make_ohlcv(n_symbols=1, n_days=10)
        config = Alpha158Config(
            include_candlestick=True,
            include_price_relative=False,
            include_time_series=False,
        )
        calc = Alpha158(config)
        result = calc.compute(df)
        assert "kmid" in result.columns
        assert "roc_5" not in result.columns


class TestAlpha101Performance:
    """Alpha101 碎片化修复后结果验证。"""

    def test_no_fragmentation_warning(self):
        """修复后不应触发 PerformanceWarning。"""
        df = _make_ohlcv(n_symbols=3, n_days=65)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            calc = Alpha101(df)
            result = calc.compute(alphas=[1, 2, 3, 4, 5])
            perf_warnings = [
                x for x in w
                if issubclass(x.category, pd.errors.PerformanceWarning)
            ]
            assert len(perf_warnings) == 0

    def test_result_structure(self):
        """Alpha101 output 结构正确。"""
        df = _make_ohlcv(n_symbols=2, n_days=65)
        calc = Alpha101(df)
        result = calc.compute(alphas=[1, 2, 3])
        assert "datetime" in result.columns
        assert "symbol" in result.columns
        assert "alpha1" in result.columns
        assert "alpha2" in result.columns
        assert "alpha3" in result.columns

    def test_skipped_alphas_excluded(self):
        """跳过的因子不应出现在结果中。"""
        df = _make_ohlcv(n_symbols=2, n_days=30)
        calc = Alpha101(df)
        result = calc.compute()
        for i in SKIPPED_ALPHAS:
            assert f"alpha{i}" not in result.columns

    def test_subset_alphas(self):
        """指定因子子集。"""
        df = _make_ohlcv(n_symbols=2, n_days=65)
        calc = Alpha101(df)
        result = calc.compute(alphas=[1, 5, 10])
        assert "alpha1" in result.columns
        assert "alpha5" in result.columns
        assert "alpha10" in result.columns
        assert "alpha2" not in result.columns

    def test_exception_in_alpha_fills_nan(self):
        """因子计算异常时填 NaN 而非崩溃。"""
        df = _make_ohlcv(n_symbols=1, n_days=5)  # 数据太少可能引发部分因子异常
        calc = Alpha101(df)
        result = calc.compute(alphas=[1])
        # 不应崩溃
        assert "alpha1" in result.columns
