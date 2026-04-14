"""Tests for ComprehensiveAlphaStrategy.

覆盖：
- 配置默认值与自定义
- 策略注册
- 因子打分（八维度、warmup 降级、NaN 容忍）
- 空数据边界
"""
from __future__ import annotations

import math
from datetime import datetime

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.comprehensive_alpha import (
    ComprehensiveAlphaConfig,
    ComprehensiveAlphaStrategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(
    symbol: str,
    close: float = 30.0,
    pe_ttm: float = 15.0,
    pb: float = 2.0,
    roe: float = 12.0,
    circ_mv: float = 500_000.0,
    amount: float = 5000.0,
    name: str = "正常股",
    list_date: str = "20180101",
    turnover_rate: float = 5.0,
    dv_ratio: float = 3.0,
    dt_netprofit_yoy: float = 15.0,
) -> Bar:
    dt = datetime(2024, 6, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 6, 15, 9, 30),
        end_ts=dt,
        open=close * 0.99,
        high=close * 1.01,
        low=close * 0.98,
        close=close,
        volume=1_000_000.0,
        amount=amount * 10000,
        metadata={
            "name": name,
            "circ_mv": circ_mv,
            "total_mv": circ_mv * 1.3,
            "turnover_rate": turnover_rate,
            "pe_ttm": pe_ttm,
            "pb": pb,
            "ps_ttm": 3.0,
            "roe": roe,
            "roa": roe * 0.5,
            "grossprofit_margin": 30.0,
            "netprofit_margin": 15.0,
            "eps": 0.65,
            "cfps": 0.80,
            "dv_ratio": dv_ratio,
            "dt_netprofit_yoy": dt_netprofit_yoy,
            "list_date": list_date,
            "is_suspended": False,
        },
    )


def _make_candidate(
    symbol: str, metadata: dict | None = None, circ_mv: float = 500_000.0,
) -> EquityCandidateInfo:
    base = {
        "pe_ttm": 15.0, "pb": 2.0, "roe": 12.0, "roa": 6.0,
        "grossprofit_margin": 30.0, "eps": 0.65, "cfps": 0.80,
        "dv_ratio": 3.0, "dt_netprofit_yoy": 15.0, "turnover_rate": 5.0,
    }
    if metadata:
        base.update(metadata)
    return EquityCandidateInfo(
        symbol=symbol, close=30.0, metadata=base,
        circ_mv=circ_mv, turnover_rate=base.get("turnover_rate", 5.0),
    )


# ============================================================================
# 配置
# ============================================================================


class TestComprehensiveAlphaConfig:
    """配置测试"""

    def test_defaults(self):
        cfg = ComprehensiveAlphaConfig()
        assert cfg.top_k == 30
        assert cfg.weight_value == 0.15
        assert cfg.weight_piotroski == 0.08
        assert cfg.weight_dividend == 0.10
        total = (
            cfg.weight_value + cfg.weight_valuation_pct + cfg.weight_quality
            + cfg.weight_piotroski + cfg.weight_accruals + cfg.weight_stability
            + cfg.weight_momentum + cfg.weight_volatility + cfg.weight_dividend
            + cfg.weight_growth
        )
        assert total == pytest.approx(1.0)

    def test_custom(self):
        cfg = ComprehensiveAlphaConfig(weight_value=0.3, weight_piotroski=0.0)
        assert cfg.weight_value == 0.3
        assert cfg.weight_piotroski == 0.0


# ============================================================================
# 注册
# ============================================================================


class TestRegistration:
    """策略注册测试"""

    def test_in_registry(self):
        assert "comprehensive_alpha" in StrategyRegistry().list_strategies()

    def test_create_from_registry(self):
        s = StrategyRegistry().create("comprehensive_alpha")
        assert isinstance(s, ComprehensiveAlphaStrategy)
        assert s.name == "comprehensive_alpha"


# ============================================================================
# 因子打分
# ============================================================================


class TestScoring:
    """因子打分排名测试"""

    def test_basic_scoring(self):
        """低 PE + 高 ROE 排前"""
        s = ComprehensiveAlphaStrategy(ComprehensiveAlphaConfig(
            top_k=3,
            weight_momentum=0.0, weight_volatility=0.0,
            # 禁用需要额外数据的因子
            weight_valuation_pct=0.0, weight_accruals=0.0,
            weight_stability=0.0, weight_piotroski=0.0,
            weight_growth=0.0,
            # 只保留 value + quality + dividend
            weight_value=0.4, weight_quality=0.3, weight_dividend=0.3,
        ))
        cands = [
            _make_candidate("CHEAP", {"pe_ttm": 8.0, "roe": 20.0}),
            _make_candidate("MIDPE", {"pe_ttm": 15.0, "roe": 12.0}),
            _make_candidate("EXPENSIV", {"pe_ttm": 50.0, "roe": 5.0}),
        ]
        ctx = StrategyContext(
            current_time=datetime(2024, 6, 15),
            positions={}, cash=1_000_000.0, nav=1_000_000.0,
        )
        ranked = s._score_candidates(cands, ctx)
        # CHEAP 应排在最前面
        assert ranked[0] == "CHEAP"

    def test_empty_candidates(self):
        """空候选集"""
        s = ComprehensiveAlphaStrategy()
        ctx = StrategyContext(
            current_time=datetime(2024, 6, 15),
            positions={}, cash=1_000_000.0, nav=1_000_000.0,
        )
        assert s._score_candidates([], ctx) == []

    def test_nan_tolerant(self):
        """含 NaN 因子值不崩溃"""
        s = ComprehensiveAlphaStrategy(ComprehensiveAlphaConfig(
            top_k=2,
            weight_momentum=0.0, weight_volatility=0.0,
            weight_valuation_pct=0.0, weight_accruals=0.0,
            weight_stability=0.0, weight_piotroski=0.0,
            weight_growth=0.0,
            weight_value=0.5, weight_quality=0.3, weight_dividend=0.2,
        ))
        cands = [
            _make_candidate("A", {"pe_ttm": 10.0, "roe": 15.0}),
            _make_candidate("B", {}),  # 大部分因子将是 NaN
        ]
        ctx = StrategyContext(
            current_time=datetime(2024, 6, 15),
            positions={}, cash=1_000_000.0, nav=1_000_000.0,
        )
        ranked = s._score_candidates(cands, ctx)
        # Should not crash even with NaN-heavy candidate
        assert isinstance(ranked, list)

    def test_dividend_factor_weight(self):
        """高股息标的在默认配置下应得到额外加分"""
        s = ComprehensiveAlphaStrategy(ComprehensiveAlphaConfig(
            top_k=3,
            weight_momentum=0.0, weight_volatility=0.0,
            weight_value=0.0, weight_quality=0.0, weight_piotroski=0.0,
            weight_accruals=0.0, weight_stability=0.0,
            weight_valuation_pct=0.0, weight_growth=0.0,
            weight_dividend=1.0,  # 只保留股息因子
        ))
        cands = [
            _make_candidate("HIGH_DIV", {"dv_ratio": 8.0}),
            _make_candidate("LOW_DIV", {"dv_ratio": 1.0}),
        ]
        ctx = StrategyContext(
            current_time=datetime(2024, 6, 15),
            positions={}, cash=1_000_000.0, nav=1_000_000.0,
        )
        ranked = s._score_candidates(cands, ctx)
        assert ranked[0] == "HIGH_DIV"

    def test_piotroski_factor_contributes(self):
        """Piotroski 因子对排名有贡献"""
        s = ComprehensiveAlphaStrategy(ComprehensiveAlphaConfig(
            top_k=3,
            weight_momentum=0.0, weight_volatility=0.0,
            weight_value=0.0, weight_quality=0.0,
            weight_dividend=0.0, weight_growth=0.0,
            weight_accruals=0.0, weight_stability=0.0,
            weight_valuation_pct=0.0,
            weight_piotroski=1.0,  # 只保留 Piotroski
        ))
        good_meta = {
            "roa": 5.0, "roa_prev": 4.0, "cfps": 1.0, "eps": 0.8,
            "debt_to_assets": 40.0, "debt_to_assets_prev": 45.0,
            "current_ratio": 2.0, "current_ratio_prev": 1.5,
            "total_share": 1000.0, "total_share_prev": 1000.0,
            "grossprofit_margin": 30.0, "grossprofit_margin_prev": 28.0,
            "asset_turnover": 0.8, "asset_turnover_prev": 0.7,
        }
        bad_meta = {
            "roa": -2.0, "roa_prev": 0.0, "cfps": -0.5, "eps": 0.3,
            "debt_to_assets": 60.0, "debt_to_assets_prev": 50.0,
            "current_ratio": 1.0, "current_ratio_prev": 1.5,
            "total_share": 1100.0, "total_share_prev": 1000.0,
            "grossprofit_margin": 25.0, "grossprofit_margin_prev": 28.0,
            "asset_turnover": 0.6, "asset_turnover_prev": 0.7,
        }
        cands = [
            _make_candidate("GOOD", good_meta),
            _make_candidate("BAD", bad_meta),
        ]
        ctx = StrategyContext(
            current_time=datetime(2024, 6, 15),
            positions={}, cash=1_000_000.0, nav=1_000_000.0,
        )
        ranked = s._score_candidates(cands, ctx)
        assert ranked[0] == "GOOD"


# ============================================================================
# 增强后的 BalancedMultiFactorStrategy 新因子槽位
# ============================================================================


class TestBalancedMultiFactorNewSlots:
    """验证 BalancedMultiFactorStrategy 新增的因子槽位"""

    def test_new_weight_fields_exist(self):
        """新增权重字段存在且默认为 0"""
        from aipha.strategy.balanced_multifactor import BalancedMultiFactorConfig
        cfg = BalancedMultiFactorConfig()
        assert cfg.weight_piotroski == 0.0
        assert cfg.weight_earnings_stability == 0.0
        assert cfg.weight_valuation_percentile == 0.0
        assert cfg.weight_accruals_quality == 0.0

    def test_new_weights_in_get_weights(self):
        """新因子出现在权重字典中"""
        from aipha.strategy.balanced_multifactor import (
            BalancedMultiFactorConfig,
            BalancedMultiFactorStrategy,
        )
        cfg = BalancedMultiFactorConfig(weight_piotroski=0.10)
        s = BalancedMultiFactorStrategy(cfg)
        ctx = StrategyContext(
            current_time=datetime(2024, 6, 15),
            positions={}, cash=1_000_000.0, nav=1_000_000.0,
        )
        w = s._get_weights(ctx)
        assert "piotroski" in w
        assert w["piotroski"] > 0

    def test_piotroski_improves_scoring(self):
        """启用 Piotroski 后影响排名"""
        from aipha.strategy.balanced_multifactor import (
            BalancedMultiFactorConfig,
            BalancedMultiFactorStrategy,
        )
        cfg = BalancedMultiFactorConfig(
            weight_piotroski=0.5,
            weight_value=0.5,
            weight_quality=0.0,
            weight_momentum=0.0,
            weight_volatility=0.0,
            top_k=2,
        )
        s = BalancedMultiFactorStrategy(cfg)
        good = _make_candidate("GOOD", {
            "pe_ttm": 10.0, "roa": 5.0, "roa_prev": 4.0,
            "cfps": 1.0, "eps": 0.8,
            "debt_to_assets": 40.0, "debt_to_assets_prev": 45.0,
            "current_ratio": 2.0, "current_ratio_prev": 1.5,
            "total_share": 1000.0, "total_share_prev": 1000.0,
            "grossprofit_margin": 30.0, "grossprofit_margin_prev": 28.0,
            "asset_turnover": 0.8, "asset_turnover_prev": 0.7,
        })
        bad = _make_candidate("BAD", {
            "pe_ttm": 10.0,  # 同等 PE
            "roa": -1.0, "roa_prev": 0.0, "cfps": -0.5, "eps": 0.3,
            "debt_to_assets": 60.0, "debt_to_assets_prev": 50.0,
            "current_ratio": 1.0, "current_ratio_prev": 1.5,
            "total_share": 1100.0, "total_share_prev": 1000.0,
            "grossprofit_margin": 25.0, "grossprofit_margin_prev": 28.0,
            "asset_turnover": 0.6, "asset_turnover_prev": 0.7,
        })
        ctx = StrategyContext(
            current_time=datetime(2024, 6, 15),
            positions={}, cash=1_000_000.0, nav=1_000_000.0,
        )
        ranked = s._score_candidates([good, bad], ctx)
        assert ranked[0] == "GOOD"
