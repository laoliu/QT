"""因子注册表集成测试。

验证：
1. 所有 27 个内置因子可通过 create_factor / create_factor_from_config 创建
2. 分类完整性与一致性
3. 因子实例化后可正常执行 compute()
4. Factor ↔ FactorBasedStrategy 桥接
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from aipha.factors.base import Factor, FactorData, FactorResult
from aipha.factors.registry import (
    create_factor,
    create_factor_from_config,
    factor_categories,
    list_factors,
    list_factors_by_category,
)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

EXPECTED_FACTORS = {
    "pe", "pb", "ps", "ep", "bp", "sp",           # value (6)
    "pe_percentile", "pb_percentile",              # value (2) — 估值分位
    "roe", "roa", "gross_margin", "net_margin",     # quality (4)
    "cfo_quality",                                  # quality (1)
    "piotroski_f", "accruals_quality",             # quality (2) — 高阶质量
    "debt_quality", "earnings_stability",          # quality (2) — 高阶质量
    "revenue_growth", "earnings_growth",            # growth (2)
    "revenue_acceleration", "sue",                  # growth (2)
    "momentum", "residual_momentum",                # momentum (2)
    "market_cap", "small_cap", "turnover_rate",     # size (3)
    "volatility", "low_vol",                        # volatility (2)
    "amihud", "turnover_momentum", "volume_ratio",  # liquidity (3)
    "putback_proximity",                            # cb (1)
    "dividend_yield", "dividend_yield_ttm",        # dividend (2)
    "dividend_growth", "dividend_growth_cagr",     # dividend (2)
    "analyst_consensus", "earnings_revision",      # analyst (2)
}

EXPECTED_CATEGORIES = {
    "value", "quality", "growth", "momentum",
    "size", "volatility", "liquidity", "cb",
    "dividend", "analyst",
}

CATEGORY_COUNTS = {
    "value": 8, "quality": 9, "growth": 4, "momentum": 2,
    "size": 3, "volatility": 2, "liquidity": 3, "cb": 1,
    "dividend": 4, "analyst": 2,
}


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _make_factor_data(
    symbol: str = "000001.SZ",
    close: float = 10.0,
    volume: float = 1e6,
) -> FactorData:
    """构造最小可用的 FactorData。"""
    # historical_close: 一些因子期望 dict[date, float]
    hist_close: dict[date, float] = {}
    base = date(2024, 1, 15)
    for i in range(60, 0, -1):
        d = date(2024, 1, 15) if i == 1 else date(
            2023, 11, 1 + (60 - i) % 28
        )
        # 简单递增日期避免重复
        d = date(2023, 10, 1) + __import__("datetime").timedelta(days=60 - i)
        hist_close[d] = close * (1 - 0.005 * i)

    return FactorData(
        symbol=symbol,
        trade_date=date(2024, 1, 15),
        close=close,
        volume=volume,
        returns=0.01,
        historical_close=hist_close,
        fundamentals={
            "pe_ttm": 15.0,
            "pb": 1.5,
            "ps_ttm": 2.0,
            "roe": 12.0,
            "roa": 6.0,
            "grossprofit_margin": 30.0,
            "netprofit_margin": 15.0,
            "ocfps": 0.8,
            "eps": 0.65,
            "revenue_yoy": 20.0,
            "dt_netprofit_yoy": 25.0,
            "q_revenue_yoy": 18.0,
            "q_dtprofit_yoy": 22.0,
            "bps": 7.0,
        },
        metadata={
            "circ_mv": 5_000_000.0,
            "total_mv": 8_000_000.0,
            "turnover_rate": 3.5,
            "volume_ratio": 1.2,
            "pe_ttm": 15.0,
            "pb": 1.5,
            "ps_ttm": 2.0,
            "roe": 12.0,
            "roa": 6.0,
            "grossprofit_margin": 30.0,
            "netprofit_margin": 15.0,
            "ocfps": 0.8,
            "eps": 0.65,
            "total_revenue_ps": 5.0,
            "revenue_yoy": 20.0,
            "dt_netprofit_yoy": 25.0,
            "q_revenue_yoy": 18.0,
            "prev_q_revenue_yoy": 15.0,
            "q_dtprofit_yoy": 22.0,
            "bps": 7.0,
            "volume": 1e6,
            "close": 10.0,
        },
    )


# ===========================================================================
# 注册表完整性
# ===========================================================================


class TestFactorRegistryCompleteness:
    """测试注册表包含所有预期因子。"""

    def test_all_expected_factors_registered(self):
        registered = set(list_factors())
        for name in EXPECTED_FACTORS:
            assert name in registered, f"因子 '{name}' 未注册"

    def test_factor_count(self):
        assert len(list_factors()) >= len(EXPECTED_FACTORS)

    def test_all_categories_present(self):
        cats = set(factor_categories())
        for cat in EXPECTED_CATEGORIES:
            assert cat in cats, f"分类 '{cat}' 缺失"

    def test_category_counts(self):
        for cat, expected_count in CATEGORY_COUNTS.items():
            factors = list_factors_by_category(cat)
            assert len(factors) == expected_count, (
                f"分类 '{cat}' 期望 {expected_count} 个因子，"
                f"实际 {len(factors)}: {factors}"
            )

    def test_no_empty_categories(self):
        for cat in factor_categories():
            factors = list_factors_by_category(cat)
            assert len(factors) > 0, f"分类 '{cat}' 为空"


# ===========================================================================
# create_factor 基本创建
# ===========================================================================


class TestCreateFactor:
    """测试所有因子可通过名称创建。"""

    @pytest.mark.parametrize("name", sorted(EXPECTED_FACTORS))
    def test_create_by_name(self, name):
        factor = create_factor(name)
        assert isinstance(factor, Factor)
        assert factor.name  # 名称非空

    @pytest.mark.parametrize("name", sorted(EXPECTED_FACTORS))
    def test_create_from_config(self, name):
        config = {"name": name}
        factor = create_factor_from_config(config)
        assert isinstance(factor, Factor)

    def test_create_with_params(self):
        factor = create_factor("pe", use_ttm=True, invert=True)
        assert isinstance(factor, Factor)

    def test_create_from_config_with_params(self):
        config = {"name": "momentum", "params": {"lookback_days": 10}}
        factor = create_factor_from_config(config)
        assert isinstance(factor, Factor)

    def test_create_unknown_raises(self):
        with pytest.raises((KeyError, ValueError)):
            create_factor("nonexistent_factor_xyz")

    def test_create_from_config_no_name_raises(self):
        with pytest.raises(ValueError, match="name"):
            create_factor_from_config({})


# ===========================================================================
# 因子计算集成（轻量验证）
# ===========================================================================


class TestFactorCompute:
    """验证所有因子实例可执行 compute() 而不抛出异常。"""

    # 部分因子需要特定 fundamentals（PIT 因子在无 provider 时走降级路径）
    FACTORS_NEEDING_FUNDAMENTALS = {
        "pe", "pb", "ps", "ep", "bp", "sp",
        "roe", "roa", "gross_margin", "net_margin", "cfo_quality",
        "revenue_growth", "earnings_growth", "revenue_acceleration", "sue",
    }

    @pytest.fixture()
    def data(self):
        return _make_factor_data()

    @pytest.mark.parametrize("name", sorted(EXPECTED_FACTORS))
    def test_compute_returns_result(self, name, data):
        factor = create_factor(name)
        result = factor.compute(data)
        assert isinstance(result, FactorResult)
        assert result.symbol == data.symbol
        assert result.trade_date == data.trade_date
        assert result.factor_name  # 非空

    @pytest.mark.parametrize("name", sorted(EXPECTED_FACTORS))
    def test_compute_batch(self, name, data):
        factor = create_factor(name)
        data2 = _make_factor_data(symbol="600000.SH", close=20.0)
        results = factor.compute_batch([data, data2])
        assert len(results) == 2
        symbols = {r.symbol for r in results}
        assert symbols == {"000001.SZ", "600000.SH"}


# ===========================================================================
# 策略-因子桥接
# ===========================================================================


class TestFactorStrategyBridge:
    """验证因子可被 FactorBasedStrategy 消费。"""

    def test_create_strategy_with_registry_factor(self):
        from aipha.strategy.factor_strategy import (
            FactorBasedStrategy,
            FactorStrategyConfig,
        )
        factor = create_factor("pe", invert=True)
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=5),
            name="TEST_PE",
        )
        assert strategy.name == "TEST_PE"

    def test_create_multi_factor_strategy(self):
        from aipha.strategy.factor_strategy import (
            FactorBasedStrategy,
            FactorStrategyConfig,
        )
        factors = [
            create_factor("pe", invert=True),
            create_factor("momentum"),
            create_factor("low_vol"),
        ]
        strategy = FactorBasedStrategy(
            factors=factors,
            weights={"EP": 0.4, "MOMENTUM_20": 0.3, "LOW_VOL_20": 0.3},
            config=FactorStrategyConfig(top_k=10, weight_method="factor_score"),
            name="MULTI_FACTOR",
        )
        assert len(strategy.factors) == 3

    def test_all_categories_have_usable_factors(self):
        """每个分类至少有一个因子可正常实例化。"""
        for cat in factor_categories():
            factor_names = list_factors_by_category(cat)
            assert len(factor_names) > 0
            # 取第一个因子验证可创建
            factor = create_factor(factor_names[0])
            assert isinstance(factor, Factor)
