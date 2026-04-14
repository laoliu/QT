"""
策略分散化分析器测试

测试覆盖：
- 相关性矩阵计算
- 分散化比率计算
- 边际分散化贡献评估
- 冗余策略检测
- 最优子集选择
- 报告生成
- 边界与异常输入
"""

from datetime import date, timedelta

import numpy as np
import pytest

from aipha.strategy.diversification import (
    DiversificationAnalyzer,
    DiversificationReport,
    MarginalDiversification,
    StrategyPairCorrelation,
)


# ============================================================
# 测试辅助
# ============================================================


def _generate_nav(
    n_days: int = 120,
    annual_return: float = 0.10,
    volatility: float = 0.20,
    seed: int = 42,
    start_date: date | None = None,
) -> dict[date, float]:
    """生成模拟净值序列"""
    rng = np.random.default_rng(seed)
    daily_ret = annual_return / 252
    daily_vol = volatility / np.sqrt(252)

    d0 = start_date or date(2023, 1, 1)
    nav = {d0: 1.0}
    val = 1.0

    for i in range(1, n_days):
        d = d0 + timedelta(days=i)
        val *= 1 + daily_ret + daily_vol * rng.standard_normal()
        nav[d] = val

    return nav


def _generate_correlated_nav(
    base_nav: dict[date, float],
    correlation: float = 0.5,
    seed: int = 99,
) -> dict[date, float]:
    """基于基准 NAV 生成相关的 NAV 序列"""
    rng = np.random.default_rng(seed)
    dates = sorted(base_nav.keys())

    # 提取基准收益
    base_rets = []
    for i in range(1, len(dates)):
        prev = base_nav[dates[i - 1]]
        curr = base_nav[dates[i]]
        base_rets.append((curr / prev - 1) if prev > 0 else 0.0)

    base_rets = np.array(base_rets)

    # 生成相关收益：r_new = corr * r_base + sqrt(1-corr^2) * noise
    noise = rng.standard_normal(len(base_rets)) * np.std(base_rets)
    new_rets = correlation * base_rets + np.sqrt(1 - correlation**2) * noise

    nav = {dates[0]: 1.0}
    val = 1.0
    for i, d in enumerate(dates[1:]):
        val *= 1 + new_rets[i]
        nav[d] = val

    return nav


# ============================================================
# 测试类
# ============================================================


class TestDiversificationAnalyzerBasic:
    """基础功能测试"""

    def test_add_and_remove_strategy(self):
        """测试添加与移除策略"""
        analyzer = DiversificationAnalyzer()
        nav = _generate_nav(seed=1)
        analyzer.add_strategy("strat_a", nav)

        assert "strat_a" in analyzer.strategy_names
        assert len(analyzer.strategy_names) == 1

        analyzer.remove_strategy("strat_a")
        assert len(analyzer.strategy_names) == 0

    def test_add_short_nav_raises(self):
        """NAV 序列太短应抛出 ValueError"""
        analyzer = DiversificationAnalyzer()
        with pytest.raises(ValueError, match="太短"):
            analyzer.add_strategy("bad", {date(2023, 1, 1): 1.0})

    def test_strategy_names_order(self):
        """策略名称按添加顺序返回"""
        analyzer = DiversificationAnalyzer()
        analyzer.add_strategy("c", _generate_nav(seed=3))
        analyzer.add_strategy("a", _generate_nav(seed=1))
        analyzer.add_strategy("b", _generate_nav(seed=2))

        assert analyzer.strategy_names == ["c", "a", "b"]


class TestCorrelationMatrix:
    """相关性矩阵测试"""

    def test_single_strategy_identity(self):
        """单策略相关性矩阵为 [[1.0]]"""
        analyzer = DiversificationAnalyzer()
        analyzer.add_strategy("only", _generate_nav())
        names, corr = analyzer.compute_correlation_matrix()

        assert names == ["only"]
        assert corr.shape == (1, 1)
        np.testing.assert_almost_equal(corr[0, 0], 1.0)

    def test_two_uncorrelated_strategies(self):
        """两个不同种子的策略相关性接近 0"""
        analyzer = DiversificationAnalyzer()
        analyzer.add_strategy("a", _generate_nav(n_days=500, seed=1))
        analyzer.add_strategy("b", _generate_nav(n_days=500, seed=99))

        names, corr = analyzer.compute_correlation_matrix()

        assert corr.shape == (2, 2)
        # 对角线 == 1.0
        np.testing.assert_almost_equal(corr[0, 0], 1.0)
        np.testing.assert_almost_equal(corr[1, 1], 1.0)
        # 非对角线接近 0（允许随机波动）
        assert abs(corr[0, 1]) < 0.2

    def test_perfectly_correlated_strategy(self):
        """完全正相关（相同 NAV）"""
        analyzer = DiversificationAnalyzer()
        nav = _generate_nav(seed=42)
        analyzer.add_strategy("a", nav)
        analyzer.add_strategy("b", dict(nav))  # 相同的 NAV

        _, corr = analyzer.compute_correlation_matrix()
        np.testing.assert_almost_equal(corr[0, 1], 1.0, decimal=10)

    def test_high_correlation_detected(self):
        """高相关性策略对应被检测"""
        analyzer = DiversificationAnalyzer()
        base = _generate_nav(n_days=200, seed=10)
        correlated = _generate_correlated_nav(base, correlation=0.9, seed=20)

        analyzer.add_strategy("base", base)
        analyzer.add_strategy("corr", correlated)

        _, corr = analyzer.compute_correlation_matrix()
        # 应有较高相关性
        assert corr[0, 1] > 0.5

    def test_three_strategy_matrix_shape(self):
        """3 策略矩阵为 (3,3) 且对称"""
        analyzer = DiversificationAnalyzer()
        for i in range(3):
            analyzer.add_strategy(f"s{i}", _generate_nav(seed=i * 10))

        names, corr = analyzer.compute_correlation_matrix()
        assert len(names) == 3
        assert corr.shape == (3, 3)
        # 对称性
        np.testing.assert_array_almost_equal(corr, corr.T)


class TestPairwiseCorrelations:
    """两两相关性测试"""

    def test_two_strategy_one_pair(self):
        """2 策略生成 1 个配对"""
        analyzer = DiversificationAnalyzer()
        analyzer.add_strategy("x", _generate_nav(seed=1))
        analyzer.add_strategy("y", _generate_nav(seed=2))

        pairs = analyzer.compute_pairwise_correlations()
        assert len(pairs) == 1
        assert pairs[0].strategy_a == "x"
        assert pairs[0].strategy_b == "y"

    def test_three_strategy_three_pairs(self):
        """3 策略生成 3 个配对"""
        analyzer = DiversificationAnalyzer()
        for i in range(3):
            analyzer.add_strategy(f"s{i}", _generate_nav(seed=i))

        pairs = analyzer.compute_pairwise_correlations()
        assert len(pairs) == 3

    def test_redundancy_flag(self):
        """冗余标记基于阈值"""
        analyzer = DiversificationAnalyzer(redundancy_threshold=0.5)
        nav = _generate_nav(seed=42, n_days=200)
        # 高相关
        correlated = _generate_correlated_nav(nav, correlation=0.95, seed=50)
        analyzer.add_strategy("a", nav)
        analyzer.add_strategy("b", correlated)

        pairs = analyzer.compute_pairwise_correlations()
        assert len(pairs) == 1
        # 因为相关性很高，is_redundant 应该为 True
        assert pairs[0].correlation > 0.5
        assert pairs[0].is_redundant is True


class TestDiversificationRatio:
    """分散化比率测试"""

    def test_single_strategy_ratio_one(self):
        """单策略分散化比率为 1.0"""
        analyzer = DiversificationAnalyzer()
        analyzer.add_strategy("solo", _generate_nav())
        dr = analyzer.compute_diversification_ratio()
        np.testing.assert_almost_equal(dr, 1.0)

    def test_identical_strategies_ratio_one(self):
        """完全相同的策略分散化比率为 1.0"""
        analyzer = DiversificationAnalyzer()
        nav = _generate_nav(seed=42)
        analyzer.add_strategy("a", nav)
        analyzer.add_strategy("b", dict(nav))

        dr = analyzer.compute_diversification_ratio()
        np.testing.assert_almost_equal(dr, 1.0, decimal=5)

    def test_uncorrelated_strategies_high_ratio(self):
        """不相关策略分散化比率 > 1"""
        analyzer = DiversificationAnalyzer()
        analyzer.add_strategy("a", _generate_nav(n_days=400, seed=1))
        analyzer.add_strategy("b", _generate_nav(n_days=400, seed=99))

        dr = analyzer.compute_diversification_ratio()
        assert dr > 1.0

    def test_custom_weights(self):
        """自定义权重的分散化比率"""
        analyzer = DiversificationAnalyzer()
        analyzer.add_strategy("a", _generate_nav(n_days=200, seed=1))
        analyzer.add_strategy("b", _generate_nav(n_days=200, seed=2))

        dr_equal = analyzer.compute_diversification_ratio()
        dr_skewed = analyzer.compute_diversification_ratio({"a": 0.99, "b": 0.01})

        # 极端倾斜的权重分散化效果接近 1
        assert dr_skewed < dr_equal or abs(dr_skewed - dr_equal) < 0.3

    def test_empty_analyzer(self):
        """空分析器分散化比率为 1.0"""
        analyzer = DiversificationAnalyzer()
        assert analyzer.compute_diversification_ratio() == 1.0


class TestMarginalDiversification:
    """边际分散化贡献测试"""

    def test_marginal_with_uncorrelated_candidate(self):
        """不相关候选增加分散化"""
        analyzer = DiversificationAnalyzer()
        base = _generate_nav(n_days=300, seed=1)
        uncorrelated = _generate_nav(n_days=300, seed=99)

        analyzer.add_strategy("base", base)
        analyzer.add_strategy("candidate", uncorrelated)

        results = analyzer.compute_marginal_diversification(
            base_strategies=["base"],
            candidate_strategies=["candidate"],
        )

        assert len(results) == 1
        assert results[0].strategy_name == "candidate"
        # 不相关候选平均相关性接近 0
        assert abs(results[0].avg_correlation_with_base) < 0.3

    def test_marginal_auto_candidates(self):
        """未指定候选时自动使用非基准策略"""
        analyzer = DiversificationAnalyzer()
        for i in range(4):
            analyzer.add_strategy(f"s{i}", _generate_nav(seed=i * 7))

        results = analyzer.compute_marginal_diversification(
            base_strategies=["s0"],
        )

        cand_names = {r.strategy_name for r in results}
        assert cand_names == {"s1", "s2", "s3"}

    def test_empty_base_returns_empty(self):
        """空基准返回空列表"""
        analyzer = DiversificationAnalyzer()
        analyzer.add_strategy("a", _generate_nav(seed=1))

        results = analyzer.compute_marginal_diversification(
            base_strategies=[],
        )
        assert results == []

    def test_diversification_score_structure(self):
        """分散化得分包含所有字段"""
        analyzer = DiversificationAnalyzer()
        analyzer.add_strategy("base", _generate_nav(n_days=200, seed=1))
        analyzer.add_strategy("cand", _generate_nav(n_days=200, seed=2))

        results = analyzer.compute_marginal_diversification(["base"])
        r = results[0]

        assert hasattr(r, "strategy_name")
        assert hasattr(r, "base_portfolio_sharpe")
        assert hasattr(r, "new_portfolio_sharpe")
        assert hasattr(r, "sharpe_improvement")
        assert hasattr(r, "avg_correlation_with_base")
        assert hasattr(r, "diversification_score")


class TestSelectBestSubset:
    """最优子集选择测试"""

    def test_select_one(self):
        """选择 1 个策略"""
        analyzer = DiversificationAnalyzer()
        analyzer.add_strategy("a", _generate_nav(seed=1))
        analyzer.add_strategy("b", _generate_nav(seed=2))

        selected = analyzer.select_best_subset(n=1)
        assert len(selected) == 1
        assert selected[0] in ["a", "b"]

    def test_select_with_base(self):
        """有基础策略时保留基础"""
        analyzer = DiversificationAnalyzer()
        for i in range(4):
            analyzer.add_strategy(f"s{i}", _generate_nav(seed=i * 5))

        selected = analyzer.select_best_subset(n=3, base_strategies=["s0"])
        assert "s0" in selected
        assert len(selected) == 3

    def test_select_all(self):
        """选择全部"""
        analyzer = DiversificationAnalyzer()
        analyzer.add_strategy("a", _generate_nav(seed=1))
        analyzer.add_strategy("b", _generate_nav(seed=2))

        selected = analyzer.select_best_subset(n=2)
        assert len(selected) == 2

    def test_select_zero(self):
        """n=0 返回空"""
        analyzer = DiversificationAnalyzer()
        analyzer.add_strategy("a", _generate_nav())
        assert analyzer.select_best_subset(n=0) == []

    def test_select_maximize_dr(self):
        """最大化分散化比率"""
        analyzer = DiversificationAnalyzer()
        for i in range(5):
            analyzer.add_strategy(f"s{i}", _generate_nav(seed=i, n_days=200))

        selected = analyzer.select_best_subset(
            n=3, maximize="diversification_ratio",
        )
        assert len(selected) == 3


class TestDiversificationReport:
    """分散化报告测试"""

    def _build_analyzer(self, n_strategies: int = 3) -> DiversificationAnalyzer:
        analyzer = DiversificationAnalyzer()
        for i in range(n_strategies):
            analyzer.add_strategy(
                f"strategy_{i}", _generate_nav(seed=i * 10, n_days=200)
            )
        return analyzer

    def test_report_structure(self):
        """报告包含所有必要字段"""
        analyzer = self._build_analyzer(3)
        report = analyzer.compute_diversification_report()

        assert isinstance(report, DiversificationReport)
        assert len(report.strategy_names) == 3
        assert report.correlation_matrix.shape == (3, 3)
        assert len(report.pairwise_correlations) == 3
        assert isinstance(report.avg_pairwise_correlation, float)
        assert isinstance(report.diversification_ratio, float)
        assert len(report.strategy_volatilities) == 3
        assert len(report.strategy_sharpes) == 3

    def test_report_with_base_strategies(self):
        """带基准策略的报告含边际贡献"""
        analyzer = self._build_analyzer(4)
        report = analyzer.compute_diversification_report(
            base_strategies=["strategy_0", "strategy_1"],
        )

        assert len(report.marginal_contributions) == 2  # strategy_2, strategy_3

    def test_report_without_base(self):
        """无基准策略的报告无边际贡献"""
        analyzer = self._build_analyzer(3)
        report = analyzer.compute_diversification_report()
        assert len(report.marginal_contributions) == 0

    def test_report_summary_non_empty(self):
        """报告摘要非空"""
        analyzer = self._build_analyzer(3)
        report = analyzer.compute_diversification_report(
            base_strategies=["strategy_0"],
        )
        summary = report.summary()

        assert "分散化分析报告" in summary
        assert "相关性矩阵" in summary
        assert "strategy_0" in summary
        assert len(summary) > 100

    def test_report_redundant_pairs(self):
        """冗余策略对被检测"""
        analyzer = DiversificationAnalyzer(redundancy_threshold=0.5)
        base = _generate_nav(n_days=200, seed=42)
        highly_corr = _generate_correlated_nav(base, correlation=0.95, seed=50)
        independent = _generate_nav(n_days=200, seed=99)

        analyzer.add_strategy("base", base)
        analyzer.add_strategy("clone", highly_corr)
        analyzer.add_strategy("indep", independent)

        report = analyzer.compute_diversification_report()

        # 至少有 base-clone 这一对冗余
        redundant_names = {
            (p.strategy_a, p.strategy_b) for p in report.redundant_pairs
        }
        assert any(
            ("base" in pair and "clone" in pair) for pair in redundant_names
        )


class TestEdgeCases:
    """边界情况测试"""

    def test_very_short_nav(self):
        """最短（2天）NAV 也能工作"""
        analyzer = DiversificationAnalyzer()
        nav = {date(2023, 1, 1): 1.0, date(2023, 1, 2): 1.01}
        analyzer.add_strategy("short", nav)

        names, corr = analyzer.compute_correlation_matrix()
        assert len(names) == 1

    def test_no_common_dates(self):
        """无公共日期时报告仍可生成"""
        analyzer = DiversificationAnalyzer()
        nav_a = {date(2023, 1, d): 1.0 + d * 0.01 for d in range(1, 10)}
        nav_b = {date(2023, 2, d): 1.0 + d * 0.01 for d in range(1, 10)}

        analyzer.add_strategy("a", nav_a)
        analyzer.add_strategy("b", nav_b)

        report = analyzer.compute_diversification_report()
        # 无公共日期，分散化比率应为 1.0
        assert report.diversification_ratio == 1.0

    def test_constant_nav_strategy(self):
        """零波动率策略（净值不变）"""
        analyzer = DiversificationAnalyzer()
        flat = {date(2023, 1, 1) + timedelta(days=i): 1.0 for i in range(100)}
        normal = _generate_nav(n_days=100, seed=42)

        analyzer.add_strategy("flat", flat)
        analyzer.add_strategy("normal", normal)

        # 应不崩溃
        report = analyzer.compute_diversification_report()
        assert report.strategy_volatilities["flat"] == 0.0

    def test_large_number_of_strategies(self):
        """10 个策略的分析"""
        analyzer = DiversificationAnalyzer()
        for i in range(10):
            analyzer.add_strategy(f"s{i}", _generate_nav(seed=i, n_days=200))

        report = analyzer.compute_diversification_report(
            base_strategies=["s0", "s1"],
        )

        assert report.correlation_matrix.shape == (10, 10)
        assert len(report.pairwise_correlations) == 45  # C(10,2) = 45
        assert len(report.marginal_contributions) == 8
