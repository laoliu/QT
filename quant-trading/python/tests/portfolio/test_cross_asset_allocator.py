"""Tests for CrossAssetAllocator — 跨资产组合配置器。

测试覆盖（≥15 用例 per §6.2 组合模块）：
- 配置 & 构造
- 工具函数：收益率、波动率、协方差、相关系数
- 配权方法：equal_weight, inverse_vol, risk_parity, min_variance
- 核心流程：add_strategy → compute
- 权重约束：clip_weights
- 边界情况：空策略、单策略、数据不足
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from aipha.portfolio.cross_asset_allocator import (
    AssetClass,
    CrossAssetAllocator,
    CrossAssetConfig,
    CrossAssetMethod,
    CrossAssetResult,
    compute_annualized_vol,
    compute_correlation_matrix,
    compute_covariance_matrix,
    compute_returns,
    compute_volatility,
    _equal_weight,
    _inverse_volatility,
    _min_variance,
    _risk_parity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_nav(start: float = 1.0, n_days: int = 100, daily_ret: float = 0.001) -> dict[date, float]:
    """生成模拟 NAV 序列。"""
    from datetime import timedelta
    nav = {}
    val = start
    for i in range(n_days):
        d = date(2024, 1, 1) + timedelta(days=i)
        nav[d] = val
        val *= (1 + daily_ret)
    return nav


def _make_nav_simple(
    returns: list[float],
    start_date: date = date(2024, 1, 1),
) -> dict[date, float]:
    """从收益率列表生成 NAV。"""
    from datetime import timedelta
    nav = {start_date: 1.0}
    val = 1.0
    for i, r in enumerate(returns):
        val *= (1 + r)
        d = start_date + timedelta(days=i + 1)
        nav[d] = val
    return nav


# ===========================================================================
# 1. 枚举 & 配置 (4 tests)
# ===========================================================================

class TestEnumsAndConfig:
    def test_asset_class_values(self):
        assert str(AssetClass.CB) == "cb"
        assert str(AssetClass.A_STOCK) == "a_stock"

    def test_method_values(self):
        assert str(CrossAssetMethod.RISK_PARITY) == "risk_parity"
        assert str(CrossAssetMethod.HRP) == "hierarchical_risk_parity"

    def test_default_config(self):
        cfg = CrossAssetConfig()
        assert cfg.method == CrossAssetMethod.RISK_PARITY
        assert cfg.lookback == 60
        assert cfg.min_class_weight == 0.10
        assert cfg.max_class_weight == 0.80

    def test_custom_config(self):
        cfg = CrossAssetConfig(method=CrossAssetMethod.EQUAL_WEIGHT, lookback=30)
        assert cfg.method == CrossAssetMethod.EQUAL_WEIGHT
        assert cfg.lookback == 30


# ===========================================================================
# 2. 工具函数 (8 tests)
# ===========================================================================

class TestUtilFunctions:

    def test_compute_returns_basic(self):
        nav = {date(2024, 1, 1): 1.0, date(2024, 1, 2): 1.01, date(2024, 1, 3): 1.02}
        rets = compute_returns(nav)
        assert len(rets) == 2
        assert abs(rets[0][1] - 0.01) < 1e-6
        assert abs(rets[1][1] - (1.02 / 1.01 - 1)) < 1e-6

    def test_compute_returns_empty(self):
        assert compute_returns({}) == []
        assert compute_returns({date(2024, 1, 1): 1.0}) == []

    def test_compute_volatility_constant(self):
        rets = [0.01] * 10
        vol = compute_volatility(rets)
        assert vol < 1e-10  # 常数收益 → 0 波动

    def test_compute_volatility_varying(self):
        rets = [0.02, -0.01, 0.03, -0.02, 0.01]
        vol = compute_volatility(rets)
        assert vol > 0

    def test_compute_annualized_vol(self):
        rets = [0.01, -0.01, 0.01, -0.01] * 30
        ann_vol = compute_annualized_vol(rets)
        daily_vol = compute_volatility(rets)
        assert abs(ann_vol - daily_vol * math.sqrt(252)) < 1e-10

    def test_covariance_matrix_identity(self):
        """相同序列的协方差 = 方差。"""
        rets = {"A": [0.01, -0.01, 0.02, -0.02, 0.01]}
        cov = compute_covariance_matrix(rets)
        var = compute_volatility(rets["A"]) ** 2 * (len(rets["A"]) - 1) / (len(rets["A"]) - 1)
        assert abs(cov["A"]["A"] - var) < 1e-8

    def test_covariance_symmetric(self):
        rets = {
            "A": [0.01, -0.01, 0.02, -0.02, 0.01],
            "B": [0.02, -0.015, 0.01, -0.01, 0.005],
        }
        cov = compute_covariance_matrix(rets)
        assert abs(cov["A"]["B"] - cov["B"]["A"]) < 1e-12

    def test_correlation_diagonal_is_one(self):
        rets = {
            "A": [0.01, -0.01, 0.02, -0.02, 0.01],
            "B": [0.02, -0.015, 0.01, -0.01, 0.005],
        }
        cov = compute_covariance_matrix(rets)
        corr = compute_correlation_matrix(cov)
        assert abs(corr["A"]["A"] - 1.0) < 1e-8
        assert abs(corr["B"]["B"] - 1.0) < 1e-8
        assert -1.0 <= corr["A"]["B"] <= 1.0


# ===========================================================================
# 3. 配权方法 (6 tests)
# ===========================================================================

class TestAllocationMethods:

    def test_equal_weight(self):
        w = _equal_weight(["A", "B", "C"])
        assert len(w) == 3
        assert abs(sum(w.values()) - 1.0) < 1e-10
        assert abs(w["A"] - 1 / 3) < 1e-10

    def test_equal_weight_empty(self):
        assert _equal_weight([]) == {}

    def test_inverse_volatility(self):
        rets = {
            "LOW_VOL": [0.001] * 50 + [-0.001] * 50,
            "HIGH_VOL": [0.02] * 50 + [-0.02] * 50,
        }
        w = _inverse_volatility(["LOW_VOL", "HIGH_VOL"], rets)
        assert w["LOW_VOL"] > w["HIGH_VOL"]  # 低波动应获得更高权重
        assert abs(sum(w.values()) - 1.0) < 1e-8

    def test_risk_parity_two_assets(self):
        rets = {
            "A": [0.01, -0.005, 0.008, -0.003, 0.005] * 20,
            "B": [0.02, -0.015, 0.012, -0.008, 0.01] * 20,
        }
        cov = compute_covariance_matrix(rets)
        w = _risk_parity(["A", "B"], cov)
        assert abs(sum(w.values()) - 1.0) < 1e-6
        # 低波资产应获得更高权重
        assert w["A"] > w["B"]

    def test_risk_parity_single(self):
        w = _risk_parity(["X"], {"X": {"X": 0.01}})
        assert w == {"X": 1.0}

    def test_min_variance(self):
        cov = {
            "A": {"A": 0.01, "B": 0.002},
            "B": {"A": 0.002, "B": 0.04},
        }
        w = _min_variance(["A", "B"], cov)
        assert abs(sum(w.values()) - 1.0) < 1e-8
        assert w["A"] > w["B"]  # A 方差更小

# ===========================================================================
# 4. CrossAssetAllocator 核心流程 (10 tests)
# ===========================================================================

class TestCrossAssetAllocator:

    def test_empty_compute(self):
        alloc = CrossAssetAllocator()
        result = alloc.compute()
        assert result.metadata.get("error") == "no strategies"

    def test_add_strategy(self):
        alloc = CrossAssetAllocator()
        nav = _make_nav(n_days=50)
        alloc.add_strategy("test_strat", AssetClass.CB, nav)
        assert "test_strat" in alloc.list_strategies()
        assert alloc.list_strategies()["test_strat"] == "cb"

    def test_add_empty_nav_raises(self):
        alloc = CrossAssetAllocator()
        with pytest.raises(ValueError, match="empty"):
            alloc.add_strategy("bad", AssetClass.CB, {})

    def test_single_strategy_compute(self):
        cfg = CrossAssetConfig(lookback=10, method=CrossAssetMethod.EQUAL_WEIGHT)
        alloc = CrossAssetAllocator(cfg)
        nav = _make_nav(n_days=50)
        alloc.add_strategy("only", AssetClass.CB, nav)
        result = alloc.compute()
        assert abs(result.strategy_weights["only"] - 1.0) < 1e-6
        assert len(result.portfolio_nav) == 50

    def test_two_class_equal_weight(self):
        cfg = CrossAssetConfig(
            method=CrossAssetMethod.EQUAL_WEIGHT,
            lookback=20,
            min_class_weight=0.0,
            max_class_weight=1.0,
            min_strategy_weight=0.0,
            max_strategy_weight=1.0,
        )
        alloc = CrossAssetAllocator(cfg)
        nav_cb = _make_nav(n_days=80, daily_ret=0.001)
        nav_eq = _make_nav(n_days=80, daily_ret=0.002)
        alloc.add_strategy("cb_strat", AssetClass.CB, nav_cb)
        alloc.add_strategy("eq_strat", AssetClass.A_STOCK, nav_eq)
        result = alloc.compute()
        assert abs(result.class_weights["cb"] - 0.5) < 1e-6
        assert abs(result.class_weights["a_stock"] - 0.5) < 1e-6

    def test_risk_parity_compute(self):
        cfg = CrossAssetConfig(
            method=CrossAssetMethod.RISK_PARITY,
            lookback=20,
            min_class_weight=0.05,
            max_class_weight=0.95,
            min_strategy_weight=0.01,
            max_strategy_weight=0.99,
        )
        alloc = CrossAssetAllocator(cfg)
        # CB: 低波动
        nav_cb = _make_nav_simple([0.001, -0.0005, 0.0008, -0.0003, 0.0005] * 20)
        # A-stock: 高波动
        nav_eq = _make_nav_simple([0.02, -0.015, 0.012, -0.008, 0.01] * 20)
        alloc.add_strategy("cb_strat", AssetClass.CB, nav_cb)
        alloc.add_strategy("eq_strat", AssetClass.A_STOCK, nav_eq)
        result = alloc.compute()
        # 风险平价 → 低波资产获得更高权重
        assert result.strategy_weights["cb_strat"] > result.strategy_weights["eq_strat"]

    def test_portfolio_nav_generated(self):
        cfg = CrossAssetConfig(
            method=CrossAssetMethod.EQUAL_WEIGHT,
            lookback=10,
            min_class_weight=0.0, max_class_weight=1.0,
            min_strategy_weight=0.0, max_strategy_weight=1.0,
        )
        alloc = CrossAssetAllocator(cfg)
        nav = _make_nav(n_days=50)
        alloc.add_strategy("s1", AssetClass.CB, nav)
        result = alloc.compute()
        assert len(result.portfolio_nav) == 50
        dates = sorted(result.portfolio_nav.keys())
        # 首日应为 1.0
        assert abs(result.portfolio_nav[dates[0]] - 1.0) < 1e-6

    def test_multiple_strategies_per_class(self):
        cfg = CrossAssetConfig(
            method=CrossAssetMethod.EQUAL_WEIGHT,
            lookback=10,
            min_class_weight=0.0, max_class_weight=1.0,
            min_strategy_weight=0.0, max_strategy_weight=1.0,
        )
        alloc = CrossAssetAllocator(cfg)
        nav1 = _make_nav(n_days=50, daily_ret=0.001)
        nav2 = _make_nav(n_days=50, daily_ret=0.002)
        nav3 = _make_nav(n_days=50, daily_ret=0.003)
        alloc.add_strategy("cb1", AssetClass.CB, nav1)
        alloc.add_strategy("cb2", AssetClass.CB, nav2)
        alloc.add_strategy("eq1", AssetClass.A_STOCK, nav3)

        result = alloc.compute()
        # 2 个 CB 策略各占 CW_cb/2, 1 个 A股 占 CW_eq
        assert len(result.strategy_weights) == 3
        assert abs(sum(result.strategy_weights.values()) - 1.0) < 1e-6

    def test_correlation_matrix_returned(self):
        cfg = CrossAssetConfig(lookback=10, min_class_weight=0.0, max_class_weight=1.0,
                               min_strategy_weight=0.0, max_strategy_weight=1.0)
        alloc = CrossAssetAllocator(cfg)
        nav1 = _make_nav(n_days=50, daily_ret=0.001)
        nav2 = _make_nav(n_days=50, daily_ret=0.002)
        alloc.add_strategy("s1", AssetClass.CB, nav1)
        alloc.add_strategy("s2", AssetClass.A_STOCK, nav2)
        result = alloc.compute()
        assert "s1" in result.correlation_matrix
        assert "s2" in result.correlation_matrix["s1"]

    def test_metadata_populated(self):
        cfg = CrossAssetConfig(lookback=10, min_class_weight=0.0, max_class_weight=1.0,
                               min_strategy_weight=0.0, max_strategy_weight=1.0)
        alloc = CrossAssetAllocator(cfg)
        nav = _make_nav(n_days=50)
        alloc.add_strategy("s1", AssetClass.CB, nav)
        result = alloc.compute()
        assert "method" in result.metadata
        assert result.metadata["n_strategies"] == 1


# ===========================================================================
# 5. 权重约束 (3 tests)
# ===========================================================================

class TestClipWeights:

    def test_clip_below_min(self):
        w = {"A": 0.02, "B": 0.98}
        clipped = CrossAssetAllocator._clip_weights(w, min_w=0.10, max_w=0.90)
        assert clipped["A"] >= 0.10
        assert clipped["B"] <= 0.90
        assert abs(sum(clipped.values()) - 1.0) < 1e-8

    def test_clip_above_max(self):
        w = {"A": 0.95, "B": 0.05}
        clipped = CrossAssetAllocator._clip_weights(w, min_w=0.10, max_w=0.80)
        # 归一化后总和为 1.0；max 裁到 0.8, min 提到 0.1 → 归一化后比例保持
        assert abs(sum(clipped.values()) - 1.0) < 1e-8
        # clipped A 应小于等于原来的 0.95（被约束降低了）
        assert clipped["A"] < 0.95

    def test_clip_empty(self):
        assert CrossAssetAllocator._clip_weights({}, 0.1, 0.9) == {}


# ===========================================================================
# 6. 边界情况 (4 tests)
# ===========================================================================

class TestEdgeCases:

    def test_insufficient_data_fallback(self):
        cfg = CrossAssetConfig(lookback=200)  # way more than data
        alloc = CrossAssetAllocator(cfg)
        nav = _make_nav(n_days=50)
        alloc.add_strategy("s1", AssetClass.CB, nav)
        result = alloc.compute()
        assert result.metadata.get("method") == "fallback_equal_weight"

    def test_identical_navs(self):
        cfg = CrossAssetConfig(lookback=10, min_class_weight=0.0, max_class_weight=1.0,
                               min_strategy_weight=0.0, max_strategy_weight=1.0)
        alloc = CrossAssetAllocator(cfg)
        nav = _make_nav(n_days=50, daily_ret=0.001)
        alloc.add_strategy("s1", AssetClass.CB, nav)
        alloc.add_strategy("s2", AssetClass.A_STOCK, dict(nav))  # same nav
        result = alloc.compute()
        assert abs(sum(result.strategy_weights.values()) - 1.0) < 1e-6

    def test_zero_return_nav(self):
        """零收益 NAV 不崩溃。"""
        cfg = CrossAssetConfig(lookback=10, min_class_weight=0.0, max_class_weight=1.0,
                               min_strategy_weight=0.0, max_strategy_weight=1.0)
        alloc = CrossAssetAllocator(cfg)
        nav = _make_nav(n_days=50, daily_ret=0.0)
        alloc.add_strategy("s1", AssetClass.CB, nav)
        result = alloc.compute()
        assert len(result.portfolio_nav) > 0

    def test_three_asset_classes(self):
        """三个资产类别也能工作。"""
        cfg = CrossAssetConfig(
            method=CrossAssetMethod.EQUAL_WEIGHT,
            lookback=10,
            min_class_weight=0.0, max_class_weight=1.0,
            min_strategy_weight=0.0, max_strategy_weight=1.0,
        )
        alloc = CrossAssetAllocator(cfg)
        alloc.add_strategy("cb1", AssetClass.CB, _make_nav(n_days=50, daily_ret=0.001))
        alloc.add_strategy("eq1", AssetClass.A_STOCK, _make_nav(n_days=50, daily_ret=0.002))
        alloc.add_strategy("etf1", AssetClass.ETF, _make_nav(n_days=50, daily_ret=0.0015))
        result = alloc.compute()
        assert len(result.class_weights) == 3
        for w in result.class_weights.values():
            assert abs(w - 1 / 3) < 1e-6
