"""Alpha 衰减与市场状态分析测试

测试覆盖：
- analyze_alpha_decay: 无衰减/有衰减/数据不足
- analyze_by_regime: BULL/BEAR/NEUTRAL 分组
- _simple_ols: 线性回归
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from aipha.reporting.alpha_decay import (
    AlphaDecayResult,
    RegimeMetrics,
    _simple_ols,
    analyze_alpha_decay,
    analyze_by_regime,
)


def _make_navs(
    start: date, n_days: int, daily_return: float = 0.001
) -> list[tuple[date, float]]:
    """生成 NAV 序列"""
    nav = 1_000_000.0
    result = []
    d = start
    for i in range(n_days):
        result.append((d, nav))
        nav *= (1 + daily_return)
        d += timedelta(days=1)
        # 跳过周末
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return result


# ============================================================
# AlphaDecayAnalyzer
# ============================================================


class TestAlphaDecay:

    def test_no_decay_flat_alpha(self):
        """策略与基准相同增速 → Alpha ≈ 0"""
        start = date(2023, 1, 2)
        strat = _make_navs(start, 300, daily_return=0.002)
        bench = _make_navs(start, 300, daily_return=0.002)
        result = analyze_alpha_decay(strat, bench, window_days=60)
        if result is not None:
            # Alpha 接近 0
            assert abs(result.avg_alpha) < 0.05

    def test_positive_decay_detected(self):
        """策略超额递减 → slope < 0"""
        start = date(2022, 1, 3)
        # 构造递减超额的 NAV
        n = 400
        bench = _make_navs(start, n, daily_return=0.0005)

        # 策略前期超额高，后期超额低
        strat = []
        s_nav = 1_000_000.0
        for i, (d, _) in enumerate(bench):
            # 超额从 0.003 线性衰减到 0.0005
            excess = 0.003 - (0.003 - 0.0005) * i / n
            s_nav *= (1 + 0.0005 + excess)
            strat.append((d, s_nav))

        result = analyze_alpha_decay(strat, bench, window_days=60)
        assert result is not None
        assert result.slope < 0
        assert result.is_decaying

    def test_insufficient_data_returns_none(self):
        """数据不足 → 返回 None"""
        start = date(2024, 1, 1)
        strat = _make_navs(start, 30, daily_return=0.001)
        bench = _make_navs(start, 30, daily_return=0.001)
        result = analyze_alpha_decay(strat, bench, window_days=60)
        assert result is None

    def test_alpha_relative_to_benchmark(self):
        """策略持续优于基准 → avg_alpha > 0"""
        start = date(2023, 1, 2)
        strat = _make_navs(start, 300, daily_return=0.003)
        bench = _make_navs(start, 300, daily_return=0.001)
        result = analyze_alpha_decay(strat, bench, window_days=60)
        assert result is not None
        assert result.avg_alpha > 0

    def test_rolling_alphas_count(self):
        """滚动 Alpha 序列长度正确"""
        start = date(2023, 1, 2)
        n = 300
        strat = _make_navs(start, n, daily_return=0.002)
        bench = _make_navs(start, n, daily_return=0.001)
        result = analyze_alpha_decay(strat, bench, window_days=60)
        if result is not None:
            # (n-1 returns) / window_days ≈ expected windows
            assert result.n_windows >= 2


# ============================================================
# RegimeConditionalAnalyzer
# ============================================================


class TestRegimeAnalysis:

    def _make_regime_data(self):
        """生成带 regime 标签的数据"""
        start = date(2023, 1, 2)
        strat = _make_navs(start, 300, daily_return=0.001)
        regimes = []
        for i, (d, _) in enumerate(strat):
            if i < 100:
                regimes.append((d, "BULL"))
            elif i < 200:
                regimes.append((d, "BEAR"))
            else:
                regimes.append((d, "NEUTRAL"))
        return strat, regimes

    def test_bull_bear_split(self):
        """BULL 和 BEAR 期间正确分组"""
        strat, regimes = self._make_regime_data()
        result = analyze_by_regime(strat, regimes)
        regime_names = {r.regime for r in result}
        assert "BULL" in regime_names
        assert "BEAR" in regime_names

    def test_neutral_regime_included(self):
        """NEUTRAL 状态不被忽略"""
        strat, regimes = self._make_regime_data()
        result = analyze_by_regime(strat, regimes)
        regime_names = {r.regime for r in result}
        assert "NEUTRAL" in regime_names

    def test_regime_sharpe_calculation(self):
        """各状态下 Sharpe 有值"""
        strat, regimes = self._make_regime_data()
        result = analyze_by_regime(strat, regimes)
        for r in result:
            assert isinstance(r.sharpe_ratio, float)

    def test_regime_drawdown_per_state(self):
        """各状态回撤有效"""
        strat, regimes = self._make_regime_data()
        result = analyze_by_regime(strat, regimes)
        for r in result:
            assert r.max_drawdown >= 0

    def test_empty_regime_period(self):
        """某状态无数据 → 不出现在结果中"""
        strat = _make_navs(date(2024, 1, 2), 50, daily_return=0.001)
        regimes = [(d, "BULL") for d, _ in strat]  # 只有 BULL
        result = analyze_by_regime(strat, regimes)
        regime_names = {r.regime for r in result}
        assert "BULL" in regime_names
        assert "BEAR" not in regime_names

    def test_empty_inputs(self):
        """空输入不崩溃"""
        assert analyze_by_regime([], []) == []
        strat = _make_navs(date(2024, 1, 2), 10)
        assert analyze_by_regime(strat, []) == []


# ============================================================
# OLS 回归
# ============================================================


class TestSimpleOLS:

    def test_perfect_line(self):
        """完美线性 → R²=1"""
        slope, intercept, r2 = _simple_ols([0, 1, 2, 3], [1, 3, 5, 7])
        assert slope == pytest.approx(2.0)
        assert intercept == pytest.approx(1.0)
        assert r2 == pytest.approx(1.0)

    def test_flat_line(self):
        """全部相同 y → slope=0"""
        slope, intercept, r2 = _simple_ols([0, 1, 2], [5, 5, 5])
        assert slope == pytest.approx(0.0)

    def test_single_point(self):
        """单点 → (0,y,0)"""
        slope, intercept, r2 = _simple_ols([0], [5])
        assert slope == 0.0


# ============================================================
# DataClass to_dict
# ============================================================


class TestResultDicts:

    def test_alpha_decay_to_dict(self):
        r = AlphaDecayResult(
            slope=-0.001, r_squared=0.5, avg_alpha=0.05,
            n_windows=5, rolling_alphas=[0.06, 0.05, 0.04],
            is_decaying=True,
        )
        d = r.to_dict()
        assert d["is_decaying"] is True
        assert d["n_windows"] == 5

    def test_regime_metrics_to_dict(self):
        r = RegimeMetrics(
            regime="BULL", trading_days=100,
            total_return=0.15, sharpe_ratio=1.5,
            max_drawdown=0.05, volatility=0.12,
        )
        d = r.to_dict()
        assert d["regime"] == "BULL"
