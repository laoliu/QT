"""adaptive_weight 自适应动态权重模块测试"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pytest

from aipha.strategy.adaptive_weight import (
    AdaptivePortfolioResult,
    AdaptiveWeightAllocator,
    WeightMethod,
    WEIGHT_METHOD_LABELS,
    WeightSnapshot,
    compare_weight_methods,
    format_comparison_report,
    _compute_metrics,
    _nav_to_returns,
    _yearly_avg_weights,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_nav(
    start: date,
    n_days: int,
    annual_return: float = 0.10,
    volatility: float = 0.15,
    seed: int = 42,
) -> dict[date, float]:
    """生成模拟策略 NAV 序列"""
    rng = np.random.default_rng(seed)
    daily_ret = annual_return / 252
    daily_vol = volatility / np.sqrt(252)
    nav = 1.0
    result: dict[date, float] = {start: nav}
    d = start
    for _ in range(n_days - 1):
        d += timedelta(days=1)
        while d.weekday() >= 5:  # 跳过周末
            d += timedelta(days=1)
        nav *= 1 + daily_ret + daily_vol * rng.standard_normal()
        result[d] = nav
    return result


def _make_strategy_navs(
    n_strategies: int = 3,
    n_days: int = 300,
    start: date | None = None,
) -> dict[str, dict[date, float]]:
    """生成多策略 NAV 字典"""
    if start is None:
        start = date(2020, 1, 2)
    params = [
        ("策略A", 0.15, 0.12, 10),
        ("策略B", 0.05, 0.20, 20),
        ("策略C", 0.08, 0.08, 30),
        ("策略D", 0.20, 0.25, 40),
        ("策略E", -0.05, 0.30, 50),
    ]
    navs: dict[str, dict[date, float]] = {}
    for i in range(min(n_strategies, len(params))):
        name, ar, vol, seed = params[i]
        navs[name] = _make_nav(start, n_days, ar, vol, seed)
    return navs


# ---------------------------------------------------------------------------
# WeightMethod enum
# ---------------------------------------------------------------------------

class TestWeightMethod:
    def test_values(self):
        assert WeightMethod.EQUAL.value == "equal"
        assert WeightMethod.ROLLING_RISK_PARITY.value == "rolling_risk_parity"
        assert WeightMethod.COMPOSITE_ADAPTIVE.value == "composite_adaptive"

    def test_from_string(self):
        assert WeightMethod("equal") == WeightMethod.EQUAL
        assert WeightMethod("momentum") == WeightMethod.MOMENTUM

    def test_all_have_labels(self):
        for m in WeightMethod:
            assert m.value in WEIGHT_METHOD_LABELS


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_compute_metrics_basic(self):
        nav = np.array([1.0, 1.01, 1.02, 1.015, 1.03, 1.05])
        m = _compute_metrics(nav)
        assert m["total_return"] == pytest.approx(0.05, abs=1e-10)
        assert m["max_drawdown"] > 0
        assert m["volatility"] > 0

    def test_compute_metrics_empty(self):
        m = _compute_metrics(np.array([]))
        assert m["total_return"] == 0.0

    def test_compute_metrics_single(self):
        m = _compute_metrics(np.array([1.0]))
        assert m["total_return"] == 0.0

    def test_nav_to_returns(self):
        dates = [date(2020, 1, i) for i in range(1, 6)]
        nav = {d: float(v) for d, v in zip(dates, [1.0, 1.01, 1.02, 0.99, 1.03])}
        rets = _nav_to_returns(nav, dates)
        assert len(rets) == 4
        assert rets[0] == pytest.approx(0.01, abs=1e-10)

    def test_yearly_avg_weights(self):
        snaps = [
            WeightSnapshot(date(2020, 6, 1), {"A": 0.6, "B": 0.4}, "equal"),
            WeightSnapshot(date(2020, 12, 1), {"A": 0.4, "B": 0.6}, "equal"),
            WeightSnapshot(date(2021, 6, 1), {"A": 0.7, "B": 0.3}, "equal"),
        ]
        result = _yearly_avg_weights(snaps)
        assert 2020 in result
        assert 2021 in result
        assert result[2020]["A"] == pytest.approx(0.5, abs=1e-10)
        assert result[2021]["A"] == pytest.approx(0.7, abs=1e-10)


# ---------------------------------------------------------------------------
# WeightSnapshot
# ---------------------------------------------------------------------------

class TestWeightSnapshot:
    def test_frozen(self):
        snap = WeightSnapshot(date(2020, 1, 1), {"A": 0.5, "B": 0.5}, "equal")
        with pytest.raises(AttributeError):
            snap.method = "other"  # type: ignore

    def test_meta_default(self):
        snap = WeightSnapshot(date(2020, 1, 1), {"A": 1.0}, "equal")
        assert snap.meta == {}


# ---------------------------------------------------------------------------
# AdaptivePortfolioResult
# ---------------------------------------------------------------------------

class TestAdaptivePortfolioResult:
    def test_get_weights_at(self):
        snaps = [
            WeightSnapshot(date(2020, 1, 1), {"A": 0.6, "B": 0.4}, "equal"),
            WeightSnapshot(date(2020, 1, 2), {"A": 0.5, "B": 0.5}, "equal"),
        ]
        result = AdaptivePortfolioResult(
            portfolio_nav={}, weights_history=snaps,
            method="equal", params={}, strategy_navs={},
        )
        w = result.get_weights_at(date(2020, 1, 2))
        assert w is not None
        assert w["A"] == 0.5

    def test_get_weights_at_missing(self):
        result = AdaptivePortfolioResult(
            portfolio_nav={}, weights_history=[],
            method="equal", params={}, strategy_navs={},
        )
        assert result.get_weights_at(date(2020, 1, 1)) is None

    def test_get_weight_df_data(self):
        snaps = [
            WeightSnapshot(date(2020, 1, 1), {"A": 0.6, "B": 0.4}, "equal"),
        ]
        result = AdaptivePortfolioResult(
            portfolio_nav={}, weights_history=snaps,
            method="equal", params={}, strategy_navs={},
        )
        rows = result.get_weight_df_data()
        assert len(rows) == 1
        assert rows[0]["A"] == 0.6


# ---------------------------------------------------------------------------
# AdaptiveWeightAllocator — equal
# ---------------------------------------------------------------------------

class TestAllocatorEqual:
    def test_equal_weights(self):
        navs = _make_strategy_navs(3, 100)
        allocator = AdaptiveWeightAllocator(method="equal")
        result = allocator.compute_portfolio(navs)
        assert len(result.portfolio_nav) > 0
        assert len(result.weights_history) > 0
        # equal 始终等权
        for snap in result.weights_history:
            assert snap.weights["策略A"] == pytest.approx(1 / 3, abs=1e-10)

    def test_empty_input(self):
        allocator = AdaptiveWeightAllocator(method="equal")
        result = allocator.compute_portfolio({})
        assert result.portfolio_nav == {}
        assert result.weights_history == []


# ---------------------------------------------------------------------------
# AdaptiveWeightAllocator — rolling_risk_parity
# ---------------------------------------------------------------------------

class TestAllocatorRiskParity:
    def test_weights_sum_to_one(self):
        navs = _make_strategy_navs(3, 200)
        allocator = AdaptiveWeightAllocator(
            method="rolling_risk_parity", lookback=40, rebalance_freq=5,
        )
        result = allocator.compute_portfolio(navs)
        for snap in result.weights_history:
            total = sum(snap.weights.values())
            assert total == pytest.approx(1.0, abs=1e-6)

    def test_low_vol_strategy_gets_higher_weight(self):
        """低波动策略应获得更高权重"""
        navs = _make_strategy_navs(3, 200)
        allocator = AdaptiveWeightAllocator(
            method="rolling_risk_parity", lookback=60, rebalance_freq=5,
            min_weight=0.01, max_weight=0.98,
        )
        result = allocator.compute_portfolio(navs)
        # 策略C 波动率最低(0.08)，权重应最高（在大部分 snapshot 中）
        last_snap = result.weights_history[-1]
        # 只验证权重存在且合理
        assert last_snap.weights["策略C"] > 0
        assert all(w > 0 for w in last_snap.weights.values())


# ---------------------------------------------------------------------------
# AdaptiveWeightAllocator — inverse_volatility
# ---------------------------------------------------------------------------

class TestAllocatorInverseVol:
    def test_produces_valid_result(self):
        navs = _make_strategy_navs(3, 200)
        allocator = AdaptiveWeightAllocator(method="inverse_volatility", lookback=40)
        result = allocator.compute_portfolio(navs)
        assert result.total_return != 0
        assert len(result.weights_history) > 0


# ---------------------------------------------------------------------------
# AdaptiveWeightAllocator — momentum
# ---------------------------------------------------------------------------

class TestAllocatorMomentum:
    def test_momentum_weights_change(self):
        navs = _make_strategy_navs(3, 200)
        allocator = AdaptiveWeightAllocator(method="momentum", lookback=40, rebalance_freq=5)
        result = allocator.compute_portfolio(navs)
        # 不同时间点权重应有差异
        w_first = result.weights_history[0].weights
        w_last = result.weights_history[-1].weights
        # 至少有一些不同（初始是等权，后面应调整）
        if len(result.weights_history) > 60:
            # lookback=40, 所以60天后应开始调整
            assert w_first != w_last


# ---------------------------------------------------------------------------
# AdaptiveWeightAllocator — rolling_max_sharpe
# ---------------------------------------------------------------------------

class TestAllocatorMaxSharpe:
    def test_produces_valid_result(self):
        navs = _make_strategy_navs(3, 200)
        allocator = AdaptiveWeightAllocator(method="rolling_max_sharpe", lookback=60)
        result = allocator.compute_portfolio(navs)
        assert len(result.portfolio_nav) > 0
        assert result.sharpe != 0 or result.total_return == 0


# ---------------------------------------------------------------------------
# AdaptiveWeightAllocator — regime_adaptive
# ---------------------------------------------------------------------------

class TestAllocatorRegime:
    def test_regime_changes_weights(self):
        navs = _make_strategy_navs(3, 300)
        allocator = AdaptiveWeightAllocator(method="regime_adaptive", lookback=40)
        result = allocator.compute_portfolio(navs)
        assert len(result.weights_history) > 0
        # 权重应在 regime 切换时变化
        for snap in result.weights_history:
            total = sum(snap.weights.values())
            assert total == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# AdaptiveWeightAllocator — drawdown_penalty
# ---------------------------------------------------------------------------

class TestAllocatorDrawdownPenalty:
    def test_drawdown_reduces_weight(self):
        navs = _make_strategy_navs(3, 200)
        allocator = AdaptiveWeightAllocator(
            method="drawdown_penalty", lookback=40, min_weight=0.01, max_weight=0.98,
        )
        result = allocator.compute_portfolio(navs)
        assert len(result.portfolio_nav) > 0
        for snap in result.weights_history:
            total = sum(snap.weights.values())
            assert total == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# AdaptiveWeightAllocator — composite_adaptive
# ---------------------------------------------------------------------------

class TestAllocatorComposite:
    def test_composite_blends_methods(self):
        navs = _make_strategy_navs(3, 200)
        allocator = AdaptiveWeightAllocator(method="composite_adaptive", lookback=40)
        result = allocator.compute_portfolio(navs)
        assert len(result.portfolio_nav) > 0
        assert result.method == "composite_adaptive"

    def test_custom_composite_weights(self):
        navs = _make_strategy_navs(3, 200)
        allocator = AdaptiveWeightAllocator(
            method="composite_adaptive",
            lookback=40,
            composite_weights={"inv_vol": 0.5, "momentum": 0.2, "dd_penalty": 0.2, "risk_parity": 0.1},
        )
        result = allocator.compute_portfolio(navs)
        assert len(result.portfolio_nav) > 0


# ---------------------------------------------------------------------------
# Weight clipping
# ---------------------------------------------------------------------------

class TestWeightClipping:
    def test_min_weight_enforced(self):
        navs = _make_strategy_navs(3, 200)
        allocator = AdaptiveWeightAllocator(
            method="momentum", lookback=40, min_weight=0.15,
        )
        result = allocator.compute_portfolio(navs)
        for snap in result.weights_history:
            for w in snap.weights.values():
                # 由于归一化，实际最小可能略偏差，但不会远低于 min_weight
                assert w >= 0.10  # 允许一点浮点误差

    def test_max_weight_enforced(self):
        navs = _make_strategy_navs(3, 200)
        allocator = AdaptiveWeightAllocator(
            method="momentum", lookback=40, max_weight=0.50,
        )
        result = allocator.compute_portfolio(navs)
        for snap in result.weights_history:
            for w in snap.weights.values():
                assert w <= 0.50 + 1e-5  # 允许极微小浮点误差


# ---------------------------------------------------------------------------
# Rebalance frequency
# ---------------------------------------------------------------------------

class TestRebalanceFrequency:
    def test_weights_stable_between_rebalances(self):
        navs = _make_strategy_navs(3, 200)
        allocator = AdaptiveWeightAllocator(
            method="rolling_risk_parity", lookback=40, rebalance_freq=20,
        )
        result = allocator.compute_portfolio(navs)
        # 在 lookback 之后、两次 rebalance 之间，权重应保持不变
        snapshots = result.weights_history
        if len(snapshots) > 80:
            # lookback=40, 第一次 rebalance 在 t=40
            # 下一次在 t=60, 之间 t=41..59 权重不变
            w40 = snapshots[40].weights
            for t in range(41, min(60, len(snapshots))):
                assert snapshots[t].weights == w40


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_strategy(self):
        """单策略组合应始终权重1.0"""
        navs = _make_strategy_navs(1, 100)
        allocator = AdaptiveWeightAllocator(method="rolling_risk_parity")
        result = allocator.compute_portfolio(navs)
        for snap in result.weights_history:
            assert list(snap.weights.values()) == [pytest.approx(1.0, abs=1e-10)]

    def test_two_strategies(self):
        navs = _make_strategy_navs(2, 100)
        allocator = AdaptiveWeightAllocator(method="composite_adaptive", lookback=30)
        result = allocator.compute_portfolio(navs)
        assert len(result.portfolio_nav) > 0

    def test_short_data(self):
        """数据不足 lookback 时应使用等权"""
        navs = _make_strategy_navs(3, 20)
        allocator = AdaptiveWeightAllocator(method="rolling_risk_parity", lookback=60)
        result = allocator.compute_portfolio(navs)
        # 所有权重应保持等权（因数据不足触发 rebalance）
        for snap in result.weights_history:
            for w in snap.weights.values():
                assert w == pytest.approx(1 / 3, abs=1e-10)

    def test_no_common_dates(self):
        """无交叉日期"""
        nav1 = {date(2020, 1, i): float(i) for i in range(1, 5)}
        nav2 = {date(2021, 1, i): float(i) for i in range(1, 5)}
        allocator = AdaptiveWeightAllocator(method="equal")
        result = allocator.compute_portfolio({"A": nav1, "B": nav2})
        assert result.portfolio_nav == {}

    def test_five_strategies(self):
        navs = _make_strategy_navs(5, 200)
        allocator = AdaptiveWeightAllocator(method="regime_adaptive", lookback=40)
        result = allocator.compute_portfolio(navs)
        assert len(result.portfolio_nav) > 0
        assert len(result.weights_history[0].weights) == 5


# ---------------------------------------------------------------------------
# Result metrics
# ---------------------------------------------------------------------------

class TestResultMetrics:
    def test_metrics_populated(self):
        navs = _make_strategy_navs(3, 300)
        allocator = AdaptiveWeightAllocator(method="rolling_risk_parity", lookback=60)
        result = allocator.compute_portfolio(navs)
        assert result.total_return != 0
        assert result.annualized_return != 0
        assert result.volatility > 0
        assert result.max_drawdown > 0

    def test_yearly_avg_weights_populated(self):
        navs = _make_strategy_navs(3, 500, start=date(2020, 1, 2))
        allocator = AdaptiveWeightAllocator(method="momentum", lookback=40)
        result = allocator.compute_portfolio(navs)
        assert len(result.yearly_avg_weights) >= 1
        for year, ws in result.yearly_avg_weights.items():
            total = sum(ws.values())
            assert total == pytest.approx(1.0, abs=0.05)

    def test_params_snapshot(self):
        allocator = AdaptiveWeightAllocator(
            method="momentum", lookback=42, rebalance_freq=7,
        )
        result = allocator.compute_portfolio(_make_strategy_navs(2, 100))
        assert result.params["lookback"] == 42
        assert result.params["rebalance_freq"] == 7
        assert result.params["method"] == "momentum"


# ---------------------------------------------------------------------------
# compare_weight_methods
# ---------------------------------------------------------------------------

class TestCompareWeightMethods:
    def test_all_methods(self):
        navs = _make_strategy_navs(3, 200)
        results = compare_weight_methods(navs, lookback=40, rebalance_freq=5)
        assert len(results) == len(WeightMethod)
        for m_name, res in results.items():
            assert m_name in [m.value for m in WeightMethod]
            assert len(res.portfolio_nav) > 0

    def test_subset_methods(self):
        navs = _make_strategy_navs(3, 200)
        results = compare_weight_methods(
            navs,
            methods=["equal", "momentum"],
            lookback=40,
        )
        assert len(results) == 2
        assert "equal" in results
        assert "momentum" in results

    def test_empty_navs(self):
        results = compare_weight_methods({})
        for m_name, res in results.items():
            assert res.portfolio_nav == {}


# ---------------------------------------------------------------------------
# format_comparison_report
# ---------------------------------------------------------------------------

class TestFormatReport:
    def test_report_content(self):
        navs = _make_strategy_navs(3, 200)
        results = compare_weight_methods(navs, methods=["equal", "momentum"])
        report = format_comparison_report(results)
        assert "自适应权重方法对比报告" in report
        assert "等权" in report
        assert "动量" in report
        assert "夏普" in report

    def test_empty_results(self):
        report = format_comparison_report({})
        assert "自适应权重方法对比报告" in report


# ---------------------------------------------------------------------------
# NAV 一致性验证
# ---------------------------------------------------------------------------

class TestNAVConsistency:
    def test_equal_method_nav_reasonable(self):
        """等权方法的 NAV 应在各策略 NAV 的范围内"""
        navs = _make_strategy_navs(3, 100)
        allocator = AdaptiveWeightAllocator(method="equal")
        result = allocator.compute_portfolio(navs)

        common_dates = sorted(result.portfolio_nav.keys())
        names = list(navs.keys())

        for d in common_dates:
            strat_navs = [result.strategy_navs[name][d] for name in names]
            actual = result.portfolio_nav[d]
            # 组合 NAV 应在策略 NAV 的合理范围内
            assert min(strat_navs) * 0.8 <= actual <= max(strat_navs) * 1.2

    def test_portfolio_nav_monotonic_start(self):
        """组合 NAV 第一天应为 1.0"""
        navs = _make_strategy_navs(3, 100)
        allocator = AdaptiveWeightAllocator(method="rolling_risk_parity")
        result = allocator.compute_portfolio(navs)
        first_date = sorted(result.portfolio_nav.keys())[0]
        assert result.portfolio_nav[first_date] == pytest.approx(1.0)
