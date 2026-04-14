"""Tests for V23SbalStrategy (风格均衡轮动策略)."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.v23_sbal import (
    QUADRANT_NAMES,
    V23SbalConfig,
    V23SbalStrategy,
)


# ---------- helpers ----------

def _make_bar(
    symbol: str,
    close: float = 10.0,
    circ_mv: float = 500_000.0,
    pe_ttm: float = 15.0,
    pb: float = 2.0,
    roe: float = 12.0,
    name: str = "正常股",
    list_date: str = "20170101",
    is_suspended: bool = False,
    revenue_yoy: float = 15.0,
    amount: float = 2000.0,
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
            "revenue_yoy": revenue_yoy,
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


def _make_candidate(
    symbol: str = "000001.SZ",
    close: float = 10.0,
    circ_mv: float = 500_000.0,
    pe_ttm: float = 15.0,
    pb: float = 2.0,
    roe: float = 12.0,
    revenue_yoy: float = 15.0,
) -> EquityCandidateInfo:
    return EquityCandidateInfo(
        symbol=symbol,
        close=close,
        metadata={
            "circ_mv": circ_mv,
            "pe_ttm": pe_ttm,
            "pb": pb,
            "roe": roe,
            "revenue_yoy": revenue_yoy,
        },
        circ_mv=circ_mv,
    )


# ---------- Config tests ----------

class TestV23SbalConfig:
    """配置类测试"""

    def test_defaults(self):
        cfg = V23SbalConfig()
        assert cfg.top_k == 30
        assert cfg.rebalance_days == 20
        assert cfg.max_industry_pct == 0.15
        assert cfg.quadrant_stocks == 8
        assert cfg.min_quadrant_stocks == 3

    def test_factor_weights_sum_to_one(self):
        cfg = V23SbalConfig()
        total = (cfg.weight_ep + cfg.weight_growth + cfg.weight_roe
                 + cfg.weight_low_vol + cfg.weight_momentum)
        assert abs(total - 1.0) < 1e-9

    def test_mv_range_covers_all_cap(self):
        cfg = V23SbalConfig()
        assert cfg.min_mv == 50_000.0   # 5亿
        assert cfg.max_mv == 10_000_000.0  # 1000亿

    def test_mdd_gradient_thresholds(self):
        cfg = V23SbalConfig()
        assert cfg.dd_level1_threshold < cfg.dd_level2_threshold
        assert cfg.dd_restore_threshold < cfg.dd_level1_threshold
        assert cfg.dd_level1_scale > cfg.dd_level2_scale

    def test_custom_params(self):
        cfg = V23SbalConfig(top_k=25, quadrant_stocks=7)
        assert cfg.top_k == 25
        assert cfg.quadrant_stocks == 7


# ---------- Strategy instantiation ----------

class TestV23SbalInstantiation:

    def test_create_default(self):
        s = V23SbalStrategy()
        assert s.name == "v23_sbal"
        assert s.config.top_k == 30

    def test_create_with_config(self):
        cfg = V23SbalConfig(top_k=25)
        s = V23SbalStrategy(config=cfg)
        assert s.config.top_k == 25

    def test_registry_lookup(self):
        registry = StrategyRegistry()
        cls = registry.get("v23_sbal")
        assert cls is V23SbalStrategy


# ---------- Quadrant classification ----------

class TestQuadrantClassification:

    def _build_candidates(self):
        """4 只股票，分别落入 4 个象限"""
        return [
            # 大盘+高价值 → large_value
            _make_candidate("LV", circ_mv=2_000_000, pe_ttm=8.0, pb=1.0),
            # 大盘+低价值 → large_growth
            _make_candidate("LG", circ_mv=3_000_000, pe_ttm=80.0, pb=8.0),
            # 小盘+高价值 → small_value
            _make_candidate("SV", circ_mv=100_000, pe_ttm=6.0, pb=0.8),
            # 小盘+低价值 → small_growth
            _make_candidate("SG", circ_mv=80_000, pe_ttm=100.0, pb=10.0),
        ]

    def test_four_quadrants_populated(self):
        s = V23SbalStrategy()
        cands = self._build_candidates()
        quads = s._classify_quadrants(cands)
        assert len(quads) == 4
        for qname in QUADRANT_NAMES:
            assert qname in quads

    def test_large_value_correct(self):
        s = V23SbalStrategy()
        cands = self._build_candidates()
        quads = s._classify_quadrants(cands)
        lv_symbols = [c.symbol for c in quads["large_value"]]
        assert "LV" in lv_symbols

    def test_small_growth_correct(self):
        s = V23SbalStrategy()
        cands = self._build_candidates()
        quads = s._classify_quadrants(cands)
        sg_symbols = [c.symbol for c in quads["small_growth"]]
        assert "SG" in sg_symbols

    def test_empty_candidates(self):
        s = V23SbalStrategy()
        quads = s._classify_quadrants([])
        for qname in QUADRANT_NAMES:
            assert quads[qname] == []


# ---------- MDD gradient risk control ----------

class TestMddGradientRiskControl:

    def test_no_drawdown_full_scale(self):
        s = V23SbalStrategy()
        s.on_start()
        s._nav_high = 1_000_000
        ctx = _make_ctx(nav=1_000_000)
        s._on_bar_update_state({}, ctx)
        assert ctx.metadata["position_scale"] == 1.0

    def test_level1_drawdown_reduces(self):
        s = V23SbalStrategy()
        s.on_start()
        s._nav_high = 1_000_000
        # 16% 回撤 → level1 (>15%)
        ctx = _make_ctx(nav=840_000)
        s._on_bar_update_state({}, ctx)
        assert ctx.metadata["position_scale"] == 0.70

    def test_level2_drawdown_reduces_more(self):
        s = V23SbalStrategy()
        s.on_start()
        s._nav_high = 1_000_000
        # 22% 回撤 → level2 (>20%)
        ctx = _make_ctx(nav=780_000)
        s._on_bar_update_state({}, ctx)
        assert ctx.metadata["position_scale"] == 0.50

    def test_recovery_restores_full(self):
        s = V23SbalStrategy()
        s.on_start()
        s._nav_high = 1_000_000
        # 先触发 level2
        ctx1 = _make_ctx(nav=780_000)
        s._on_bar_update_state({}, ctx1)
        assert s._timing_scale == 0.50
        # 恢复到 5% 回撤 → 恢复满仓
        ctx2 = _make_ctx(nav=950_000)
        s._on_bar_update_state({}, ctx2)
        assert ctx2.metadata["position_scale"] == 1.0

    def test_hysteresis_zone_holds(self):
        """11% 回撤在迟滞区（8%-15%之间），应保持当前状态"""
        s = V23SbalStrategy()
        s.on_start()
        s._nav_high = 1_000_000
        # 先触发 level1 (16% dd)
        ctx1 = _make_ctx(nav=840_000)
        s._on_bar_update_state({}, ctx1)
        assert s._timing_scale == 0.70
        # 回到 11% dd → 迟滞区，保持 0.70
        ctx2 = _make_ctx(nav=890_000)
        s._on_bar_update_state({}, ctx2)
        assert ctx2.metadata["position_scale"] == 0.70

    def test_disabled_timing(self):
        cfg = V23SbalConfig(drawdown_timing_enabled=False)
        s = V23SbalStrategy(config=cfg)
        s.on_start()
        s._nav_high = 1_000_000
        ctx = _make_ctx(nav=700_000)
        s._on_bar_update_state({}, ctx)
        assert ctx.metadata["position_scale"] == 1.0


# ---------- Extra filter ----------

class TestExtraFilter:

    def test_pass_normal_stock(self):
        s = V23SbalStrategy()
        c = _make_candidate(pb=5.0, roe=8.0)
        assert s._extra_filter(c, _make_ctx()) is True

    def test_reject_high_pb(self):
        s = V23SbalStrategy()
        c = _make_candidate(pb=20.0)
        assert s._extra_filter(c, _make_ctx()) is False

    def test_reject_low_roe(self):
        s = V23SbalStrategy()
        c = _make_candidate(roe=-10.0)
        assert s._extra_filter(c, _make_ctx()) is False

    def test_none_metadata_passes(self):
        """metadata 缺失字段不应阻塞"""
        s = V23SbalStrategy()
        c = EquityCandidateInfo(
            symbol="X", close=10.0, metadata={}, circ_mv=100_000,
        )
        assert s._extra_filter(c, _make_ctx()) is True
