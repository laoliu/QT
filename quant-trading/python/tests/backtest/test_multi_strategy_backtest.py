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
from pathlib import Path

import pytest
import yaml

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

MINIMAL_MULTI_YML = """
name: test_multi
data:
  source: synthetic
  start_date: "2025-01-01"
  end_date: "2025-06-01"
  symbols: ["000001.SZ", "000002.SZ"]
initial_capital: 1000000
strategy:
  name: unused

portfolio:
  allocation_mode: fixed_weight
  strategies:
    - name: demo_dual_ma
      weight: 0.6
      config:
        short_period: 5
        long_period: 20
    - name: demo_mean_reversion
      weight: 0.4
      config:
        lookback: 20
        threshold: 2.0
"""


@pytest.fixture
def multi_yml(tmp_path: Path) -> Path:
    """创建临时多策略配置文件"""
    p = tmp_path / "multi.yml"
    p.write_text(MINIMAL_MULTI_YML, encoding="utf-8")
    return p


# ============================================================================
# 配置解析
# ============================================================================


class TestParseMultiStrategyConfig:
    """parse_multi_strategy_config 测试"""

    def test_parse_basic(self, multi_yml: Path):
        """正确解析基本配置"""
        shared, strategies, risk_control = parse_multi_strategy_config(multi_yml)
        assert len(strategies) == 2
        assert strategies[0]["name"] == "demo_dual_ma"
        assert strategies[0]["weight"] == 0.6
        assert strategies[1]["name"] == "demo_mean_reversion"
        assert strategies[1]["weight"] == 0.4
        # shared 不含 portfolio
        assert "portfolio" not in shared
        # 基本配置无 risk_control
        assert risk_control is None

    def test_missing_portfolio_raises(self, tmp_path: Path):
        """缺少 portfolio.strategies 应抛异常"""
        p = tmp_path / "no_portfolio.yml"
        p.write_text("""
name: test
data:
  source: synthetic
  start_date: "2025-01-01"
  end_date: "2025-06-01"
strategy:
  name: dual_ma
""", encoding="utf-8")
        with pytest.raises(ValueError, match="portfolio.strategies"):
            parse_multi_strategy_config(p)

    def test_empty_strategies_raises(self, tmp_path: Path):
        """strategies 为空列表应抛异常"""
        p = tmp_path / "empty.yml"
        p.write_text("""
name: test
data:
  source: synthetic
  start_date: "2025-01-01"
  end_date: "2025-06-01"
portfolio:
  strategies: []
""", encoding="utf-8")
        with pytest.raises(ValueError, match="非空列表"):
            parse_multi_strategy_config(p)

    def test_file_not_found_raises(self):
        """配置文件不存在"""
        with pytest.raises(FileNotFoundError):
            parse_multi_strategy_config("/nonexistent/path.yml")

    def test_weight_normalization_warning(self, tmp_path: Path, caplog):
        """权重之和不为1时应有警告"""
        p = tmp_path / "bad_weight.yml"
        p.write_text("""
name: test
data:
  source: synthetic
  start_date: "2025-01-01"
  end_date: "2025-06-01"
portfolio:
  strategies:
    - name: dual_ma
      weight: 0.3
    - name: mean_reversion
      weight: 0.3
""", encoding="utf-8")
        with caplog.at_level("WARNING"):
            shared, strategies, risk_control = parse_multi_strategy_config(p)
        assert any("归一化" in msg for msg in caplog.messages)


# ============================================================================
# 子策略配置生成
# ============================================================================


class TestBuildSubStrategyConfig:
    """build_sub_strategy_config 测试"""

    def test_basic_build(self):
        """基本配置生成"""
        shared = {
            "name": "multi_test",
            "data": {"source": "synthetic", "start_date": "2025-01-01", "end_date": "2025-06-01"},
            "initial_capital": 1_000_000,
        }
        sub = {"name": "demo_dual_ma", "weight": 0.6, "config": {"short_period": 5}}

        result = build_sub_strategy_config(shared, sub, 1_000_000)
        assert result["initial_capital"] == 600_000
        assert result["strategy"]["name"] == "demo_dual_ma"
        assert result["strategy"]["params"]["short_period"] == 5
        assert "demo_dual_ma" in result["name"]

    def test_output_dir_includes_name(self):
        """输出目录包含子策略名"""
        shared = {"data": {}, "output_dir": "output/test"}
        sub = {"name": "my_strat", "weight": 0.5}
        result = build_sub_strategy_config(shared, sub, 100_000)
        assert "my_strat" in result["output_dir"]

    def test_capital_allocation(self):
        """资金分配正确"""
        shared = {"data": {}}
        sub = {"name": "s1", "weight": 0.25}
        result = build_sub_strategy_config(shared, sub, 2_000_000)
        assert result["initial_capital"] == 500_000

    def test_missing_config_defaults_to_empty(self):
        """子策略无 config 时 params 为空 dict"""
        shared = {"data": {}}
        sub = {"name": "s1", "weight": 0.5}
        result = build_sub_strategy_config(shared, sub, 100_000)
        assert result["strategy"]["params"] == {}


# ============================================================================
# NAV 合成
# ============================================================================


class TestCombineNavSeries:
    """_combine_nav_series 测试"""

    def test_single_strategy(self):
        """单策略 NAV 直接等于自身"""
        sr = SubStrategyResult(
            name="s1",
            weight=1.0,
            nav_series={
                date(2025, 1, 6): 100_000,
                date(2025, 1, 7): 101_000,
                date(2025, 1, 8): 102_000,
            },
        )
        combined = _combine_nav_series([sr], 100_000)
        assert len(combined) == 3
        assert combined[date(2025, 1, 7)] == pytest.approx(101_000)

    def test_two_strategies_weighted(self):
        """两个策略加权合成"""
        sr1 = SubStrategyResult(
            name="s1", weight=0.6,
            nav_series={
                date(2025, 1, 6): 600_000,  # 60% of 1M
                date(2025, 1, 7): 612_000,  # +2%
            },
        )
        sr2 = SubStrategyResult(
            name="s2", weight=0.4,
            nav_series={
                date(2025, 1, 6): 400_000,  # 40% of 1M
                date(2025, 1, 7): 396_000,  # -1%
            },
        )
        combined = _combine_nav_series([sr1, sr2], 1_000_000)
        assert combined[date(2025, 1, 6)] == pytest.approx(1_000_000)
        # 612000 + 396000 = 1008000
        assert combined[date(2025, 1, 7)] == pytest.approx(1_008_000)

    def test_empty_results(self):
        """无结果返回空 dict"""
        assert _combine_nav_series([], 100_000) == {}

    def test_failed_strategy_uses_initial(self):
        """失败策略的 NAV 贡献 = weight * total_capital"""
        sr_ok = SubStrategyResult(
            name="ok", weight=0.7,
            nav_series={date(2025, 1, 6): 700_000},
        )
        sr_fail = SubStrategyResult(name="fail", weight=0.3, nav_series={})
        combined = _combine_nav_series([sr_ok, sr_fail], 1_000_000)
        # ok: 700000, fail: 0.3 * 1M = 300000
        assert combined[date(2025, 1, 6)] == pytest.approx(1_000_000)


# ============================================================================
# 组合指标计算
# ============================================================================


class TestComputePortfolioMetrics:
    """_compute_portfolio_metrics 测试"""

    def test_basic_metrics(self):
        """基本指标计算"""
        nav = {
            date(2025, 1, 6): 100_000,
            date(2025, 1, 7): 101_000,
            date(2025, 1, 8): 102_000,
            date(2025, 1, 9): 101_500,
            date(2025, 1, 10): 103_000,
        }
        metrics = _compute_portfolio_metrics(nav, 100_000)
        assert metrics["annual_return"] > 0
        assert 0 < metrics["max_drawdown"] < 1
        assert "sharpe" in metrics

    def test_empty_nav(self):
        """空 NAV → 全零"""
        metrics = _compute_portfolio_metrics({}, 100_000)
        assert metrics["annual_return"] == 0.0
        assert metrics["max_drawdown"] == 0.0

    def test_single_day(self):
        """单日无法计算日收益率"""
        nav = {date(2025, 1, 6): 100_000}
        metrics = _compute_portfolio_metrics(nav, 100_000)
        assert metrics["annual_return"] == 0.0

    def test_max_drawdown_calculation(self):
        """最大回撤计算正确"""
        nav = {
            date(2025, 1, 6): 100_000,
            date(2025, 1, 7): 110_000,
            date(2025, 1, 8): 99_000,   # 从 110k 跌到 99k = 10%
            date(2025, 1, 9): 105_000,
        }
        metrics = _compute_portfolio_metrics(nav, 100_000)
        assert metrics["max_drawdown"] == pytest.approx(11_000 / 110_000, rel=1e-3)


# ============================================================================
# 相关性矩阵
# ============================================================================


class TestComputeCorrelation:
    """_compute_correlation 测试"""

    def test_single_strategy_returns_none(self):
        """少于2个策略返回 None"""
        sr = SubStrategyResult(
            name="s1", weight=1.0,
            nav_series={date(2025, 1, d): 100_000 + d * 1000 for d in range(6, 26)},
        )
        assert _compute_correlation([sr]) is None

    def test_two_strategies_perfect_correlation(self):
        """完全正相关"""
        days = [date(2025, 1, d) for d in range(6, 26)]
        nav1 = {d: 100_000 + i * 1000 for i, d in enumerate(days)}
        nav2 = {d: 200_000 + i * 2000 for i, d in enumerate(days)}

        sr1 = SubStrategyResult(name="s1", weight=0.5, nav_series=nav1)
        sr2 = SubStrategyResult(name="s2", weight=0.5, nav_series=nav2)
        corr = _compute_correlation([sr1, sr2])
        assert corr is not None
        assert corr["s1"]["s1"] == pytest.approx(1.0)
        assert corr["s1"]["s2"] == pytest.approx(1.0, abs=0.01)

    def test_insufficient_common_dates(self):
        """共同日期不足10个返回 None"""
        sr1 = SubStrategyResult(
            name="s1", weight=0.5,
            nav_series={date(2025, 1, 6): 100_000, date(2025, 1, 7): 101_000},
        )
        sr2 = SubStrategyResult(
            name="s2", weight=0.5,
            nav_series={date(2025, 1, 6): 200_000, date(2025, 1, 7): 199_000},
        )
        assert _compute_correlation([sr1, sr2]) is None


# ============================================================================
# MultiStrategyResult 数据结构
# ============================================================================


class TestMultiStrategyResult:
    """MultiStrategyResult 数据结构"""

    def test_default_construction(self):
        """默认构造"""
        r = MultiStrategyResult()
        assert r.sub_results == []
        assert r.combined_nav == {}
        assert r.portfolio_annual_return == 0.0
        assert r.portfolio_max_drawdown == 0.0
        assert r.portfolio_sharpe == 0.0
        assert r.allocation_mode == "fixed_weight"
        assert r.risk_control_config is None
        assert r.adaptive_result is None

    def test_with_data(self):
        """带数据构造"""
        sr = SubStrategyResult(name="test", weight=1.0)
        r = MultiStrategyResult(
            sub_results=[sr],
            portfolio_annual_return=0.15,
            portfolio_max_drawdown=0.10,
            portfolio_sharpe=1.5,
            initial_capital=1_000_000,
        )
        assert len(r.sub_results) == 1
        assert r.portfolio_sharpe == 1.5


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
