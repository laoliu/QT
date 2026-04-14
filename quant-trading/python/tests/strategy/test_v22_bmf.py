"""Tests for V22BmfStrategy (均衡多因子轮动策略)."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.v22_bmf import V22BmfConfig, V22BmfStrategy


# ---------- helpers ----------

def _make_bar(
    symbol: str,
    close: float = 10.0,
    circ_mv: float = 200_000.0,
    pe_ttm: float = 15.0,
    pb: float = 2.0,
    roe: float = 12.0,
    name: str = "正常股",
    list_date: str = "20170101",
    is_suspended: bool = False,
    dt_netprofit_yoy: float = 20.0,
    revenue_yoy: float = 15.0,
    dv_ratio: float = 2.0,
    amount: float = 1000.0,
    turnover_rate: float = 3.0,
    industry: str = "电子",
) -> Bar:
    dt = datetime(2024, 6, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 6, 15, 9, 30),
        end_ts=dt,
        open=close,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=100_000,
        is_suspended=is_suspended,
        metadata={
            "circ_mv": circ_mv,
            "pe_ttm": pe_ttm,
            "pb": pb,
            "roe": roe,
            "name": name,
            "list_date": list_date,
            "dt_netprofit_yoy": dt_netprofit_yoy,
            "revenue_yoy": revenue_yoy,
            "dv_ratio": dv_ratio,
            "amount": amount,
            "turnover_rate": turnover_rate,
            "industry": industry,
        },
    )


def _make_ctx(
    positions: dict[str, int] | None = None,
    cash: float = 1_000_000.0,
    nav: float | None = None,
) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 6, 15, 15, 0),
        positions=positions or {},
        cash=cash,
        nav=nav or cash,
    )


# ---------- Config tests ----------

class TestV22BmfConfig:
    """配置类测试 (≥3 cases)"""

    def test_defaults(self):
        cfg = V22BmfConfig()
        assert cfg.top_k == 20
        assert cfg.rebalance_days == 20
        assert cfg.min_mv == 50_000.0
        assert cfg.max_mv == 1_500_000.0
        assert cfg.max_industry_pct == 0.20
        assert cfg.max_pb == 8.0
        pass

    def test_weight_sum_one(self):
        """因子权重总和应为 1.0"""
        cfg = V22BmfConfig()
        total = (
            cfg.weight_size + cfg.weight_momentum
            + cfg.weight_ep + cfg.weight_profit_growth
            + cfg.weight_roe + cfg.weight_low_vol
        )
        assert abs(total - 1.0) < 1e-9

    def test_custom_override(self):
        cfg = V22BmfConfig(top_k=20, max_mv=1_000_000.0)
        assert cfg.top_k == 20
        assert cfg.max_mv == 1_000_000.0

    def test_drawdown_timing_defaults(self):
        cfg = V22BmfConfig()
        assert cfg.drawdown_timing_enabled is False
        assert cfg.dd_reduce_threshold == 0.20
        assert cfg.dd_restore_threshold == 0.10
        assert cfg.dd_min_scale == 0.50


# ---------- Strategy tests ----------

class TestV22BmfStrategy:
    """策略核心逻辑测试 (≥12 cases)"""

    @pytest.fixture
    def strategy(self):
        cfg = V22BmfConfig(
            top_k=3,
            rebalance_days=1,
            min_mv=10_000.0,
            max_mv=10_000_000.0,
            min_price=1.0,
            max_price=500.0,
            min_turnover=10.0,
            buffer_k=0,
            max_industry_pct=1.0,
            drawdown_timing_enabled=False,
        )
        s = V22BmfStrategy(config=cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "v22_bmf" in StrategyRegistry().list_strategies()

    def test_extra_filter_high_pb(self, strategy):
        """PB > max_pb 应被过滤"""
        c = EquityCandidateInfo(
            symbol="HP.SZ", close=10.0,
            metadata={"pb": 15.0, "pe_ttm": 10.0, "roe": 12.0},
        )
        assert strategy._extra_filter(c, _make_ctx()) is False

    def test_extra_filter_negative_pe(self, strategy):
        """PE <= 0 应被过滤"""
        c = EquityCandidateInfo(
            symbol="LOSS.SZ", close=10.0,
            metadata={"pb": 2.0, "pe_ttm": -5.0, "roe": -3.0},
        )
        assert strategy._extra_filter(c, _make_ctx()) is False

    def test_extra_filter_normal(self, strategy):
        """正常标的应通过"""
        c = EquityCandidateInfo(
            symbol="OK.SZ", close=10.0,
            metadata={"pb": 2.0, "pe_ttm": 15.0, "roe": 12.0},
        )
        assert strategy._extra_filter(c, _make_ctx()) is True

    def test_score_candidates_empty(self, strategy):
        """空列表返回空"""
        assert strategy._score_candidates([], _make_ctx()) == []

    def test_score_candidates_returns_ranked(self, strategy):
        """_score_candidates 返回排序后的 symbol 列表"""
        bars = {
            f"S{i}.SZ": _make_bar(f"S{i}.SZ", circ_mv=100_000 + i * 50_000)
            for i in range(5)
        }
        for _ in range(250):
            strategy._update_history(bars)
        ctx = _make_ctx()
        cands = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(cands, ctx)
        assert isinstance(ranked, list)
        assert len(ranked) > 0
        assert all(isinstance(s, str) for s in ranked)

    def test_score_candidates_prefers_quality(self, strategy):
        """高 ROE+低 PE 标的应排名靠前"""
        bars = {
            "GOOD.SZ": _make_bar("GOOD.SZ", pe_ttm=8.0, roe=25.0,
                                  dv_ratio=4.0, dt_netprofit_yoy=30.0),
            "BAD.SZ": _make_bar("BAD.SZ", pe_ttm=50.0, roe=3.0,
                                 dv_ratio=0.5, dt_netprofit_yoy=2.0),
        }
        for _ in range(250):
            strategy._update_history(bars)
        ctx = _make_ctx()
        cands = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(cands, ctx)
        assert ranked[0] == "GOOD.SZ"


# ---------- Drawdown timing tests ----------

class TestDrawdownTiming:
    """回撤择时模块测试 (≥5 cases)"""

    @pytest.fixture
    def timing_strategy(self):
        cfg = V22BmfConfig(
            top_k=3, rebalance_days=1, min_mv=10_000.0,
            max_mv=10_000_000.0, min_turnover=10.0,
            drawdown_timing_enabled=True,
            dd_reduce_threshold=0.20,
            dd_restore_threshold=0.10,
            dd_min_scale=0.50,
        )
        s = V22BmfStrategy(config=cfg)
        s.on_start()
        return s

    def test_initial_scale_is_one(self, timing_strategy):
        """初始择时 scale 应为 1.0"""
        assert timing_strategy._timing_scale == 1.0

    def test_no_drawdown_keeps_full(self, timing_strategy):
        """无回撤时保持满仓"""
        ctx = _make_ctx(nav=1_000_000)
        timing_strategy._on_bar_update_state({}, ctx)
        assert timing_strategy._timing_scale == 1.0
        assert ctx.metadata.get("position_scale") == 1.0

    def test_large_drawdown_reduces(self, timing_strategy):
        """回撤 > 20% 应减仓至 50%"""
        # 先建立高水位
        ctx1 = _make_ctx(nav=1_000_000)
        timing_strategy._on_bar_update_state({}, ctx1)
        assert timing_strategy._nav_high == 1_000_000

        # 回撤 25%
        ctx2 = _make_ctx(nav=750_000)
        timing_strategy._on_bar_update_state({}, ctx2)
        assert timing_strategy._timing_scale == 0.50
        assert ctx2.metadata["position_scale"] == 0.50

    def test_recovery_restores_full(self, timing_strategy):
        """回撤恢复到 < 10% 应恢复满仓"""
        # 建立高水位
        ctx1 = _make_ctx(nav=1_000_000)
        timing_strategy._on_bar_update_state({}, ctx1)
        # 触发减仓
        ctx2 = _make_ctx(nav=750_000)
        timing_strategy._on_bar_update_state({}, ctx2)
        assert timing_strategy._timing_scale == 0.50
        # 恢复到 95% (5% dd < 10%)
        ctx3 = _make_ctx(nav=950_000)
        timing_strategy._on_bar_update_state({}, ctx3)
        assert timing_strategy._timing_scale == 1.0

    def test_hysteresis_zone_keeps_state(self, timing_strategy):
        """迟滞区（10%-20%）保持当前状态"""
        # 建立高水位
        ctx1 = _make_ctx(nav=1_000_000)
        timing_strategy._on_bar_update_state({}, ctx1)
        # 触发减仓
        ctx2 = _make_ctx(nav=750_000)
        timing_strategy._on_bar_update_state({}, ctx2)
        assert timing_strategy._timing_scale == 0.50
        # 恢复到 85% (15% dd, 在迟滞区)
        ctx3 = _make_ctx(nav=850_000)
        timing_strategy._on_bar_update_state({}, ctx3)
        assert timing_strategy._timing_scale == 0.50  # 保持减仓

    def test_timing_disabled(self):
        """关闭择时时 scale 恒为 1.0"""
        cfg = V22BmfConfig(drawdown_timing_enabled=False)
        s = V22BmfStrategy(config=cfg)
        s.on_start()
        ctx = _make_ctx(nav=750_000)
        s._nav_high = 1_000_000  # 模拟高水位
        s._on_bar_update_state({}, ctx)
        assert s._timing_scale == 1.0
