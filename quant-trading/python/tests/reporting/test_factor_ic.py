"""因子 IC 分析测试

覆盖：
- 截面 IC 计算（正常、并列、NaN、不足样本）
- 面板 IC 计算（多日期分组）
- IC 衰减分析
- IC 半衰期估计
- 结果序列化
- 边界条件
"""

import math

import pytest

from aipha.reporting.factor_ic import (
    FactorICAnalyzer,
    FactorICResult,
    ICDecayResult,
    _estimate_half_life,
    _pearson,
    _rank,
    _simple_ols,
)


# =========================================================================
# _rank 工具函数
# =========================================================================

class TestRank:
    def test_ascending(self):
        assert _rank([1.0, 2.0, 3.0]) == [1.0, 2.0, 3.0]

    def test_descending(self):
        assert _rank([3.0, 2.0, 1.0]) == [3.0, 2.0, 1.0]

    def test_ties(self):
        ranks = _rank([1.0, 2.0, 2.0, 4.0])
        assert ranks[0] == 1.0
        assert ranks[1] == 2.5  # 平均排名
        assert ranks[2] == 2.5
        assert ranks[3] == 4.0

    def test_all_same(self):
        ranks = _rank([5.0, 5.0, 5.0])
        assert all(r == 2.0 for r in ranks)

    def test_single_element(self):
        assert _rank([42.0]) == [1.0]


# =========================================================================
# _pearson 工具函数
# =========================================================================

class TestPearson:
    def test_perfect_positive(self):
        assert abs(_pearson([1, 2, 3, 4], [10, 20, 30, 40]) - 1.0) < 1e-10

    def test_perfect_negative(self):
        assert abs(_pearson([1, 2, 3, 4], [40, 30, 20, 10]) - (-1.0)) < 1e-10

    def test_zero_correlation(self):
        r = _pearson([1, 2, 3, 4], [1, -1, 1, -1])
        assert abs(r) < 0.5  # 近似零

    def test_insufficient_data(self):
        assert math.isnan(_pearson([1.0], [2.0]))

    def test_constant_x(self):
        # 方差为零
        assert _pearson([5, 5, 5], [1, 2, 3]) == 0.0


# =========================================================================
# _simple_ols 工具函数
# =========================================================================

class TestSimpleOLS:
    def test_perfect_fit(self):
        slope, intercept, r2 = _simple_ols([0, 1, 2], [1, 3, 5])
        assert abs(slope - 2.0) < 1e-10
        assert abs(intercept - 1.0) < 1e-10
        assert abs(r2 - 1.0) < 1e-10

    def test_single_point(self):
        slope, intercept, r2 = _simple_ols([1.0], [2.0])
        assert slope == 0.0
        assert r2 == 0.0


# =========================================================================
# FactorICAnalyzer.compute_ic
# =========================================================================

class TestComputeIC:
    def setup_method(self):
        self.analyzer = FactorICAnalyzer()

    def test_perfect_rank_correlation(self):
        factor = [1.0, 2.0, 3.0, 4.0, 5.0]
        returns = [0.01, 0.02, 0.03, 0.04, 0.05]
        ic = self.analyzer.compute_ic(factor, returns)
        assert abs(ic - 1.0) < 1e-10

    def test_inverse_rank_correlation(self):
        factor = [5.0, 4.0, 3.0, 2.0, 1.0]
        returns = [0.01, 0.02, 0.03, 0.04, 0.05]
        ic = self.analyzer.compute_ic(factor, returns)
        assert abs(ic - (-1.0)) < 1e-10

    def test_with_nan_values(self):
        factor = [1.0, float("nan"), 3.0, 4.0, 5.0]
        returns = [0.01, 0.02, 0.03, 0.04, 0.05]
        ic = self.analyzer.compute_ic(factor, returns)
        assert math.isfinite(ic)

    def test_insufficient_samples(self):
        ic = self.analyzer.compute_ic([1.0, 2.0], [0.01, 0.02])
        assert math.isnan(ic)

    def test_length_mismatch(self):
        ic = self.analyzer.compute_ic([1.0, 2.0, 3.0], [0.01, 0.02])
        assert math.isnan(ic)

    def test_all_nan(self):
        ic = self.analyzer.compute_ic(
            [float("nan")] * 5, [0.01, 0.02, 0.03, 0.04, 0.05]
        )
        assert math.isnan(ic)

    def test_with_inf(self):
        factor = [1.0, float("inf"), 3.0, 4.0, 5.0]
        returns = [0.01, 0.02, 0.03, 0.04, 0.05]
        ic = self.analyzer.compute_ic(factor, returns)
        assert math.isfinite(ic)

    def test_ties(self):
        factor = [1.0, 1.0, 2.0, 3.0, 4.0]
        returns = [0.01, 0.015, 0.02, 0.03, 0.04]
        ic = self.analyzer.compute_ic(factor, returns)
        assert -1.0 <= ic <= 1.0


# =========================================================================
# FactorICAnalyzer.compute_panel_ic
# =========================================================================

class TestComputePanelIC:
    def setup_method(self):
        self.analyzer = FactorICAnalyzer()

    def _make_panel(self, n_dates=10, n_stocks=20):
        """构造测试面板数据。"""
        panel = []
        for d in range(n_dates):
            for s in range(n_stocks):
                fv = (s + 1) * 1.0 + d * 0.1  # 单调递增
                rv = (s + 1) * 0.01 + d * 0.001
                panel.append({
                    "datetime": f"2024-01-{d+1:02d}",
                    "symbol": f"SH{s:04d}",
                    "momentum": fv,
                    "fwd_ret": rv,
                })
        return panel

    def test_basic(self):
        panel = self._make_panel()
        result = self.analyzer.compute_panel_ic(
            panel, "momentum", "fwd_ret"
        )
        assert isinstance(result, FactorICResult)
        assert result.factor_name == "momentum"
        assert result.n_periods == 10
        # 完美正相关
        assert result.ic_mean > 0.9

    def test_no_data(self):
        result = self.analyzer.compute_panel_ic([], "x", "y")
        assert result.n_periods == 0
        assert result.ic_mean == 0.0

    def test_single_date(self):
        panel = self._make_panel(n_dates=1, n_stocks=10)
        result = self.analyzer.compute_panel_ic(
            panel, "momentum", "fwd_ret"
        )
        assert result.n_periods == 1

    def test_few_stocks_per_date(self):
        """每日只有 2 只股票，IC 应全部为 NaN。"""
        panel = self._make_panel(n_dates=5, n_stocks=2)
        result = self.analyzer.compute_panel_ic(
            panel, "momentum", "fwd_ret"
        )
        # 2 stocks < 3 minimum
        assert result.n_periods == 0

    def test_missing_fields(self):
        """面板数据缺少因子列名。"""
        panel = [{"datetime": "2024-01-01", "symbol": "SH0001"}]
        result = self.analyzer.compute_panel_ic(
            panel, "nonexistent", "fwd_ret"
        )
        assert result.n_periods == 0


# =========================================================================
# FactorICResult 属性
# =========================================================================

class TestFactorICResult:
    def test_properties(self):
        result = FactorICResult(
            factor_name="test",
            forward_period=5,
            ic_series=[0.05, 0.03, 0.04, 0.06, -0.01],
            dates=["d1", "d2", "d3", "d4", "d5"],
        )
        assert abs(result.ic_mean - 0.034) < 0.001
        assert result.ic_std > 0
        assert result.ic_ir != 0
        assert result.ic_positive_rate == 0.8
        assert result.n_periods == 5
        assert result.ic_abs_mean > 0

    def test_empty_series(self):
        result = FactorICResult("empty", 1, [], [])
        assert result.ic_mean == 0.0
        assert result.ic_std == 0.0
        assert result.ic_ir == 0.0
        assert result.ic_positive_rate == 0.0
        assert result.n_periods == 0

    def test_nan_in_series(self):
        result = FactorICResult(
            "test", 1,
            [0.05, float("nan"), 0.03],
            ["d1", "d2", "d3"],
        )
        assert result.n_periods == 2
        assert math.isfinite(result.ic_mean)

    def test_to_dict(self):
        result = FactorICResult("alpha1", 5, [0.04, 0.03], ["d1", "d2"])
        d = result.to_dict()
        assert d["factor_name"] == "alpha1"
        assert d["forward_period"] == 5
        assert "ic_mean" in d
        assert "ic_ir" in d

    def test_single_value_series(self):
        result = FactorICResult("test", 1, [0.05], ["d1"])
        assert result.ic_std == 0.0
        assert result.ic_ir == 0.0


# =========================================================================
# IC 衰减分析
# =========================================================================

class TestICDecay:
    def setup_method(self):
        self.analyzer = FactorICAnalyzer(forward_periods=[1, 5, 10, 20])

    def test_decay_result(self):
        """测试 IC 衰减结果结构。"""
        # 构造面板数据，带不同 forward return 列
        panel = []
        for d in range(30):
            for s in range(30):
                row = {
                    "datetime": f"2024-01-{d+1:02d}",
                    "factor_x": (s + 1) * 1.0,
                    "fwd_ret_1": (s + 1) * 0.001,
                    "fwd_ret_5": (s + 1) * 0.004,
                    "fwd_ret_10": (s + 1) * 0.006,
                    "fwd_ret_20": (s + 1) * 0.008,
                }
                panel.append(row)

        result = self.analyzer.compute_ic_decay(
            panel, "factor_x", "fwd_ret_{period}"
        )
        assert isinstance(result, ICDecayResult)
        assert result.factor_name == "factor_x"
        assert len(result.periods) == 4
        assert len(result.ic_means) == 4

    def test_decay_to_dict(self):
        result = ICDecayResult(
            factor_name="test",
            periods=[1, 5],
            ic_means=[0.05, 0.03],
            ic_irs=[2.0, 1.5],
            half_life=10.0,
            decay_slope=-0.001,
        )
        d = result.to_dict()
        assert d["factor_name"] == "test"
        assert d["half_life"] == 10.0
        assert len(d["periods"]) == 2

    def test_decay_no_data(self):
        result = self.analyzer.compute_ic_decay(
            [], "factor_x", "fwd_ret_{period}"
        )
        assert len(result.periods) == 0


# =========================================================================
# IC 半衰期
# =========================================================================

class TestHalfLife:
    def test_decaying_ic(self):
        # IC 从高到低
        periods = [1, 5, 10, 20]
        ic_means = [0.08, 0.05, 0.02, 0.005]
        hl = _estimate_half_life(periods, ic_means)
        assert hl is not None
        assert hl > 0

    def test_no_decay(self):
        # IC 不衰减（递增）
        periods = [1, 5, 10, 20]
        ic_means = [0.02, 0.03, 0.05, 0.08]
        hl = _estimate_half_life(periods, ic_means)
        assert hl is None

    def test_flat_ic(self):
        # IC 平坦
        periods = [1, 5, 10, 20]
        ic_means = [0.05, 0.05, 0.05, 0.05]
        hl = _estimate_half_life(periods, ic_means)
        # slope = 0, 不衰减
        assert hl is None

    def test_single_period(self):
        hl = _estimate_half_life([1], [0.05])
        assert hl is None

    def test_zero_ic(self):
        hl = _estimate_half_life([1, 5], [0.0, 0.0])
        assert hl is None

    def test_none_half_life_to_dict(self):
        result = ICDecayResult(
            "test", [1], [0.05], [1.0], None, 0.0
        )
        d = result.to_dict()
        assert d["half_life"] is None


# =========================================================================
# summary_table
# =========================================================================

class TestSummaryTable:
    def test_basic(self):
        analyzer = FactorICAnalyzer()
        results = [
            FactorICResult("f1", 1, [0.05, 0.03], ["d1", "d2"]),
            FactorICResult("f2", 1, [0.01, -0.02], ["d1", "d2"]),
        ]
        table = analyzer.summary_table(results)
        assert len(table) == 2
        assert table[0]["factor_name"] == "f1"
        assert table[1]["factor_name"] == "f2"
