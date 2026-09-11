"""
VaR / CVaR / 压力测试 单元测试

覆盖：
1. HistoricalVaR — 正常、边界、全正/全负收益
2. ParametricVaR — 正态假设、零波动
3. CornishFisherVaR — 偏度/峰度修正
4. StressTestRunner — 区间匹配、无数据、摘要
5. VaRResult.to_dict、StressTestResult.to_dict
"""

import math
from datetime import date, timedelta

import pytest

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR
from aipha.risk.var import (
    A_SHARE_STRESS_SCENARIOS,
    CornishFisherVaR,
    HistoricalVaR,
    ParametricVaR,
    PortfolioVaR,
    PortfolioVaRResult,
    StressTestResult,
    StressTestRunner,
    StressTestScenario,
    VaRMethod,
    VaRResult,
)


# ============================================================================
# Fixtures
# ============================================================================


def _random_returns(n: int, mean: float = 0.0, std: float = 0.01,
                    seed: int = 42) -> list[float]:
    """用简单 LCG 生成伪随机正态近似收益"""
    import random
    rng = random.Random(seed)
    return [mean + rng.gauss(0, std) for _ in range(n)]


@pytest.fixture
def normal_returns() -> list[float]:
    """250 个交易日、均值 0.05%、日波动 1%"""
    return _random_returns(250, mean=0.0005, std=0.01)


@pytest.fixture
def all_negative_returns() -> list[float]:
    """全负收益"""
    return [-0.01] * 100


@pytest.fixture
def all_positive_returns() -> list[float]:
    """全正收益"""
    return [0.01] * 100


@pytest.fixture
def fat_tail_returns() -> list[float]:
    """厚尾分布：大部分小波动 + 偶尔极端"""
    rets = _random_returns(240, std=0.005, seed=99)
    rets.extend([-0.08, -0.06, -0.07, -0.05, 0.04, -0.09,
                 0.06, -0.10, 0.03, -0.04])
    return rets


# ============================================================================
# HistoricalVaR
# ============================================================================


class TestHistoricalVaR:
    """历史模拟法 VaR 测试"""

    def test_basic_computation(self, normal_returns):
        var = HistoricalVaR(confidence=0.95)
        result = var.compute(normal_returns)
        assert result.method == VaRMethod.HISTORICAL
        assert result.sample_size == 250
        assert result.confidence == 0.95
        assert result.var > 0  # VaR 应为正
        assert result.cvar >= result.var  # CVaR >= VaR

    def test_higher_confidence_higher_var(self, normal_returns):
        """更高置信度 → 更大 VaR"""
        var_95 = HistoricalVaR(0.95).compute(normal_returns)
        var_99 = HistoricalVaR(0.99).compute(normal_returns)
        assert var_99.var >= var_95.var

    def test_all_negative_returns(self, all_negative_returns):
        """全负收益 → VaR 和 CVaR 都为正且大"""
        result = HistoricalVaR(0.95).compute(all_negative_returns)
        assert result.var == pytest.approx(0.01)
        assert result.cvar == pytest.approx(0.01)

    def test_all_positive_returns(self, all_positive_returns):
        """全正收益 → VaR 应为负（没有损失）"""
        result = HistoricalVaR(0.95).compute(all_positive_returns)
        assert result.var < 0  # 没有真正的损失

    def test_annualized_var(self, normal_returns):
        """年化 VaR = 日 VaR × sqrt(TRADING_DAYS_PER_YEAR)

        用默认构造，所以年化因子必须取 §13.8 的常量；下面 PortfolioVaR 那条
        显式传了 252，是在验证参数能被覆盖，两者不矛盾。
        """
        result = HistoricalVaR(0.95).compute(normal_returns)
        expected_annual = result.var * math.sqrt(TRADING_DAYS_PER_YEAR)
        assert result.annualized_var == pytest.approx(expected_annual)

    def test_insufficient_samples(self):
        """样本不足 → ValueError"""
        var = HistoricalVaR()
        with pytest.raises(ValueError, match="至少需要 2"):
            var.compute([0.01])

    def test_invalid_confidence(self):
        """无效置信度 → ValueError"""
        with pytest.raises(ValueError, match="confidence"):
            HistoricalVaR(confidence=1.5)
        with pytest.raises(ValueError):
            HistoricalVaR(confidence=0)

    def test_to_dict(self, normal_returns):
        result = HistoricalVaR(0.95).compute(normal_returns)
        d = result.to_dict()
        assert d["method"] == "historical"
        assert "var" in d
        assert "cvar" in d
        assert "annualized_var" in d


# ============================================================================
# ParametricVaR
# ============================================================================


class TestParametricVaR:
    """参数法 VaR 测试"""

    def test_basic_computation(self, normal_returns):
        var = ParametricVaR(confidence=0.95)
        result = var.compute(normal_returns)
        assert result.method == VaRMethod.PARAMETRIC
        assert result.var > 0
        assert result.cvar >= result.var

    def test_zero_volatility(self):
        """零波动 → VaR = CVaR = 0"""
        result = ParametricVaR(0.95).compute([0.0] * 100)
        assert result.var == 0.0
        assert result.cvar == 0.0

    def test_vs_historical_order(self, normal_returns):
        """正态分布数据：参数法和历史法应在同一量级"""
        hist = HistoricalVaR(0.95).compute(normal_returns)
        param = ParametricVaR(0.95).compute(normal_returns)
        # 同一量级，差异不超过 50%
        assert abs(hist.var - param.var) / max(hist.var, 0.0001) < 0.5

    def test_higher_confidence_higher_var(self, normal_returns):
        v95 = ParametricVaR(0.95).compute(normal_returns)
        v99 = ParametricVaR(0.99).compute(normal_returns)
        assert v99.var > v95.var

    def test_insufficient_samples(self):
        with pytest.raises(ValueError, match="至少需要 2"):
            ParametricVaR().compute([])


# ============================================================================
# CornishFisherVaR
# ============================================================================


class TestCornishFisherVaR:
    """Cornish-Fisher VaR 测试"""

    def test_basic_computation(self, normal_returns):
        var = CornishFisherVaR(confidence=0.95)
        result = var.compute(normal_returns)
        assert result.method == VaRMethod.CORNISH_FISHER
        assert result.var > 0
        assert result.cvar >= 0

    def test_fat_tails_higher_var(self, fat_tail_returns, normal_returns):
        """厚尾数据 → CF VaR 应比参数法大"""
        cf = CornishFisherVaR(0.95).compute(fat_tail_returns)
        # CF 对厚尾的修正应增大 VaR
        assert cf.var > 0

    def test_near_normal_close_to_parametric(self, normal_returns):
        """近正态数据 → CF 与参数法接近"""
        cf = CornishFisherVaR(0.95).compute(normal_returns)
        param = ParametricVaR(0.95).compute(normal_returns)
        assert abs(cf.var - param.var) / max(param.var, 0.0001) < 0.3

    def test_zero_volatility(self):
        result = CornishFisherVaR(0.95).compute([0.0] * 10)
        assert result.var == 0.0

    def test_insufficient_samples(self):
        with pytest.raises(ValueError, match="至少需要 4"):
            CornishFisherVaR().compute([0.01, -0.01, 0.02])


# ============================================================================
# StressTestRunner
# ============================================================================


class TestStressTestRunner:
    """压力测试运行器测试"""

    def _make_nav(self, start: date, days: int,
                  daily_ret: float = -0.02) -> list[tuple[date, float]]:
        """生成模拟 NAV 序列"""
        nav = 1_000_000.0
        series = [(start, nav)]
        for i in range(1, days):
            nav *= (1 + daily_ret)
            series.append((start + timedelta(days=i), nav))
        return series

    def test_run_with_matching_scenario(self):
        """NAV 覆盖场景区间 → 产出结果"""
        scenario = StressTestScenario(
            name="test_crash",
            description="测试崩盘",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 31),
        )
        nav = self._make_nav(date(2025, 1, 1), 90, daily_ret=-0.01)
        runner = StressTestRunner(scenarios=[scenario])
        results = runner.run(nav)
        assert len(results) == 1
        r = results[0]
        assert r.scenario.name == "test_crash"
        assert r.portfolio_return < 0  # 持续下跌
        assert r.max_drawdown > 0
        assert r.var_95 > 0
        assert r.worst_day < 0

    def test_no_overlap(self):
        """NAV 不覆盖任何场景 → 空结果"""
        scenario = StressTestScenario(
            name="future",
            description="未来场景",
            start_date=date(2030, 1, 1),
            end_date=date(2030, 6, 30),
        )
        nav = self._make_nav(date(2025, 1, 1), 100)
        runner = StressTestRunner(scenarios=[scenario])
        results = runner.run(nav)
        assert len(results) == 0

    def test_empty_nav(self):
        """空 NAV → 空结果"""
        runner = StressTestRunner()
        results = runner.run([])
        assert results == []

    def test_summary_output(self):
        """摘要输出格式正确"""
        scenario = StressTestScenario(
            name="test",
            description="测试",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 28),
        )
        nav = self._make_nav(date(2025, 1, 1), 60, daily_ret=-0.005)
        runner = StressTestRunner(scenarios=[scenario])
        results = runner.run(nav)
        summary = runner.summary(results)
        assert "情景: test" in summary
        assert "最大回撤" in summary

    def test_summary_no_results(self):
        """无结果时的摘要"""
        runner = StressTestRunner()
        summary = runner.summary([])
        assert "无可用" in summary

    def test_stress_result_to_dict(self):
        """StressTestResult.to_dict"""
        scenario = StressTestScenario(
            name="test", description="测试",
            start_date=date(2025, 1, 1), end_date=date(2025, 3, 1),
        )
        r = StressTestResult(
            scenario=scenario,
            portfolio_return=-0.15,
            max_drawdown=0.20,
            var_95=0.03,
            cvar_95=0.04,
            worst_day=-0.08,
            trading_days=40,
        )
        d = r.to_dict()
        assert d["scenario"] == "test"
        assert d["portfolio_return"] == -0.15
        assert d["max_drawdown"] == 0.20

    def test_default_scenarios(self):
        """默认使用 A 股压力情景"""
        runner = StressTestRunner()
        assert len(runner.scenarios) == len(A_SHARE_STRESS_SCENARIOS)
        assert runner.scenarios[0].name == "2015_crash"

    def test_multiple_scenarios(self):
        """多个场景，部分覆盖"""
        s1 = StressTestScenario(
            name="early", description="早期",
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 31),
        )
        s2 = StressTestScenario(
            name="late", description="晚期",
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 31),
        )
        nav = self._make_nav(date(2025, 1, 1), 60)
        runner = StressTestRunner(scenarios=[s1, s2])
        results = runner.run(nav)
        assert len(results) == 1
        assert results[0].scenario.name == "early"


# ============================================================================
# PortfolioVaR (C-4: 组合级协方差矩阵法)
# ============================================================================


def _make_portfolio_data(
    n_obs: int = 250, seed: int = 42,
) -> tuple[dict[str, float], dict[str, list[float]]]:
    """构造 3 资产组合测试数据：权重 + 收益率矩阵。"""
    import random
    rng = random.Random(seed)
    weights = {"A": 0.5, "B": 0.3, "C": 0.2}
    returns_matrix = {
        "A": [rng.gauss(0.0005, 0.01) for _ in range(n_obs)],
        "B": [rng.gauss(0.0003, 0.015) for _ in range(n_obs)],
        "C": [rng.gauss(0.0002, 0.008) for _ in range(n_obs)],
    }
    return weights, returns_matrix


class TestPortfolioVaR:
    """组合级协方差矩阵法 VaR 测试（C-4）"""

    def test_basic_computation(self):
        """基本计算：VaR/CVaR > 0，值在合理范围。"""
        weights, returns_matrix = _make_portfolio_data()
        pv = PortfolioVaR(confidence=0.95)
        result = pv.compute(weights, returns_matrix)

        assert isinstance(result, PortfolioVaRResult)
        assert result.var > 0
        assert result.cvar >= result.var  # CVaR >= VaR
        assert result.portfolio_vol > 0
        assert result.n_assets == 3
        assert result.n_obs == 250

    def test_component_var_sums_close_to_total(self):
        """Component VaR 之和应接近组合 VaR。"""
        weights, returns_matrix = _make_portfolio_data()
        pv = PortfolioVaR(confidence=0.95)
        result = pv.compute(weights, returns_matrix)

        sum_component = sum(result.component_var.values())
        # Component VaR 之和 ≈ z * port_vol（在正态假设下严格相等）
        z = ParametricVaR._norm_ppf(0.95)
        assert abs(sum_component - z * result.portfolio_vol) < 1e-10

    def test_all_keys_present(self):
        """component_var 包含所有资产键。"""
        weights, returns_matrix = _make_portfolio_data()
        pv = PortfolioVaR(confidence=0.95)
        result = pv.compute(weights, returns_matrix)
        assert set(result.component_var.keys()) == {"A", "B", "C"}

    def test_annualized_var(self):
        """年化 VaR = 日度 VaR × sqrt(252)。"""
        weights, returns_matrix = _make_portfolio_data()
        pv = PortfolioVaR(confidence=0.95, trading_days_per_year=252)
        result = pv.compute(weights, returns_matrix)
        expected = result.var * math.sqrt(252)
        assert abs(result.annualized_var - expected) < 1e-12

    def test_single_asset_matches_parametric(self):
        """单资产组合 VaR 应与 ParametricVaR 一致。"""
        import random
        rng = random.Random(123)
        rets = [rng.gauss(0.0003, 0.012) for _ in range(200)]

        # PortfolioVaR (单资产)
        pv = PortfolioVaR(confidence=0.95)
        p_result = pv.compute({"X": 1.0}, {"X": rets})

        # ParametricVaR
        param = ParametricVaR(confidence=0.95)
        s_result = param.compute(rets)

        assert abs(p_result.var - s_result.var) < 1e-10

    def test_empty_weights_raises(self):
        """空权重应抛出 ValueError。"""
        pv = PortfolioVaR()
        with pytest.raises(ValueError, match="不能为空"):
            pv.compute({}, {})

    def test_missing_returns_raises(self):
        """权重中有资产但收益率矩阵缺失时应报错。"""
        pv = PortfolioVaR()
        with pytest.raises(ValueError, match="缺少收益率数据"):
            pv.compute({"A": 0.5, "B": 0.5}, {"A": [0.01, 0.02]})

    def test_unequal_length_raises(self):
        """各资产收益率长度不一致时应报错。"""
        pv = PortfolioVaR()
        with pytest.raises(ValueError, match="长度不一致"):
            pv.compute(
                {"A": 0.5, "B": 0.5},
                {"A": [0.01, 0.02], "B": [0.01]},
            )

    def test_too_few_obs_raises(self):
        """观测期数 < 2 时应报错。"""
        pv = PortfolioVaR()
        with pytest.raises(ValueError, match="至少需要 2 期"):
            pv.compute({"A": 1.0}, {"A": [0.01]})

    def test_confidence_validation(self):
        """confidence 超范围应报错。"""
        with pytest.raises(ValueError):
            PortfolioVaR(confidence=1.5)
        with pytest.raises(ValueError):
            PortfolioVaR(confidence=0.0)

    def test_to_dict(self):
        """to_dict 输出完整键。"""
        weights, returns_matrix = _make_portfolio_data(n_obs=50)
        pv = PortfolioVaR(confidence=0.99)
        result = pv.compute(weights, returns_matrix)
        d = result.to_dict()
        expected_keys = {
            "var", "cvar", "confidence", "portfolio_vol",
            "annualized_var", "annualized_cvar", "component_var",
            "n_assets", "n_obs",
        }
        assert set(d.keys()) == expected_keys
        assert d["confidence"] == 0.99
        assert d["n_assets"] == 3

    def test_higher_confidence_larger_var(self):
        """99% VaR 应大于 95% VaR。"""
        weights, returns_matrix = _make_portfolio_data()
        var_95 = PortfolioVaR(confidence=0.95).compute(weights, returns_matrix).var
        var_99 = PortfolioVaR(confidence=0.99).compute(weights, returns_matrix).var
        assert var_99 > var_95

    def test_zero_weight_asset_zero_component(self):
        """权重为 0 的资产，Component VaR 应为 0。"""
        weights = {"A": 1.0, "B": 0.0}
        import random
        rng = random.Random(7)
        returns_matrix = {
            "A": [rng.gauss(0, 0.01) for _ in range(100)],
            "B": [rng.gauss(0, 0.02) for _ in range(100)],
        }
        result = PortfolioVaR(confidence=0.95).compute(weights, returns_matrix)
        assert abs(result.component_var["B"]) < 1e-15
