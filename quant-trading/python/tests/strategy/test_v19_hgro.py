"""Tests for V19HgroStrategy (微盘成长因子轮动策略)."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.v19_hgro import V19HgroConfig, V19HgroStrategy


# ---------- helpers ----------

def _make_bar(
    symbol: str,
    close: float = 10.0,
    circ_mv: float = 100_000.0,
    pe_ttm: float = 15.0,
    pb: float = 2.0,
    name: str = "正常股",
    list_date: str = "20170101",
    is_suspended: bool = False,
    dt_netprofit_yoy: float = 20.0,
    amount: float = 500.0,
    turnover_rate: float = 3.0,
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
            "name": name,
            "list_date": list_date,
            "dt_netprofit_yoy": dt_netprofit_yoy,
            "amount": amount,
            "turnover_rate": turnover_rate,
        },
    )


def _make_ctx(
    positions: dict[str, int] | None = None,
    cash: float = 1_000_000.0,
) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 6, 15, 15, 0),
        positions=positions or {},
        cash=cash,
        nav=cash,
    )


# ---------- Config tests ----------

class TestV19HgroConfig:

    def test_defaults(self):
        cfg = V19HgroConfig()
        assert cfg.top_k == 15
        assert cfg.rebalance_days == 20
        assert cfg.min_mv == 20_000.0
        assert cfg.max_mv == 300_000.0
        assert cfg.max_pb == 5.0
        assert cfg.weight_small_size == 0.40
        assert cfg.weight_momentum == 0.25
        assert cfg.weight_growth == 0.20
        assert cfg.weight_ep == 0.10
        assert cfg.weight_bp == 0.05
        assert cfg.momentum_lookback == 240
        assert cfg.momentum_skip_recent == 20
        assert cfg.vol_scaling_enabled is False
        assert cfg.vol_target == 0.20

    def test_custom_override(self):
        cfg = V19HgroConfig(top_k=10, max_mv=500_000.0)
        assert cfg.top_k == 10
        assert cfg.max_mv == 500_000.0

    def test_weight_sum_one(self):
        cfg = V19HgroConfig()
        total = (
            cfg.weight_small_size
            + cfg.weight_momentum
            + cfg.weight_growth
            + cfg.weight_ep
            + cfg.weight_bp
        )
        assert abs(total - 1.0) < 1e-9


# ---------- Strategy tests ----------

class TestV19HgroStrategy:

    @pytest.fixture
    def strategy(self):
        cfg = V19HgroConfig(
            top_k=3,
            rebalance_days=1,
            min_mv=10_000.0,
            max_mv=5_000_000.0,
            min_price=1.0,
            max_price=200.0,
            min_turnover=10.0,
            buffer_k=0,
        )
        s = V19HgroStrategy(config=cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "v19_hgro" in StrategyRegistry().list_strategies()

    def test_extra_filter_high_pb(self, strategy):
        """PB > max_pb 的标的应被过滤"""
        c = EquityCandidateInfo(
            symbol="HP.SZ",
            close=10.0,
            metadata={"pb": 8.0, "pe_ttm": 15.0},
        )
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is False

    def test_extra_filter_negative_pe(self, strategy):
        """PE <= 0（亏损）的标的应被过滤"""
        c = EquityCandidateInfo(
            symbol="LOSS.SZ",
            close=10.0,
            metadata={"pb": 2.0, "pe_ttm": -5.0},
        )
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is False

    def test_extra_filter_normal(self, strategy):
        """正常标的应通过"""
        c = EquityCandidateInfo(
            symbol="OK.SZ",
            close=10.0,
            metadata={"pb": 2.0, "pe_ttm": 15.0},
        )
        ctx = _make_ctx()
        assert strategy._extra_filter(c, ctx) is True

    def test_score_candidates_prefers_small_mv(self, strategy):
        """小市值标的在综合排名中应排在前面"""
        bars = {
            "SMALL.SZ": _make_bar("SMALL.SZ", circ_mv=30_000),
            "MED.SZ": _make_bar("MED.SZ", circ_mv=200_000),
            "BIG.SZ": _make_bar("BIG.SZ", circ_mv=1_000_000),
        }
        for i in range(250):
            strategy._update_history(bars)
        ctx = _make_ctx()
        cands = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(cands, ctx)
        # small_size 权重 0.40，小市值应靠前
        assert ranked[0] == "SMALL.SZ"

    def test_score_candidates_empty(self, strategy):
        """空候选列表应返回空"""
        ctx = _make_ctx()
        assert strategy._score_candidates([], ctx) == []

    def test_vol_scale_disabled_default(self, strategy):
        """默认 vol_scaling 关闭，scale=1.0"""
        assert strategy._vol_scale == 1.0

    def test_vol_scale_enabled(self):
        """启用 vol 缩放后 _vol_scale 应被更新且注入 context"""
        cfg = V19HgroConfig(
            vol_scaling_enabled=True,
            vol_target=0.20,
            vol_lookback=60,
        )
        s = V19HgroStrategy(config=cfg)
        s.on_start()
        # 模拟 NAV history（低波动 → scale > 1）
        ctx = _make_ctx()
        ctx.nav_history = [1_000_000 + i * 100 for i in range(100)]
        bars: dict = {}
        s._on_bar_update_state(bars, ctx)
        # 低波动时应放大
        assert s._vol_scale > 1.0
        # vol_scale 与 mdd_scale 取 min 后注入 context.metadata["position_scale"]
        expected_scale = min(s._vol_scale, s._mdd_scale)
        assert ctx.metadata.get("position_scale") == expected_scale

    def test_vol_scale_high_vol_reduces(self):
        """高波动时 vol_scale 应缩小"""
        import math
        cfg = V19HgroConfig(
            vol_scaling_enabled=True,
            vol_target=0.10,
            vol_lookback=60,
        )
        s = V19HgroStrategy(config=cfg)
        s.on_start()
        # 模拟高波动 NAV
        ctx = _make_ctx()
        nav = [1_000_000.0]
        for i in range(1, 100):
            delta = 30_000 * (1 if i % 2 == 0 else -1)
            nav.append(nav[-1] + delta)
        ctx.nav_history = nav
        bars: dict = {}
        s._on_bar_update_state(bars, ctx)
        assert s._vol_scale < 1.0

    def test_filter_excludes_suspended(self, strategy):
        """停牌应被基础过滤排除"""
        bars = {
            "HALT.SZ": _make_bar("HALT.SZ", is_suspended=True),
            "OK.SZ": _make_bar("OK.SZ"),
        }
        ctx = _make_ctx()
        cands = strategy._filter_universe(bars, ctx)
        syms = [c.symbol for c in cands]
        assert "HALT.SZ" not in syms

    def test_filter_excludes_st(self, strategy):
        """ST 标的应被排除"""
        bars = {
            "ST.SZ": _make_bar("ST.SZ", name="*ST退市"),
            "OK.SZ": _make_bar("OK.SZ"),
        }
        ctx = _make_ctx()
        cands = strategy._filter_universe(bars, ctx)
        syms = [c.symbol for c in cands]
        assert "ST.SZ" not in syms

    def test_config_from_kwargs(self):
        """支持 kwargs 方式构造"""
        s = V19HgroStrategy(top_k=10, max_pb=3.0)
        assert s.config.top_k == 10
        assert s.config.max_pb == 3.0
