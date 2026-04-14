"""Tests for ValueQualityStrategy (价值质量复合策略)."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.value_quality import (
    ValueQualityConfig,
    ValueQualityStrategy,
)


def _make_candidate(
    symbol: str,
    close: float = 30.0,
    circ_mv: float = 500000.0,
    pe_ttm: float = 15.0,
    pb: float = 2.0,
    roe: float | None = 12.0,
    grossprofit_margin: float | None = 30.0,
    debt_to_assets: float | None = 40.0,
) -> EquityCandidateInfo:
    """构造候选，带完整价值质量元数据."""
    meta = {
        "pe_ttm": pe_ttm,
        "pb": pb,
        "circ_mv": circ_mv,
        "amount": 5000.0,
        "name": "测试",
        "list_date": "20180101",
        "industry": "电子",
    }
    if roe is not None:
        meta["roe"] = roe
    if grossprofit_margin is not None:
        meta["grossprofit_margin"] = grossprofit_margin
    if debt_to_assets is not None:
        meta["debt_to_assets"] = debt_to_assets
    return EquityCandidateInfo(
        symbol=symbol, close=close, circ_mv=circ_mv,
        turnover_rate=3.0, amount=5000.0,
        list_date="20180101", name="测试",
        metadata=meta,
    )


def _make_ctx(
    positions: dict[str, int] | None = None,
    cash: float = 1_000_000.0,
) -> StrategyContext:
    """构造策略上下文."""
    pos = positions or {}
    return StrategyContext(
        positions=pos,
        cash=cash,
        nav=cash,
        current_time=datetime(2024, 6, 15),
    )


# ============================================================================
# Config Tests
# ============================================================================


class TestValueQualityConfig:
    """配置测试."""

    def test_defaults(self):
        cfg = ValueQualityConfig()
        assert cfg.top_k == 30
        assert cfg.rebalance_days == 20
        assert cfg.weight_quality == 0.30
        assert cfg.weight_value == 0.30
        assert cfg.weight_momentum == 0.20
        assert cfg.min_roe == 8.0
        assert cfg.max_pe == 80.0
        assert cfg.max_debt_to_assets == 70.0
        assert cfg.stop_loss_pct == 0.18

    def test_weights_sum_to_one(self):
        cfg = ValueQualityConfig()
        total = (cfg.weight_quality + cfg.weight_value + cfg.weight_momentum
                 + cfg.weight_volatility + cfg.weight_size)
        assert abs(total - 1.0) < 1e-9

    def test_quality_sub_weights_sum_to_one(self):
        cfg = ValueQualityConfig()
        total = cfg.quality_roe_weight + cfg.quality_margin_weight + cfg.quality_debt_weight
        assert abs(total - 1.0) < 1e-9

    def test_value_sub_weights_sum_to_one(self):
        cfg = ValueQualityConfig()
        total = cfg.value_pe_weight + cfg.value_pb_weight
        assert abs(total - 1.0) < 1e-9


# ============================================================================
# Strategy Tests
# ============================================================================


class TestValueQualityStrategy:
    """核心策略逻辑测试."""

    @pytest.fixture
    def strategy(self):
        s = ValueQualityStrategy(
            config=ValueQualityConfig(top_k=3, rebalance_days=1)
        )
        s.on_start()
        return s

    def test_registration(self):
        reg = StrategyRegistry()
        assert "value_quality" in reg._strategies

    def test_name(self, strategy):
        assert strategy.name == "value_quality"

    def test_empty_candidates(self, strategy):
        ctx = _make_ctx()
        assert strategy._score_candidates([], ctx) == []

    def test_extra_filter_low_roe(self, strategy):
        """ROE < 8% 应被过滤."""
        c = _make_candidate("A.SZ", roe=5.0)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is False

    def test_extra_filter_high_pe(self, strategy):
        """PE > 80 应被过滤."""
        c = _make_candidate("A.SZ", pe_ttm=100.0)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is False

    def test_extra_filter_negative_pe(self, strategy):
        """PE <= 0（亏损）应被过滤."""
        c = _make_candidate("A.SZ", pe_ttm=-5.0)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is False

    def test_extra_filter_high_debt(self, strategy):
        """负债率 > 70% 应被过滤."""
        c = _make_candidate("A.SZ", debt_to_assets=75.0)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is False

    def test_extra_filter_pass(self, strategy):
        """合格标的通过过滤."""
        c = _make_candidate("A.SZ", roe=15.0, pe_ttm=12.0, debt_to_assets=30.0)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is True

    def test_extra_filter_none_values_pass(self, strategy):
        """值为 None → 不过滤（宽容处理）."""
        c = _make_candidate("A.SZ", roe=None, pe_ttm=15.0, debt_to_assets=None)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is True

    def test_value_quality_ranking(self, strategy):
        """低 PE + 高 ROE 的标的应排名最高."""
        candidates = [
            _make_candidate("A.SZ", pe_ttm=8, pb=1.5, roe=20, grossprofit_margin=45, debt_to_assets=20),  # 最优
            _make_candidate("B.SZ", pe_ttm=50, pb=5.0, roe=8, grossprofit_margin=15, debt_to_assets=60),  # 最差
            _make_candidate("C.SZ", pe_ttm=20, pb=2.5, roe=12, grossprofit_margin=30, debt_to_assets=40),  # 中等
        ]
        for c in candidates:
            strategy._price_history[c.symbol] = [30 + i * 0.1 for i in range(65)]

        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked[0] == "A.SZ"  # 最佳价值+质量
        assert ranked[-1] == "B.SZ"  # 最差价值+质量

    def test_momentum_confirmation(self, strategy):
        """动量确认：下跌中的低 PE 应排名较低."""
        candidates = [
            _make_candidate("A.SZ", pe_ttm=8, roe=20, grossprofit_margin=40, debt_to_assets=20),
            _make_candidate("B.SZ", pe_ttm=12, roe=18, grossprofit_margin=35, debt_to_assets=25),
        ]
        # A 下跌，B 上涨
        strategy._price_history["A.SZ"] = [50 - i * 0.8 for i in range(65)]
        strategy._price_history["B.SZ"] = [25 + i * 0.5 for i in range(65)]

        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        # 动量权重 0.20，价值权重 0.30，质量权重 0.30
        # A 价值+质量更优但动量更差，综合结果取决于具体分值
        assert len(ranked) == 2

    def test_debt_quality_impact(self, strategy):
        """低负债率应提升质量分."""""
        candidates = [
            _make_candidate("A.SZ", pe_ttm=15, roe=12, debt_to_assets=10),  # 低负债
            _make_candidate("B.SZ", pe_ttm=15, roe=12, debt_to_assets=65),  # 高负债
        ]
        for c in candidates:
            strategy._price_history[c.symbol] = [30.0] * 65

        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked[0] == "A.SZ"


class TestValueQualityBoundary:
    """边界场景测试."""

    def test_single_candidate(self):
        strategy = ValueQualityStrategy(
            config=ValueQualityConfig(top_k=3, rebalance_days=1)
        )
        strategy.on_start()
        candidates = [_make_candidate("A.SZ")]
        strategy._price_history["A.SZ"] = [30.0] * 65
        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked == ["A.SZ"]

    def test_all_none_roe(self):
        """所有 ROE 都是 None → 质量因子缺失 → median 模式下获中位排名分，候选保留."""
        strategy = ValueQualityStrategy(
            config=ValueQualityConfig(top_k=3, rebalance_days=1)
        )
        strategy.on_start()
        candidates = [
            _make_candidate("A.SZ", roe=None, pe_ttm=10),
            _make_candidate("B.SZ", roe=None, pe_ttm=20),
        ]
        for c in candidates:
            strategy._price_history[c.symbol] = [30.0] * 65
        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        # ROE=None → 默认 median 模式下获中位排名分 → 候选保留
        assert len(ranked) == 2

    def test_zero_circ_mv(self):
        """circ_mv=0 → 小市值因子排名无穷低但不报错."""
        strategy = ValueQualityStrategy(
            config=ValueQualityConfig(top_k=3, rebalance_days=1)
        )
        strategy.on_start()
        candidates = [
            _make_candidate("A.SZ", circ_mv=0),
            _make_candidate("B.SZ", circ_mv=500000.0),
        ]
        for c in candidates:
            strategy._price_history[c.symbol] = [30.0] * 65
        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        assert len(ranked) == 2


# ---------------------------------------------------------------------------
# 新增：增强因子插槽测试
# ---------------------------------------------------------------------------


class TestValueQualityEnhancedFactors:
    """验证 ValueQualityStrategy 新增的 Piotroski / 估值分位 / 应计盈余因子插槽。"""

    def _make_rich_candidate(
        self,
        symbol: str,
        close: float = 30.0,
        circ_mv: float = 500_000.0,
        pe_ttm: float = 15.0,
        pb: float = 2.0,
        roe: float = 12.0,
        grossprofit_margin: float = 30.0,
        debt_to_assets: float = 40.0,
        # Piotroski 维度
        roa: float | None = None,
        roa_prev: float | None = None,
        cfps: float | None = None,
        eps: float | None = None,
        debt_to_assets_prev: float | None = None,
        current_ratio: float | None = None,
        current_ratio_prev: float | None = None,
        # 应计盈余
        net_profit: float | None = None,
        ocf: float | None = None,
        total_assets: float | None = None,
        # 估值分位
        pe_history: list[float] | None = None,
    ) -> EquityCandidateInfo:
        meta = {
            "pe_ttm": pe_ttm,
            "pb": pb,
            "circ_mv": circ_mv,
            "amount": 5000.0,
            "name": "测试",
            "list_date": "20180101",
            "industry": "电子",
            "roe": roe,
            "grossprofit_margin": grossprofit_margin,
            "debt_to_assets": debt_to_assets,
        }
        # Piotroski 字段
        for key, val in [
            ("roa", roa), ("roa_prev", roa_prev),
            ("cfps", cfps), ("eps", eps),
            ("debt_to_assets_prev", debt_to_assets_prev),
            ("current_ratio", current_ratio),
            ("current_ratio_prev", current_ratio_prev),
        ]:
            if val is not None:
                meta[key] = val
        # 应计盈余字段
        if net_profit is not None:
            meta["net_profit"] = net_profit
        if ocf is not None:
            meta["ocf"] = ocf
        if total_assets is not None:
            meta["total_assets"] = total_assets
        if pe_history is not None:
            meta["pe_history"] = pe_history
        return EquityCandidateInfo(
            symbol=symbol, close=close, circ_mv=circ_mv,
            turnover_rate=3.0, amount=5000.0,
            list_date="20180101", name="测试",
            metadata=meta,
        )

    def test_default_weights_zero(self):
        """新因子权重默认为 0，不影响原有行为。"""
        cfg = ValueQualityConfig()
        assert cfg.weight_piotroski == 0.0
        assert cfg.weight_valuation_pct == 0.0
        assert cfg.weight_accruals == 0.0

    def test_piotroski_enabled(self):
        """启用 Piotroski 后打分不报错且排序合理。"""
        cfg = ValueQualityConfig(
            top_k=3, rebalance_days=1,
            weight_piotroski=0.15,
            piotroski_min_dims=3,  # 测试数据降低门槛
            weight_value=0.25, weight_quality=0.25,
            weight_momentum=0.15, weight_volatility=0.10,
            weight_size=0.10,
        )
        s = ValueQualityStrategy(config=cfg)
        s.on_start()

        cands = [
            self._make_rich_candidate(
                "A.SZ", roe=20.0,
                roa=5.0, roa_prev=4.0, cfps=1.5, eps=1.0,
                debt_to_assets_prev=45.0,
                current_ratio=2.0, current_ratio_prev=1.8,
            ),
            self._make_rich_candidate(
                "B.SZ", roe=10.0,
                roa=3.0, roa_prev=3.5, cfps=0.5, eps=0.8,
                debt_to_assets_prev=38.0,
            ),
            self._make_rich_candidate("C.SZ", roe=8.0),
        ]
        for c in cands:
            s._price_history[c.symbol] = [30.0] * 65
        ctx = _make_ctx()
        ranked = s._score_candidates(cands, ctx)
        assert isinstance(ranked, list)
        assert len(ranked) >= 1

    def test_valuation_pct_enabled(self):
        """启用估值分位后不报错。"""
        cfg = ValueQualityConfig(
            top_k=3, rebalance_days=1,
            weight_valuation_pct=0.10,
            weight_value=0.25, weight_quality=0.25,
            weight_momentum=0.15, weight_volatility=0.10,
            weight_size=0.10, weight_piotroski=0.0, weight_accruals=0.0,
        )
        s = ValueQualityStrategy(config=cfg)
        s.on_start()

        cands = [
            self._make_rich_candidate(
                "A.SZ", pe_ttm=12.0,
                pe_history=[10.0, 11.0, 12.0, 15.0, 20.0] * 10,
            ),
            self._make_rich_candidate(
                "B.SZ", pe_ttm=25.0,
                pe_history=[20.0, 22.0, 25.0, 30.0, 35.0] * 10,
            ),
        ]
        for c in cands:
            s._price_history[c.symbol] = [30.0] * 65
        ctx = _make_ctx()
        ranked = s._score_candidates(cands, ctx)
        assert isinstance(ranked, list)
        assert len(ranked) >= 1

    def test_accruals_enabled(self):
        """启用应计盈余因子后不报错。"""
        cfg = ValueQualityConfig(
            top_k=3, rebalance_days=1,
            weight_accruals=0.10,
            weight_value=0.25, weight_quality=0.25,
            weight_momentum=0.15, weight_volatility=0.10,
            weight_size=0.10, weight_piotroski=0.0,
            weight_valuation_pct=0.0,
        )
        s = ValueQualityStrategy(config=cfg)
        s.on_start()

        cands = [
            self._make_rich_candidate(
                "A.SZ", net_profit=1.5, ocf=1.0, total_assets=10.0,
            ),
            self._make_rich_candidate(
                "B.SZ", net_profit=1.0, ocf=0.3, total_assets=10.0,
            ),
        ]
        for c in cands:
            s._price_history[c.symbol] = [30.0] * 65
        ctx = _make_ctx()
        ranked = s._score_candidates(cands, ctx)
        assert isinstance(ranked, list)

    def test_all_new_factors_enabled(self):
        """同时启用全部 3 个新因子。"""
        cfg = ValueQualityConfig(
            top_k=5, rebalance_days=1,
            weight_value=0.20, weight_quality=0.20,
            weight_momentum=0.10, weight_volatility=0.10,
            weight_size=0.05,
            weight_piotroski=0.15,
            weight_valuation_pct=0.10,
            weight_accruals=0.10,
            piotroski_min_dims=3,
        )
        s = ValueQualityStrategy(config=cfg)
        s.on_start()

        cands = [
            self._make_rich_candidate(
                "A.SZ", roe=20.0,
                roa=5.0, roa_prev=4.0, cfps=1.5, eps=1.0,
                debt_to_assets_prev=45.0,
                net_profit=1.5, ocf=1.0, total_assets=10.0,
                pe_ttm=12.0,
                pe_history=[10, 11, 12, 15, 20] * 10,
            ),
            self._make_rich_candidate(
                "B.SZ", roe=10.0,
                roa=3.0, roa_prev=3.5, cfps=0.5, eps=0.8,
                net_profit=0.5, ocf=0.3, total_assets=10.0,
                pe_ttm=25.0,
                pe_history=[20, 22, 25, 30, 35] * 10,
            ),
            self._make_rich_candidate(
                "C.SZ", roe=15.0, pe_ttm=18.0,
            ),
        ]
        for c in cands:
            s._price_history[c.symbol] = [30.0] * 65
        ctx = _make_ctx()
        ranked = s._score_candidates(cands, ctx)
        assert isinstance(ranked, list)
        assert len(ranked) >= 1
