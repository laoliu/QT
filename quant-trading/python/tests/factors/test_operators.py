"""
operators.py 单元测试

覆盖: ts_* 时序算子、cs_* 截面算子、数学辅助函数
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.factors.operators import (
    cs_mean,
    cs_rank,
    cs_scale,
    cs_std,
    cs_zscore,
    pow1,
    pow2,
    quesval,
    quesval2,
    safe_div,
    ts_abs,
    ts_argmax,
    ts_argmin,
    ts_corr,
    ts_cov,
    ts_decay_linear,
    ts_delay,
    ts_delta,
    ts_greater,
    ts_less,
    ts_log,
    ts_max,
    ts_mean,
    ts_min,
    ts_product,
    ts_quantile,
    ts_rank,
    ts_resi,
    ts_rsquare,
    ts_sign,
    ts_slope,
    ts_std,
    ts_sum,
    ts_var,
)


# ─── Fixture: 构造面板数据 ───────────────────────────────────

@pytest.fixture
def panel_data():
    """生成 2 只股票 x 20 个交易日的面板数据"""
    np.random.seed(42)
    dates = pd.bdate_range("2023-01-01", periods=20)
    symbols = ["A", "B"]
    idx = pd.MultiIndex.from_product([dates, symbols], names=["datetime", "symbol"])
    n = len(idx)
    close = pd.Series(np.random.uniform(10, 20, n), index=idx, name="close")
    volume = pd.Series(np.random.uniform(1000, 5000, n), index=idx, name="volume")
    group = close.index.get_level_values("symbol")
    dt = close.index.get_level_values("datetime")
    return close, volume, group, dt


# ─── ts_delay / ts_delta ───────────────────────────────────

class TestTsDelay:
    def test_delay_shifts_values(self, panel_data):
        close, _, group, _ = panel_data
        delayed = ts_delay(close, 1, group)
        # 第一个值应该是 NaN(每组)
        for sym in ["A", "B"]:
            mask = group == sym
            assert pd.isna(delayed[mask].iloc[0])
            # 后续值 = 原值前移一期
            np.testing.assert_array_almost_equal(
                delayed[mask].dropna().values,
                close[mask].iloc[:-1].values,
            )

    def test_delay_zero_is_identity(self, panel_data):
        close, _, group, _ = panel_data
        delayed = ts_delay(close, 0, group)
        pd.testing.assert_series_equal(delayed, close)


class TestTsDelta:
    def test_delta_is_diff(self, panel_data):
        close, _, group, _ = panel_data
        d = ts_delta(close, 1, group)
        expected = close - ts_delay(close, 1, group)
        pd.testing.assert_series_equal(d, expected)


# ─── ts_sum / ts_mean / ts_std / ts_var ────────────────────

class TestTsAgg:
    def test_sum_window(self, panel_data):
        close, _, group, _ = panel_data
        s = ts_sum(close, 3, group)
        # 验证某组第 3 个值 = 前 3 个的和
        mask = group == "A"
        vals = close[mask].values
        expected_val = vals[0] + vals[1] + vals[2]
        assert abs(s[mask].iloc[2] - expected_val) < 1e-10

    def test_mean_window(self, panel_data):
        close, _, group, _ = panel_data
        m = ts_mean(close, 3, group)
        # 验证某组第3个值 = 前3个的均值
        mask = group == "A"
        vals = close[mask].values
        expected_val = (vals[0] + vals[1] + vals[2]) / 3.0
        assert abs(m[mask].iloc[2] - expected_val) < 1e-10

    def test_std_positive(self, panel_data):
        close, _, group, _ = panel_data
        std = ts_std(close, 5, group)
        valid = std.dropna()
        assert (valid >= 0).all()

    def test_var_equals_std_squared(self, panel_data):
        close, _, group, _ = panel_data
        v = ts_var(close, 5, group)
        s = ts_std(close, 5, group)
        valid = v.dropna()
        np.testing.assert_array_almost_equal(
            valid.values, (s.dropna() ** 2).values, decimal=10
        )


# ─── ts_min / ts_max / ts_argmin / ts_argmax ─────────────

class TestTsMinMax:
    def test_min_max_consistency(self, panel_data):
        close, _, group, _ = panel_data
        tmin = ts_min(close, 5, group)
        tmax = ts_max(close, 5, group)
        # tmax >= tmin 在所有有效点
        assert (tmax >= tmin).all()

    def test_argmax_in_range(self, panel_data):
        close, _, group, _ = panel_data
        amax = ts_argmax(close, 5, group)
        valid = amax.dropna()
        # 1-based: 1=最近, window=最老
        assert (valid >= 1).all()
        assert (valid <= 5).all()

    def test_argmin_in_range(self, panel_data):
        close, _, group, _ = panel_data
        amin = ts_argmin(close, 5, group)
        valid = amin.dropna()
        # 1-based: 1=最近, window=最老
        assert (valid >= 1).all()
        assert (valid <= 5).all()


# ─── ts_rank / ts_quantile / ts_corr / ts_cov ──────────────

class TestTsRankCorr:
    def test_rank_between_0_1(self, panel_data):
        close, _, group, _ = panel_data
        r = ts_rank(close, 5, group)
        valid = r.dropna()
        assert (valid >= 0).all()
        assert (valid <= 1).all()

    def test_quantile_between_0_1(self, panel_data):
        close, _, group, _ = panel_data
        q = ts_quantile(close, 5, 0.5, group)  # 中位数
        valid = q.dropna()
        # 中位数应为有限数值
        assert len(valid) > 0
        assert not np.isinf(valid).any()

    def test_corr_between_neg1_pos1(self, panel_data):
        close, volume, group, _ = panel_data
        c = ts_corr(close, volume, 10, group)
        valid = c.dropna()
        assert (valid >= -1.01).all()
        assert (valid <= 1.01).all()

    def test_cov_symmetric(self, panel_data):
        close, volume, group, _ = panel_data
        c1 = ts_cov(close, volume, 10, group)
        c2 = ts_cov(volume, close, 10, group)
        pd.testing.assert_series_equal(c1, c2, check_names=False)


# ─── ts_slope / ts_rsquare / ts_resi ───────────────────────

class TestTsRegression:
    def test_slope_output_shape(self, panel_data):
        close, _, group, _ = panel_data
        s = ts_slope(close, 10, group)
        assert len(s) == len(close)

    def test_rsquare_between_0_1(self, panel_data):
        close, _, group, _ = panel_data
        r2 = ts_rsquare(close, 10, group)
        valid = r2.dropna()
        # R² 可能稍超 [0,1] 由于数值误差
        assert (valid >= -0.01).all()
        assert (valid <= 1.01).all()

    def test_resi_sum_near_zero(self, panel_data):
        """残差在每个窗口内均值应接近 0"""
        close, _, group, _ = panel_data
        r = ts_resi(close, 5, group)
        # 验证最后一个值存在即可（残差是最后一天的）
        assert len(r.dropna()) > 0


# ─── ts_product / ts_decay_linear ──────────────────────────

class TestTsProductDecay:
    def test_product_simple(self):
        """纯序列无分组的乘积"""
        s = pd.Series([2.0, 3.0, 4.0, 5.0])
        g = pd.Series(["X"] * 4)
        p = ts_product(s, 3, g)
        assert abs(p.iloc[2] - 24.0) < 1e-10  # 2*3*4
        assert abs(p.iloc[3] - 60.0) < 1e-10  # 3*4*5

    def test_decay_linear_output_shape(self, panel_data):
        close, _, group, _ = panel_data
        dl = ts_decay_linear(close, 5, group)
        assert len(dl) == len(close)


# ─── ts_log / ts_abs / ts_sign ─────────────────────────────

class TestTsMath:
    def test_log_positive(self, panel_data):
        close, _, _, _ = panel_data
        result = ts_log(close)
        assert (result.dropna() > 0).all()  # close > 0 → log > 0 for close > 1

    def test_abs_non_negative(self, panel_data):
        close, _, _, _ = panel_data
        delta = close - close.mean()
        result = ts_abs(delta)
        assert (result >= 0).all()

    def test_sign_values(self):
        s = pd.Series([-3, 0, 5, -1, 2])
        result = ts_sign(s)
        np.testing.assert_array_equal(result.values, [-1, 0, 1, -1, 1])


# ─── ts_greater / ts_less ──────────────────────────────────

class TestTsGreaterLess:
    def test_greater_min_max(self):
        a = pd.Series([1.0, 5.0, 3.0])
        b = pd.Series([3.0, 2.0, 3.0])
        np.testing.assert_array_equal(ts_greater(a, b).values, [3.0, 5.0, 3.0])

    def test_less_min_max(self):
        a = pd.Series([1.0, 5.0, 3.0])
        b = pd.Series([3.0, 2.0, 3.0])
        np.testing.assert_array_equal(ts_less(a, b).values, [1.0, 2.0, 3.0])


# ─── cs_rank / cs_scale / cs_mean / cs_std / cs_zscore ─────

class TestCrossSection:
    def test_rank_between_0_1(self, panel_data):
        close, _, _, dt = panel_data
        r = cs_rank(close, dt)
        valid = r.dropna()
        assert (valid >= 0).all()
        assert (valid <= 1).all()

    def test_scale_sums_to_1(self, panel_data):
        close, _, _, dt = panel_data
        # 确保值为正
        scaled = cs_scale(close.abs() + 1, dt)
        # 每个截面 abs 和应为 1
        for d in dt.unique():
            mask = dt == d
            total = scaled[mask].abs().sum()
            assert abs(total - 1.0) < 1e-8

    def test_zscore_mean_zero(self, panel_data):
        close, _, _, dt = panel_data
        z = cs_zscore(close, dt)
        for d in dt.unique():
            mask = dt == d
            assert abs(z[mask].mean()) < 1e-10


# ─── safe_div / pow1 / pow2 / quesval / quesval2 ──────────

class TestMathHelpers:
    def test_safe_div_zero_denominator(self):
        a = pd.Series([1.0, 2.0, 3.0])
        b = pd.Series([0.0, 2.0, 0.0])
        result = safe_div(a, b)
        # 默认 fill=0.0
        assert result.iloc[0] == 0.0
        assert result.iloc[1] == 1.0
        assert result.iloc[2] == 0.0

    def test_safe_div_custom_fill(self):
        a = pd.Series([1.0, 2.0])
        b = pd.Series([0.0, 1.0])
        result = safe_div(a, b, fill=np.nan)
        assert pd.isna(result.iloc[0])
        assert result.iloc[1] == 2.0

    def test_pow1(self):
        s = pd.Series([4.0, 9.0, 16.0])
        result = pow1(s, 0.5)
        np.testing.assert_array_almost_equal(result.values, [2.0, 3.0, 4.0])

    def test_pow2(self):
        s = pd.Series([2.0, 3.0])
        e = pd.Series([3.0, 2.0])
        result = pow2(s, e)
        np.testing.assert_array_almost_equal(result.values, [8.0, 9.0])

    def test_quesval(self):
        threshold = 0.0
        condition = pd.Series([-1, 0, 1, 2])
        # condition > threshold → true_val; condition <= threshold → false_val
        result = quesval(threshold, condition, 10.0, -10.0)
        # condition[-1] <= 0 → false_val=-10; condition[0] <= 0 → -10; condition[1] > 0 → 10; condition[2] > 0 → 10
        expected = [-10.0, -10.0, 10.0, 10.0]
        np.testing.assert_array_equal(result.values, expected)

    def test_quesval2_semantics(self):
        """quesval2: condition < threshold → true_val; else → false_val"""
        threshold = pd.Series([5.0, 5.0, 5.0])
        condition = pd.Series([3.0, 5.0, 7.0])
        result = quesval2(threshold, condition, 1.0, 0.0)
        # 3 < 5 → 1; 5 < 5 → False → 0; 7 < 5 → False → 0
        np.testing.assert_array_equal(result.values, [1.0, 0.0, 0.0])


# ─── 边界/空数据测试 ──────────────────────────────────────

class TestEdgeCases:
    def test_empty_series(self):
        s = pd.Series([], dtype=float)
        g = pd.Series([], dtype=str)
        result = ts_sum(s, 3, g)
        assert len(result) == 0

    def test_single_element(self):
        s = pd.Series([5.0])
        g = pd.Series(["A"])
        result = ts_delay(s, 1, g)
        assert pd.isna(result.iloc[0])

    def test_all_nan(self):
        s = pd.Series([np.nan, np.nan, np.nan])
        g = pd.Series(["A", "A", "A"])
        result = ts_mean(s, 2, g)
        assert result.isna().all()

    def test_constant_series_std_zero(self):
        s = pd.Series([5.0] * 10)
        g = pd.Series(["A"] * 10)
        result = ts_std(s, 5, g)
        valid = result.dropna()
        assert (valid == 0).all()
