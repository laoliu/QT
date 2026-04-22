"""多策略组合回测协调器测试

测试 multi_strategy_backtest 模块：
- 配置解析（parse_multi_strategy_config）
- 子策略配置生成（build_sub_strategy_config）
- NAV 合成（_combine_nav_series）
- 组合指标计算（_compute_portfolio_metrics）
- 相关性矩阵计算（_compute_correlation）
- 配权算法（_allocate_weights）
- 胜率/Calmar/月度收益/回撤/贡献度计算
- 进度 I/O（_write_combo_progress / _read_combo_progress）
"""

from __future__ import annotations

from datetime import date

import pytest

from aipha.backtest.multi_strategy_backtest import (
    MultiStrategyResult,
    SubStrategyResult,
    _combine_nav_series,
    _compute_correlation,
    _compute_portfolio_metrics,
    build_sub_strategy_config,
    parse_multi_strategy_config,
)

# New function imports for top-10 combo backtest
from aipha.backtest.multi_strategy_backtest import (
    ALLOCATION_MODE_MAP,
    _allocate_weights,
    _compute_calmar,
    _compute_contributions,
    _compute_drawdown_curve,
    _compute_monthly_returns,
    _compute_win_rate,
    _get_combo_progress_path,
    _read_combo_progress,
    _write_combo_progress,
)

import numpy as np
import pandas as pd


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def multi_config() -> dict:
    """多策略配置 dict"""
    return {
        "allocation_mode": "fixed_weight",
        "rebalance_freq": "monthly",
        "strategies": [
            {"name": "demo_dual_ma", "weight": 0.6, "config": {"short_period": 5, "long_period": 20}},
            {"name": "demo_mean_reversion", "weight": 0.4, "config": {"lookback": 20, "threshold": 2.0}},
        ],
    }


# ============================================================================
# 配置解析
# ============================================================================


class TestParseMultiStrategyConfig:
    """parse_multi_strategy_config 测试"""

    def test_parse_basic(self, multi_config: dict):
        """正确解析基本配置"""
        cfgs, weights, mode, freq = parse_multi_strategy_config(multi_config)
        assert len(cfgs) == 2
        assert weights[0] == 0.6
        assert weights[1] == 0.4
        assert mode == "fixed_weight"
        assert freq == "monthly"

    def test_missing_strategies_returns_empty(self):
        """缺少 strategies 返回空"""
        cfgs, weights, mode, freq = parse_multi_strategy_config({})
        assert cfgs == []
        assert weights == []

    def test_equal_mode_normalizes(self):
        """equal 模式下自动均分权重"""
        config = {
            "allocation_mode": "equal",
            "strategies": [
                {"name": "s1", "weight": 0.8, "config": {}},
                {"name": "s2", "weight": 0.2, "config": {}},
            ],
        }
        cfgs, weights, mode, freq = parse_multi_strategy_config(config)
        assert len(weights) == 2
        assert abs(weights[0] - 0.5) < 1e-10
        assert abs(weights[1] - 0.5) < 1e-10

    def test_defaults(self):
        """默认 mode=equal, freq=monthly"""
        config = {"strategies": [{"name": "s1", "weight": 1.0}]}
        _, _, mode, freq = parse_multi_strategy_config(config)
        assert mode == "equal"
        assert freq == "monthly"

    def test_config_extraction(self, multi_config: dict):
        """提取子策略 config"""
        cfgs, _, _, _ = parse_multi_strategy_config(multi_config)
        assert cfgs[0]["short_period"] == 5
        assert cfgs[1]["lookback"] == 20


# ============================================================================
# 子策略配置生成
# ============================================================================


class TestBuildSubStrategyConfig:
    """build_sub_strategy_config 测试"""

    def test_basic_build(self):
        """基本配置合并"""
        base = {"data": {"source": "synthetic"}, "initial_capital": 1_000_000}
        override = {"initial_capital": 500_000, "strategy": {"name": "dual_ma"}}
        result = build_sub_strategy_config(base, override)
        assert result["initial_capital"] == 500_000
        assert result["strategy"]["name"] == "dual_ma"
        assert result["data"]["source"] == "synthetic"

    def test_override_wins(self):
        """override 覆盖 base"""
        base = {"x": 1, "y": 2}
        override = {"y": 99}
        result = build_sub_strategy_config(base, override)
        assert result["x"] == 1
        assert result["y"] == 99

    def test_empty_override(self):
        """空 override 返回 base 副本"""
        base = {"a": 1}
        result = build_sub_strategy_config(base, {})
        assert result == {"a": 1}

    def test_does_not_mutate_base(self):
        """不修改原始 base"""
        base = {"x": 1}
        build_sub_strategy_config(base, {"x": 99})
        assert base["x"] == 1


# ============================================================================
# NAV 合成
# ============================================================================


class TestCombineNavSeriesOld:
    """_combine_nav_series 原有测试 (适配新 API)"""

    def test_single_strategy(self):
        """单策略 NAV"""
        dates = pd.to_datetime([date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8)])
        nav = pd.Series([100_000, 101_000, 102_000], index=dates)
        combined = _combine_nav_series([nav], [1.0])
        assert len(combined) == 3

    def test_two_strategies_weighted(self):
        """两个策略加权合成"""
        dates = pd.to_datetime([date(2025, 1, 6), date(2025, 1, 7)])
        nav1 = pd.Series([600_000, 612_000], index=dates)
        nav2 = pd.Series([400_000, 396_000], index=dates)
        combined = _combine_nav_series([nav1, nav2], [0.6, 0.4])
        assert len(combined) == 2

    def test_empty_results(self):
        """无结果返回空 Series"""
        combined = _combine_nav_series([], [])
        assert combined.empty

    def test_produces_series(self):
        """返回值是 pd.Series"""
        dates = pd.to_datetime([date(2025, 1, 6), date(2025, 1, 7)])
        nav = pd.Series([100_000, 110_000], index=dates)
        combined = _combine_nav_series([nav], [1.0])
        assert isinstance(combined, pd.Series)


# ============================================================================
# 组合指标计算
# ============================================================================


class TestComputePortfolioMetrics:
    """_compute_portfolio_metrics 测试"""

    def test_basic_metrics(self):
        """基本指标计算"""
        dates = pd.to_datetime([date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8),
                                date(2025, 1, 9), date(2025, 1, 10)])
        nav = pd.Series([100_000, 101_000, 102_000, 101_500, 103_000], index=dates)
        metrics = _compute_portfolio_metrics(nav)
        assert metrics["annual_return"] > 0
        assert 0 < metrics["max_drawdown"] < 1
        assert "sharpe" in metrics

    def test_empty_nav(self):
        """空 NAV → 全零"""
        metrics = _compute_portfolio_metrics(pd.Series(dtype=float))
        assert metrics["annual_return"] == 0.0
        assert metrics["max_drawdown"] == 0.0

    def test_single_day(self):
        """单日无法计算日收益率"""
        nav = pd.Series([100_000], index=pd.to_datetime([date(2025, 1, 6)]))
        metrics = _compute_portfolio_metrics(nav)
        assert metrics["annual_return"] == 0.0

    def test_max_drawdown_calculation(self):
        """最大回撤计算正确"""
        dates = pd.to_datetime([date(2025, 1, 6), date(2025, 1, 7),
                                date(2025, 1, 8), date(2025, 1, 9)])
        nav = pd.Series([100_000, 110_000, 99_000, 105_000], index=dates)
        metrics = _compute_portfolio_metrics(nav)
        assert metrics["max_drawdown"] == pytest.approx(11_000 / 110_000, rel=1e-3)


# ============================================================================
# 相关性矩阵
# ============================================================================


class TestComputeCorrelation:
    """_compute_correlation 测试"""

    def test_single_strategy_returns_empty(self):
        """少于2个策略返回空 DataFrame"""
        nav = pd.Series(
            [100_000 + d * 1000 for d in range(20)],
            index=pd.to_datetime([date(2025, 1, d) for d in range(6, 26)]),
        )
        corr = _compute_correlation([nav], ["s1"])
        assert corr.empty

    def test_two_strategies_perfect_correlation(self):
        """完全正相关"""
        days = pd.to_datetime([date(2025, 1, d) for d in range(6, 26)])
        nav1 = pd.Series([100_000 + i * 1000 for i in range(20)], index=days)
        nav2 = pd.Series([200_000 + i * 2000 for i in range(20)], index=days)
        corr = _compute_correlation([nav1, nav2], ["s1", "s2"])
        assert not corr.empty
        assert corr.loc["s1", "s1"] == pytest.approx(1.0)
        assert corr.loc["s1", "s2"] == pytest.approx(1.0, abs=0.01)

    def test_insufficient_common_dates(self):
        """共同日期不足 → still returns DataFrame (even if small)"""
        days = pd.to_datetime([date(2025, 1, 6), date(2025, 1, 7)])
        nav1 = pd.Series([100_000, 101_000], index=days)
        nav2 = pd.Series([200_000, 199_000], index=days)
        # With only 2 dates, pct_change gives 1 row after dropna → corr is computed
        corr = _compute_correlation([nav1, nav2], ["s1", "s2"])
        assert isinstance(corr, pd.DataFrame)


# ============================================================================
# MultiStrategyResult 数据结构
# ============================================================================


class TestMultiStrategyResultOld:
    """MultiStrategyResult 数据结构（适配新签名）"""

    def test_minimal_construction(self):
        """最小构造（必须传 sub_results + combined_nav）"""
        nav = pd.Series([1.0, 1.01], index=pd.bdate_range("2025-01-06", periods=2))
        r = MultiStrategyResult(sub_results=[], combined_nav=nav)
        assert r.sub_results == []
        assert r.combined_annual_return == 0.0
        assert r.combined_max_drawdown == 0.0
        assert r.combined_sharpe == 0.0
        assert r.allocation_mode == "equal"
        assert r.correlation_matrix is None

    def test_with_data(self):
        """带数据构造"""
        nav = pd.Series([1.0, 1.1], index=pd.bdate_range("2025-01-06", periods=2))
        sr = SubStrategyResult(name="test", weight=1.0, nav_series=nav)
        r = MultiStrategyResult(
            sub_results=[sr],
            combined_nav=nav,
            combined_annual_return=0.15,
            combined_max_drawdown=0.10,
            combined_sharpe=1.5,
        )
        assert len(r.sub_results) == 1
        assert r.combined_sharpe == 1.5

    def test_to_dict(self):
        """to_dict 输出正确字段"""
        nav = pd.Series([1.0, 1.05], index=pd.bdate_range("2025-01-06", periods=2))
        sr = SubStrategyResult(name="s1", weight=0.5, nav_series=nav, sharpe=1.2)
        r = MultiStrategyResult(
            sub_results=[sr],
            combined_nav=nav,
            combined_sharpe=1.5,
        )
        d = r.to_dict()
        assert "allocation_mode" in d
        assert "combined_sharpe" in d
        assert len(d["sub_results"]) == 1


# ============================================================================
# 配权算法 (_allocate_weights)
# ============================================================================


def _make_nav(seed: int, drift: float = 0.0005, vol: float = 0.01, n: int = 100) -> pd.Series:
    """创建一个随机 NAV Series 用于测试配权。"""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2024-01-02", periods=n)
    vals = 1e6 * (1 + rng.normal(drift, vol, n)).cumprod()
    return pd.Series(vals, index=dates)


class TestAllocateWeights:
    """_allocate_weights 配权算法测试"""

    def test_empty_list(self):
        assert _allocate_weights([]) == []

    def test_single_strategy(self):
        w = _allocate_weights([_make_nav(42)])
        assert w == [1.0]

    def test_equal_mode(self):
        navs = [_make_nav(i) for i in range(3)]
        w = _allocate_weights(navs, mode="equal")
        assert len(w) == 3
        for wi in w:
            assert abs(wi - 1 / 3) < 1e-10

    @pytest.mark.parametrize("mode", [
        "equal", "risk_parity", "inverse_vol", "max_sharpe",
        "min_variance", "target_vol", "max_diversification",
    ])
    def test_weights_sum_to_one(self, mode):
        navs = [_make_nav(i) for i in range(4)]
        w = _allocate_weights(navs, mode=mode)
        assert abs(sum(w) - 1.0) < 1e-6, f"{mode}: sum={sum(w)}"

    @pytest.mark.parametrize("mode", [
        "equal", "risk_parity", "inverse_vol", "max_sharpe",
        "min_variance", "target_vol", "max_diversification",
    ])
    def test_weights_non_negative(self, mode):
        navs = [_make_nav(i) for i in range(4)]
        w = _allocate_weights(navs, mode=mode)
        for wi in w:
            assert wi >= -1e-10, f"{mode}: negative weight {wi}"

    def test_custom_weights(self):
        navs = [_make_nav(i) for i in range(3)]
        w = _allocate_weights(navs, mode="custom", custom_weights=[0.5, 0.3, 0.2])
        assert abs(w[0] - 0.5) < 1e-10

    def test_custom_weights_normalized(self):
        navs = [_make_nav(i) for i in range(3)]
        w = _allocate_weights(navs, mode="custom", custom_weights=[2, 3, 5])
        assert abs(sum(w) - 1.0) < 1e-10
        assert abs(w[0] - 0.2) < 1e-10

    def test_custom_wrong_length_fallback(self):
        navs = [_make_nav(i) for i in range(3)]
        w = _allocate_weights(navs, mode="custom", custom_weights=[0.5, 0.5])
        assert len(w) == 3

    def test_unknown_mode_fallback(self):
        navs = [_make_nav(i) for i in range(3)]
        w = _allocate_weights(navs, mode="something_unknown")
        for wi in w:
            assert abs(wi - 1 / 3) < 1e-10

    def test_inverse_vol_favors_low_vol(self):
        # nav0 has low vol, nav1 has high vol
        nav_low = _make_nav(42, vol=0.005)
        nav_high = _make_nav(7, vol=0.03)
        w = _allocate_weights([nav_low, nav_high], mode="inverse_vol")
        assert w[0] > w[1]

    def test_short_series_fallback(self):
        dates = pd.bdate_range("2024-01-02", periods=3)
        navs = [pd.Series([100, 101, 102], index=dates),
                pd.Series([100, 99, 98], index=dates)]
        w = _allocate_weights(navs, mode="risk_parity")
        assert abs(sum(w) - 1.0) < 1e-6


# ============================================================================
# 胜率/Calmar/月度收益/回撤曲线
# ============================================================================


class TestComputeWinRate:
    def test_rising_nav(self):
        nav = _make_nav(42, drift=0.002)
        wr = _compute_win_rate(nav)
        assert 0.0 <= wr <= 1.0
        assert wr > 0.4

    def test_empty(self):
        assert _compute_win_rate(pd.Series(dtype=float)) == 0.0

    def test_single_point(self):
        assert _compute_win_rate(pd.Series([100.0])) == 0.0

    def test_flat(self):
        dates = pd.bdate_range("2024-01-02", periods=50)
        flat = pd.Series([1e6] * 50, index=dates)
        assert _compute_win_rate(flat) == 0.0


class TestComputeCalmar:
    def test_rising(self):
        nav = _make_nav(42, drift=0.002)
        assert _compute_calmar(nav) > 0

    def test_empty(self):
        assert _compute_calmar(pd.Series(dtype=float)) == 0.0

    def test_single_point(self):
        assert _compute_calmar(pd.Series([100.0])) == 0.0

    def test_flat(self):
        dates = pd.bdate_range("2024-01-02", periods=50)
        flat = pd.Series([1e6] * 50, index=dates)
        assert _compute_calmar(flat) == 0.0


class TestComputeMonthlyReturns:
    def test_format(self):
        nav = _make_nav(42)
        mr = _compute_monthly_returns(nav)
        assert isinstance(mr, list)
        for m in mr:
            assert "month" in m and "return" in m
            assert len(m["month"]) == 7

    def test_empty(self):
        assert _compute_monthly_returns(pd.Series(dtype=float)) == []


class TestComputeDrawdownCurve:
    def test_values_non_positive(self):
        nav = _make_nav(42)
        dd = _compute_drawdown_curve(nav)
        for p in dd:
            assert p["drawdown"] <= 0.001  # rounding tolerance

    def test_empty(self):
        assert _compute_drawdown_curve(pd.Series(dtype=float)) == []

    def test_capped_at_500(self):
        dates = pd.bdate_range("2020-01-02", periods=2000)
        nav = pd.Series(np.linspace(100, 110, 2000), index=dates)
        dd = _compute_drawdown_curve(nav)
        assert len(dd) <= 510  # ~500 + tolerance


# ============================================================================
# 贡献度
# ============================================================================


class TestComputeContributions:
    def test_basic(self):
        navs = [_make_nav(i) for i in range(3)]
        subs = [SubStrategyResult(name=f"s{i}", weight=1 / 3, nav_series=n)
                for i, n in enumerate(navs)]
        combined_nav = pd.Series(
            sum(n * (1 / 3) for n in navs),
        )
        # Use first nav's index
        combined_nav.index = navs[0].index
        contribs = _compute_contributions(subs, combined_nav)
        assert len(contribs) == 3
        assert all("strategy_id" in c for c in contribs)

    def test_empty(self):
        combined = pd.Series([100, 110], index=pd.bdate_range("2024-01-02", periods=2))
        assert _compute_contributions([], combined) == []


# ============================================================================
# 进度 I/O
# ============================================================================


class TestProgressIO:
    def test_write_and_read(self, tmp_path, monkeypatch):
        progress_file = tmp_path / "combo-backtest-progress.json"
        monkeypatch.setattr(
            "aipha.backtest.multi_strategy_backtest._get_combo_progress_path",
            lambda: str(progress_file),
        )
        _write_combo_progress({"status": "running", "phase": "test"})
        result = _read_combo_progress()
        assert result["status"] == "running"
        assert result["phase"] == "test"

    def test_read_missing_returns_idle(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "aipha.backtest.multi_strategy_backtest._get_combo_progress_path",
            lambda: str(tmp_path / "nonexistent.json"),
        )
        result = _read_combo_progress()
        assert result.get("status") in ("idle", None) or result == {}


# ============================================================================
# ALLOCATION_MODE_MAP
# ============================================================================


class TestAllocationModeMap:
    def test_all_modes(self):
        expected = {"equal", "risk_parity", "inverse_vol", "max_sharpe",
                    "min_variance", "target_vol", "max_diversification", "custom"}
        assert set(ALLOCATION_MODE_MAP.keys()) == expected

    def test_labels_nonempty(self):
        for label in ALLOCATION_MODE_MAP.values():
            assert len(label) >= 2
