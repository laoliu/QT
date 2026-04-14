"""Tests for EarningsMomentumStrategy (盈利动量策略)."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.earnings_momentum import (
    EarningsMomentumConfig,
    EarningsMomentumStrategy,
)


def _make_candidate(
    symbol: str,
    close: float = 30.0,
    circ_mv: float = 500000.0,
    pe_ttm: float = 25.0,
    roe: float | None = 12.0,
    dt_netprofit_yoy: float | None = 30.0,
    revenue_yoy: float | None = 20.0,
    sue: float | None = 1.5,
) -> EquityCandidateInfo:
    """构造候选，带盈利增长元数据."""
    meta = {
        "pe_ttm": pe_ttm,
        "circ_mv": circ_mv,
        "amount": 5000.0,
        "name": "测试",
        "list_date": "20180101",
        "industry": "电子",
    }
    if roe is not None:
        meta["roe"] = roe
    if dt_netprofit_yoy is not None:
        meta["dt_netprofit_yoy"] = dt_netprofit_yoy
    if revenue_yoy is not None:
        meta["revenue_yoy"] = revenue_yoy
    if sue is not None:
        meta["sue"] = sue
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


class TestEarningsMomentumConfig:
    """配置测试."""

    def test_defaults(self):
        cfg = EarningsMomentumConfig()
        assert cfg.top_k == 25
        assert cfg.rebalance_days == 15
        assert cfg.weight_earnings_accel == 0.30
        assert cfg.weight_revenue_growth == 0.20
        assert cfg.weight_sue == 0.20
        assert cfg.weight_momentum == 0.15
        assert cfg.weight_quality == 0.10
        assert cfg.weight_volatility == 0.05
        assert cfg.min_earnings_growth == 10.0
        assert cfg.min_revenue_growth == 5.0
        assert cfg.min_roe == 5.0
        assert cfg.max_pe == 150.0
        assert cfg.stop_loss_pct == 0.15

    def test_weights_sum_to_one(self):
        cfg = EarningsMomentumConfig()
        total = (cfg.weight_earnings_accel + cfg.weight_revenue_growth
                 + cfg.weight_sue + cfg.weight_momentum
                 + cfg.weight_quality + cfg.weight_volatility)
        assert abs(total - 1.0) < 1e-9

    def test_custom(self):
        cfg = EarningsMomentumConfig(top_k=15, min_earnings_growth=20.0)
        assert cfg.top_k == 15
        assert cfg.min_earnings_growth == 20.0


# ============================================================================
# Strategy Tests
# ============================================================================


class TestEarningsMomentumStrategy:
    """核心策略逻辑测试."""

    @pytest.fixture
    def strategy(self):
        s = EarningsMomentumStrategy(
            config=EarningsMomentumConfig(top_k=3, rebalance_days=1)
        )
        s.on_start()
        return s

    def test_registration(self):
        reg = StrategyRegistry()
        assert "earnings_momentum" in reg._strategies

    def test_name(self, strategy):
        assert strategy.name == "earnings_momentum"

    def test_empty_candidates(self, strategy):
        ctx = _make_ctx()
        assert strategy._score_candidates([], ctx) == []

    # --- Extra Filter Tests ---

    def test_filter_low_earnings_growth(self, strategy):
        """扣非利润增长 < 10% 应被过滤."""
        c = _make_candidate("A.SZ", dt_netprofit_yoy=5.0)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is False

    def test_filter_low_revenue_growth(self, strategy):
        """营收增长 < 5% 应被过滤."""
        c = _make_candidate("A.SZ", revenue_yoy=3.0)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is False

    def test_filter_low_roe(self, strategy):
        """ROE < 5% 应被过滤."""
        c = _make_candidate("A.SZ", roe=3.0)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is False

    def test_filter_negative_pe(self, strategy):
        """PE <= 0（亏损）应被过滤."""
        c = _make_candidate("A.SZ", pe_ttm=-10.0)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is False

    def test_filter_high_pe(self, strategy):
        """PE > 150 应被过滤."""
        c = _make_candidate("A.SZ", pe_ttm=200.0)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is False

    def test_filter_pass(self, strategy):
        """合格标的通过过滤."""
        c = _make_candidate("A.SZ", dt_netprofit_yoy=30, revenue_yoy=20, roe=12, pe_ttm=25)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is True

    def test_filter_none_values_pass(self, strategy):
        """所有基本面指标为 None → 不过滤（宽容处理）."""
        c = _make_candidate("A.SZ", dt_netprofit_yoy=None, revenue_yoy=None,
                            roe=None, pe_ttm=None, sue=None)
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is True

    # --- Scoring Tests ---

    def test_high_earnings_growth_preferred(self, strategy):
        """高盈利增长 + 高营收增长的标的应排名最高."""
        candidates = [
            _make_candidate("A.SZ", dt_netprofit_yoy=80, revenue_yoy=50, sue=2.5, roe=20),  # 最优
            _make_candidate("B.SZ", dt_netprofit_yoy=15, revenue_yoy=8, sue=0.5, roe=8),   # 最差
            _make_candidate("C.SZ", dt_netprofit_yoy=40, revenue_yoy=25, sue=1.5, roe=12),  # 中等
        ]
        for c in candidates:
            strategy._price_history[c.symbol] = [30 + i * 0.1 for i in range(65)]

        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked[0] == "A.SZ"
        assert ranked[-1] == "B.SZ"

    def test_sue_confirmation(self, strategy):
        """SUE 高的标的在其他条件相同时应排更前."""
        candidates = [
            _make_candidate("A.SZ", dt_netprofit_yoy=30, revenue_yoy=20, sue=3.0, roe=12),
            _make_candidate("B.SZ", dt_netprofit_yoy=30, revenue_yoy=20, sue=0.1, roe=12),
        ]
        for c in candidates:
            strategy._price_history[c.symbol] = [30.0] * 65

        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked[0] == "A.SZ"  # 高 SUE

    def test_none_sue_handled(self, strategy):
        """SUE 为 None → median 填充 → 该候选排名靠后但不排除."""
        candidates = [
            _make_candidate("A.SZ", sue=None, dt_netprofit_yoy=50, revenue_yoy=30),
            _make_candidate("B.SZ", sue=2.0, dt_netprofit_yoy=50, revenue_yoy=30),
        ]
        for c in candidates:
            strategy._price_history[c.symbol] = [30.0] * 65

        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        # 核心验证：不崩溃 + 缺失 SUE 的 A.SZ 仍被保留（median 填充）
        assert len(ranked) == 2
        assert "A.SZ" in ranked
        assert "B.SZ" in ranked

    def test_momentum_influence(self, strategy):
        """价格动量确认：上涨股应优于下跌股."""""
        candidates = [
            _make_candidate("A.SZ", dt_netprofit_yoy=30, revenue_yoy=20, sue=1.0),
            _make_candidate("B.SZ", dt_netprofit_yoy=30, revenue_yoy=20, sue=1.0),
        ]
        strategy._price_history["A.SZ"] = [25 + i * 0.5 for i in range(65)]  # 上涨
        strategy._price_history["B.SZ"] = [40 - i * 0.5 for i in range(65)]  # 下跌

        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked[0] == "A.SZ"


class TestEarningsMomentumBoundary:
    """边界场景测试."""

    def test_single_candidate(self):
        strategy = EarningsMomentumStrategy(
            config=EarningsMomentumConfig(top_k=3, rebalance_days=1)
        )
        strategy.on_start()
        candidates = [_make_candidate("A.SZ")]
        strategy._price_history["A.SZ"] = [30.0] * 65
        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked == ["A.SZ"]

    def test_all_none_financials(self):
        """所有基本面 None → median 填充 → 基于动量/波动率排名."""
        strategy = EarningsMomentumStrategy(
            config=EarningsMomentumConfig(top_k=3, rebalance_days=1)
        )
        strategy.on_start()
        candidates = [
            _make_candidate("A.SZ", dt_netprofit_yoy=None, revenue_yoy=None, sue=None, roe=None),
            _make_candidate("B.SZ", dt_netprofit_yoy=None, revenue_yoy=None, sue=None, roe=None),
        ]
        for c in candidates:
            strategy._price_history[c.symbol] = [30 + i * 0.1 for i in range(65)]
        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        # median 填充 → 两者都有有效分数（动量/波动率因子有价格数据）
        assert len(ranked) == 2

    def test_negative_earnings_growth(self):
        """负增长标的排名靠后."""""
        strategy = EarningsMomentumStrategy(
            config=EarningsMomentumConfig(top_k=5, rebalance_days=1)
        )
        strategy.on_start()
        candidates = [
            _make_candidate("A.SZ", dt_netprofit_yoy=-20, revenue_yoy=5, sue=0.5),
            _make_candidate("B.SZ", dt_netprofit_yoy=50, revenue_yoy=30, sue=2.0),
        ]
        for c in candidates:
            strategy._price_history[c.symbol] = [30.0] * 65
        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked[0] == "B.SZ"
