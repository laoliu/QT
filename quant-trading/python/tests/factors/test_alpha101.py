"""
Alpha101 因子库单元测试

覆盖: 正常计算、选择因子、空数据、极端值、跳过因子、输出格式
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.factors.alpha101 import (
    Alpha101,
    SKIPPED_ALPHAS,
    compute_alpha101,
    get_alpha101_factor_names,
)


# ─── Fixture: 构造 OHLCV + VWAP 面板 ──────────────────────

@pytest.fixture
def sample_panel() -> pd.DataFrame:
    """3 只股票 x 250 日 OHLCV + VWAP 数据（需要长历史以覆盖 adv180 等窗口）"""
    np.random.seed(99)
    dates = pd.bdate_range("2022-01-01", periods=250)
    symbols = ["A", "B", "C"]
    rows = []
    for sym in symbols:
        base = {"A": 20.0, "B": 50.0, "C": 10.0}[sym]
        prices = base + np.cumsum(np.random.randn(250) * 0.5)
        for i, dt in enumerate(dates):
            c = max(prices[i], 0.5)
            o = c + np.random.randn() * 0.2
            o = max(o, 0.1)
            h = max(o, c) + abs(np.random.randn()) * 0.3
            lo = min(o, c) - abs(np.random.randn()) * 0.3
            lo = max(lo, 0.01)
            v = np.random.uniform(5e5, 5e6)
            vwap = (h + lo + c) / 3.0
            rows.append({"datetime": dt, "symbol": sym, "open": o, "high": h,
                         "low": lo, "close": c, "volume": v, "vwap": vwap})
    return pd.DataFrame(rows)


@pytest.fixture
def short_panel() -> pd.DataFrame:
    """短历史面板（仅 20 日），用于测试边界"""
    np.random.seed(77)
    dates = pd.bdate_range("2023-06-01", periods=20)
    rows = []
    for sym in ["X", "Y"]:
        base = 15.0 if sym == "X" else 25.0
        for i, dt in enumerate(dates):
            c = base + np.random.randn() * 0.5
            c = max(c, 0.5)
            o = c + np.random.randn() * 0.1
            o = max(o, 0.1)
            h = max(o, c) + 0.2
            lo = min(o, c) - 0.2
            lo = max(lo, 0.01)
            v = 1e6 + np.random.randn() * 1e5
            vwap = (h + lo + c) / 3.0
            rows.append({"datetime": dt, "symbol": sym, "open": o, "high": h,
                         "low": lo, "close": c, "volume": v, "vwap": vwap})
    return pd.DataFrame(rows)


# ─── 正常计算 ─────────────────────────────────────────────

class TestAlpha101Normal:
    def test_compute_returns_dataframe(self, sample_panel):
        result = compute_alpha101(sample_panel)
        assert isinstance(result, pd.DataFrame)
        assert "datetime" in result.columns
        assert "symbol" in result.columns

    def test_factor_count(self, sample_panel):
        result = compute_alpha101(sample_panel)
        factor_cols = [c for c in result.columns if c.startswith("alpha")]
        assert len(factor_cols) == 82

    def test_factor_names_list(self):
        names = get_alpha101_factor_names()
        assert len(names) == 82
        assert "alpha1" in names
        assert "alpha101" in names
        # 跳过的不应在列表中
        for skip_id in SKIPPED_ALPHAS:
            assert f"alpha{skip_id}" not in names

    def test_factor_names_include_skipped(self):
        names = get_alpha101_factor_names(include_skipped=True)
        assert len(names) == 101

    def test_all_stocks_present(self, sample_panel):
        result = compute_alpha101(sample_panel)
        assert set(result["symbol"].unique()) == {"A", "B", "C"}

    def test_no_inf_values(self, sample_panel):
        result = compute_alpha101(sample_panel)
        factor_cols = [c for c in result.columns if c.startswith("alpha")]
        for col in factor_cols:
            assert not np.isinf(result[col]).any(), f"{col} contains inf"

    def test_select_specific_alphas(self, sample_panel):
        result = compute_alpha101(sample_panel, alphas=[1, 5, 10, 101])
        factor_cols = [c for c in result.columns if c.startswith("alpha")]
        assert sorted(factor_cols) == ["alpha1", "alpha10", "alpha101", "alpha5"]


# ─── 逐个因子烟雾测试 ──────────────────────────────────────

class TestAlpha101Individual:
    """
    每个可实现的 alpha 单独调用并验证:
    - 不抛异常
    - 结果为 Series
    - 长度正确
    """

    IMPLEMENTABLE = sorted(set(range(1, 102)) - SKIPPED_ALPHAS)

    @pytest.fixture(autouse=True)
    def setup(self, sample_panel):
        self.calc = Alpha101(sample_panel)
        self.n = len(sample_panel)

    @pytest.mark.parametrize("alpha_id", IMPLEMENTABLE)
    def test_individual_alpha(self, alpha_id):
        method = getattr(self.calc, f"alpha{alpha_id}")
        result = method()
        assert isinstance(result, pd.Series), f"alpha{alpha_id} should return Series"
        assert len(result) == self.n, f"alpha{alpha_id} length mismatch"


# ─── 边界条件 ─────────────────────────────────────────────

class TestAlpha101Edge:
    def test_short_history_no_crash(self, short_panel):
        """短历史仅 20 日，长窗口因子大量 NaN 但不应报错"""
        result = compute_alpha101(short_panel)
        assert len(result) > 0

    def test_single_stock(self, sample_panel):
        df = sample_panel[sample_panel["symbol"] == "A"].copy()
        result = compute_alpha101(df, alphas=[1, 2, 3, 4, 5])
        assert result["symbol"].unique().tolist() == ["A"]

    def test_constant_prices(self):
        """价格全相同不应出 inf"""
        dates = pd.bdate_range("2023-01-01", periods=30)
        rows = []
        for sym in ["P", "Q"]:
            for dt in dates:
                rows.append({"datetime": dt, "symbol": sym, "open": 10.0,
                             "high": 10.0, "low": 10.0, "close": 10.0,
                             "volume": 1e6, "vwap": 10.0})
        df = pd.DataFrame(rows)
        result = compute_alpha101(df, alphas=[1, 2, 3, 41, 101])
        factor_cols = [c for c in result.columns if c.startswith("alpha")]
        for col in factor_cols:
            assert not np.isinf(result[col]).any(), f"{col} inf with constant prices"

    def test_zero_volume(self, sample_panel):
        """零成交量不应崩溃"""
        df = sample_panel.copy()
        df.loc[df.index[:30], "volume"] = 0.0
        result = compute_alpha101(df, alphas=[1, 12, 20, 43])
        assert len(result) > 0


# ─── 跳过因子 ─────────────────────────────────────────────

class TestAlpha101Skipped:
    def test_skipped_not_computed(self, sample_panel):
        """跳过因子不应出现在compute结果中"""
        result = compute_alpha101(sample_panel)
        for skip_id in SKIPPED_ALPHAS:
            assert f"alpha{skip_id}" not in result.columns

    def test_skipped_set_correct(self):
        """确认 SKIPPED_ALPHAS 是预期集合"""
        expected = {48, 56, 58, 59, 63, 67, 69, 70, 76, 79, 80, 82, 87, 89, 90, 91, 93, 97, 100}
        assert SKIPPED_ALPHAS == expected


# ─── vwap 自动计算 ────────────────────────────────────────

class TestAlpha101VwapFallback:
    def test_auto_vwap_when_missing(self, sample_panel):
        """去除 vwap 列后应自动推算"""
        df = sample_panel.drop(columns=["vwap"])
        result = compute_alpha101(df, alphas=[1, 2, 41, 101])
        assert len(result) > 0
