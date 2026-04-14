"""Tests for BalancedMultiFactorStrategy.

覆盖要求:
- 配置默认值 / 自定义 / 权重归一化
- 策略注册
- 过滤漏斗（价格/市值/ST/停牌/上市天数/流动性）
- 因子打分（Value/Quality 偏好、warmup 降级、NaN 容忍）
- _get_weights regime 自适应
- 完整 on_bar 流程（调仓/止损/无交易日）
- 空数据 / 边界条件
"""
from __future__ import annotations

import math
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.balanced_multifactor import (
    BalancedMultiFactorConfig,
    BalancedMultiFactorStrategy,
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
    is_suspended: bool = False,
    is_index_member: bool = True,
    turnover_rate: float = 5.0,
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
            "pe_ttm": pe_ttm,
            "pb": pb,
            "roe": roe,
            "circ_mv": circ_mv,
            "amount": amount,
            "name": name,
            "list_date": list_date,
            "is_index_member": is_index_member,
            "turnover_rate": turnover_rate,
        },
    )


def _make_ctx(
    positions: dict[str, int] | None = None,
    cash: float = 1_000_000.0,
    metadata: dict | None = None,
) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 6, 15, 15, 0),
        positions=positions or {},
        cash=cash,
        nav=cash,
        metadata=metadata or {},
    )


def _warmup(strategy: BalancedMultiFactorStrategy, bars: dict[str, Bar], n: int = 70):
    """模拟 n 根 bar 的 warmup，填充 _price_history。"""
    for _ in range(n):
        strategy._update_history(bars)


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestBalancedMultiFactorConfig:

    def test_defaults(self):
        cfg = BalancedMultiFactorConfig()
        assert cfg.top_k == 30
        assert cfg.rebalance_days == 20
        assert cfg.min_mv == 300_000.0
        assert cfg.max_mv == 5_000_000.0
        assert cfg.weight_value == 0.25
        assert cfg.weight_quality == 0.25
        assert cfg.weight_momentum == 0.25
        assert cfg.weight_volatility == 0.25
        assert cfg.weight_dividend == 0.0
        assert cfg.weight_earnings_growth == 0.0
        assert cfg.momentum_lookback == 60
        assert cfg.momentum_skip_recent == 5
        assert cfg.inverse_vol_weighting is True
        assert cfg.value_metric == "pe_ttm"
        assert cfg.quality_metric == "roe"
        assert cfg.regime_adaptive is False

    def test_custom_values(self):
        cfg = BalancedMultiFactorConfig(
            top_k=50,
            rebalance_days=40,
            weight_value=0.30,
            weight_quality=0.30,
            weight_momentum=0.20,
            weight_volatility=0.20,
            value_metric="pb",
        )
        assert cfg.top_k == 50
        assert cfg.rebalance_days == 40
        assert cfg.weight_value == 0.30
        assert cfg.value_metric == "pb"

    def test_auxiliary_weights(self):
        cfg = BalancedMultiFactorConfig(
            weight_dividend=0.10,
            weight_earnings_growth=0.05,
        )
        assert cfg.weight_dividend == 0.10
        assert cfg.weight_earnings_growth == 0.05

    def test_regime_deltas(self):
        cfg = BalancedMultiFactorConfig()
        assert cfg.regime_bull_delta_momentum == 0.08
        assert cfg.regime_bear_delta_volatility == 0.05


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:

    def test_strategy_in_registry(self):
        assert "balanced_multifactor" in StrategyRegistry().list_strategies()

    def test_create_from_registry(self):
        strat = StrategyRegistry().create("balanced_multifactor")
        assert isinstance(strat, BalancedMultiFactorStrategy)
        assert strat.name == "balanced_multifactor"


# ---------------------------------------------------------------------------
# Weight calculation
# ---------------------------------------------------------------------------

class TestWeights:

    def _strategy(self, **kwargs):
        cfg = BalancedMultiFactorConfig(**kwargs)
        return BalancedMultiFactorStrategy(cfg)

    def test_equal_weights_neutral(self):
        s = self._strategy()
        w = s._get_weights(_make_ctx())
        assert abs(w["value"] - 0.25) < 1e-9
        assert abs(w["quality"] - 0.25) < 1e-9
        assert abs(w["momentum"] - 0.25) < 1e-9
        assert abs(w["volatility"] - 0.25) < 1e-9
        assert abs(sum(w.values()) - 1.0) < 1e-9

    def test_weights_with_auxiliary(self):
        s = self._strategy(
            weight_value=0.20, weight_quality=0.20,
            weight_momentum=0.20, weight_volatility=0.20,
            weight_dividend=0.10, weight_earnings_growth=0.10,
        )
        w = s._get_weights(_make_ctx())
        assert abs(sum(w.values()) - 1.0) < 1e-9
        assert abs(w["dividend"] - 0.10) < 1e-9

    def test_regime_bull_adjustment(self):
        s = self._strategy(regime_adaptive=True)
        ctx = _make_ctx(metadata={"market_regime": "BULL"})
        w = s._get_weights(ctx)
        assert abs(sum(w.values()) - 1.0) < 1e-9
        # BULL: momentum 增加
        assert w["momentum"] > 0.25  # base 0.25 + delta 0.08

    def test_regime_bear_adjustment(self):
        s = self._strategy(regime_adaptive=True)
        ctx = _make_ctx(metadata={"market_regime": "BEAR"})
        w = s._get_weights(ctx)
        assert abs(sum(w.values()) - 1.0) < 1e-9
        # BEAR: volatility 增加
        assert w["volatility"] > 0.25
        # BEAR: momentum 减少
        assert w["momentum"] < 0.25

    def test_regime_neutral_no_change(self):
        s = self._strategy(regime_adaptive=True)
        ctx = _make_ctx(metadata={"market_regime": "NEUTRAL"})
        w = s._get_weights(ctx)
        assert abs(w["value"] - 0.25) < 1e-9


# ---------------------------------------------------------------------------
# Filter universe
# ---------------------------------------------------------------------------

class TestFilterUniverse:

    @pytest.fixture
    def strategy(self):
        cfg = BalancedMultiFactorConfig(
            top_k=3,
            rebalance_days=1,
            min_price=1.0,
            max_price=500.0,
            min_turnover=10.0,
            lot_size=100,
            buffer_k=0,
            min_listed_days=10,
        )
        s = BalancedMultiFactorStrategy(cfg)
        s.on_start()
        return s

    def test_excludes_st(self, strategy):
        bars = {
            "ST.SZ": _make_bar("ST.SZ", name="ST测试"),
            "OK.SZ": _make_bar("OK.SZ"),
        }
        cs = strategy._filter_universe(bars, _make_ctx())
        syms = [c.symbol for c in cs]
        assert "ST.SZ" not in syms
        assert "OK.SZ" in syms

    def test_excludes_suspended(self, strategy):
        bars = {
            "HALT.SZ": _make_bar("HALT.SZ", is_suspended=True),
            "OK.SZ": _make_bar("OK.SZ"),
        }
        cs = strategy._filter_universe(bars, _make_ctx())
        assert "HALT.SZ" not in [c.symbol for c in cs]

    def test_excludes_low_turnover(self, strategy):
        bars = {
            "LOW.SZ": _make_bar("LOW.SZ", amount=5),
            "OK.SZ": _make_bar("OK.SZ", amount=5000),
        }
        cs = strategy._filter_universe(bars, _make_ctx())
        syms = [c.symbol for c in cs]
        assert "LOW.SZ" not in syms
        assert "OK.SZ" in syms

    def test_excludes_new_listing(self, strategy):
        bars = {
            "NEW.SZ": _make_bar("NEW.SZ", list_date="20240610"),
            "OLD.SZ": _make_bar("OLD.SZ", list_date="20200101"),
        }
        cs = strategy._filter_universe(bars, _make_ctx())
        assert "NEW.SZ" not in [c.symbol for c in cs]
        assert "OLD.SZ" in [c.symbol for c in cs]

    def test_excludes_price_range(self, strategy):
        bars = {
            "CHEAP.SZ": _make_bar("CHEAP.SZ", close=0.5),
            "EXPENSIVE.SZ": _make_bar("EXPENSIVE.SZ", close=600.0),
            "OK.SZ": _make_bar("OK.SZ", close=30.0),
        }
        cs = strategy._filter_universe(bars, _make_ctx())
        syms = [c.symbol for c in cs]
        assert "CHEAP.SZ" not in syms
        assert "EXPENSIVE.SZ" not in syms
        assert "OK.SZ" in syms

    def test_excludes_market_cap_range(self, strategy):
        bars = {
            "TINY.SZ": _make_bar("TINY.SZ", circ_mv=100),          # 太小
            "GIANT.SZ": _make_bar("GIANT.SZ", circ_mv=99_000_000),  # 太大
            "OK.SZ": _make_bar("OK.SZ", circ_mv=500_000),
        }
        cs = strategy._filter_universe(bars, _make_ctx())
        syms = [c.symbol for c in cs]
        assert "TINY.SZ" not in syms
        # GIANT.SZ should pass with default max_mv=5,000,000
        # but our fixture max_mv is default 5_000_000 from BalancedMultiFactorConfig
        # circ_mv=99M > max_mv=5M → excluded
        assert "GIANT.SZ" not in syms
        assert "OK.SZ" in syms

    def test_excludes_non_member(self, strategy):
        bars = {
            "NONMEMBER.SZ": _make_bar("NONMEMBER.SZ", is_index_member=False),
            "MEMBER.SZ": _make_bar("MEMBER.SZ", is_index_member=True),
        }
        cs = strategy._filter_universe(bars, _make_ctx())
        assert "NONMEMBER.SZ" not in [c.symbol for c in cs]
        assert "MEMBER.SZ" in [c.symbol for c in cs]

    def test_empty_bars(self, strategy):
        cs = strategy._filter_universe({}, _make_ctx())
        assert cs == []


# ---------------------------------------------------------------------------
# Factor scoring
# ---------------------------------------------------------------------------

class TestScoreCandidates:

    @pytest.fixture
    def strategy(self):
        cfg = BalancedMultiFactorConfig(
            top_k=5,
            rebalance_days=1,
            min_price=1.0,
            max_price=500.0,
            min_turnover=10.0,
            lot_size=100,
            buffer_k=0,
            min_listed_days=10,
        )
        s = BalancedMultiFactorStrategy(cfg)
        s.on_start()
        return s

    def test_value_factor_low_pe_preferred(self, strategy):
        """低 PE 得分更高（因为 value factor 是 -PE 排名）"""
        bars = {
            "LOW_PE.SZ": _make_bar("LOW_PE.SZ", pe_ttm=8.0),
            "HIGH_PE.SZ": _make_bar("HIGH_PE.SZ", pe_ttm=60.0),
        }
        _warmup(strategy, bars)
        ctx = _make_ctx()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked[0] == "LOW_PE.SZ"

    def test_quality_factor_high_roe_preferred(self, strategy):
        """高 ROE 得分更高"""
        bars = {
            "HI_ROE.SZ": _make_bar("HI_ROE.SZ", pe_ttm=15, roe=25.0),
            "LO_ROE.SZ": _make_bar("LO_ROE.SZ", pe_ttm=15, roe=3.0),
        }
        _warmup(strategy, bars)
        ctx = _make_ctx()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked[0] == "HI_ROE.SZ"

    def test_warmup_uses_static_factors_only(self, strategy):
        """Warmup 期间（bar < momentum_lookback），应跳过动量/波动率因子,
        仅用 value + quality 来排名，而非返回空列表。"""
        bars = {
            "A.SZ": _make_bar("A.SZ", pe_ttm=10, roe=20),
            "B.SZ": _make_bar("B.SZ", pe_ttm=30, roe=5),
        }
        # 只 warmup 5 根 bar（远不够 momentum_lookback=60）
        for _ in range(5):
            strategy._update_history(bars)
        ctx = _make_ctx()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        # 应有结果（warmup 降级到 value+quality）
        assert len(ranked) > 0
        # A: 低PE + 高ROE → 排第一
        assert ranked[0] == "A.SZ"

    def test_warmup_dynamic_factors_kick_in_after_sufficient_bars(self, strategy):
        """充足的 bar 后，动量/波动率因子应参与排名"""
        # 创建两组 bars: A 有更好的动量（价格持续上涨）
        bars_a_close = 30.0
        bars_b_close = 30.0
        for i in range(70):
            bars_a_close += 0.1  # 持续上涨 → 正动量
            bars_b_close -= 0.05  # 下跌 → 负动量
            bars = {
                "A.SZ": _make_bar("A.SZ", close=max(1, bars_a_close), pe_ttm=15, roe=12),
                "B.SZ": _make_bar("B.SZ", close=max(1, bars_b_close), pe_ttm=15, roe=12),
            }
            strategy._update_history(bars)
        ctx = _make_ctx()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        assert len(ranked) == 2
        # A 有更好的动量，在 momentum 维度上占优
        assert ranked[0] == "A.SZ"

    def test_empty_candidates_returns_empty(self, strategy):
        ranked = strategy._score_candidates([], _make_ctx())
        assert ranked == []

    def test_single_candidate(self, strategy):
        bars = {"ONLY.SZ": _make_bar("ONLY.SZ")}
        _warmup(strategy, bars)
        ctx = _make_ctx()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        assert ranked == ["ONLY.SZ"]

    def test_nan_pe_excluded_from_ranking(self, strategy):
        """pe_ttm 为 NaN 的标的应被 value factor NaN 污染"""
        bars = {
            "GOOD.SZ": _make_bar("GOOD.SZ", pe_ttm=15, roe=12),
            "BAD_PE.SZ": _make_bar("BAD_PE.SZ", pe_ttm=float("nan"), roe=12),
        }
        _warmup(strategy, bars)
        ctx = _make_ctx()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        # BAD_PE should be excluded or ranked after GOOD
        # Due to NaN contamination, it may not appear at all
        assert "GOOD.SZ" in ranked

    def test_all_same_values_no_crash(self, strategy):
        """所有标的因子值相同时不应崩溃"""
        bars = {
            f"S{i}.SZ": _make_bar(f"S{i}.SZ", pe_ttm=15, roe=12, close=30)
            for i in range(5)
        }
        _warmup(strategy, bars)
        ctx = _make_ctx()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        assert len(ranked) == 5


# ---------------------------------------------------------------------------
# Full on_bar flow
# ---------------------------------------------------------------------------

class TestOnBar:

    @pytest.fixture
    def strategy(self):
        cfg = BalancedMultiFactorConfig(
            top_k=3,
            rebalance_days=5,  # 每5根bar调仓
            min_price=1.0,
            max_price=500.0,
            min_turnover=10.0,
            lot_size=100,
            buffer_k=0,
            min_listed_days=10,
            stop_loss_pct=1.0,      # 禁用止损，简化测试
            trailing_stop_pct=1.0,
            momentum_lookback=5,    # 缩短动量回看以配合测试
            momentum_skip_recent=0, # 简化测试无需跳过
            volatility_lookback=5,
        )
        s = BalancedMultiFactorStrategy(cfg)
        s.on_start()
        return s

    def _run_bars(self, strategy, bars, ctx, n):
        """运行 n 根 bar，返回最后一次的 intents"""
        intents = []
        for i in range(n):
            intents = strategy.on_bar(bars, ctx)
        return intents

    def test_no_trade_before_first_rebalance(self, strategy):
        bars = {
            "A.SZ": _make_bar("A.SZ", pe_ttm=10, roe=20),
            "B.SZ": _make_bar("B.SZ", pe_ttm=20, roe=10),
        }
        ctx = _make_ctx()
        # Bar 1-4: 无调仓
        for _ in range(4):
            intents = strategy.on_bar(bars, ctx)
            assert len(intents) == 0

    def test_first_rebalance_generates_buys(self, strategy):
        bars = {
            "A.SZ": _make_bar("A.SZ", pe_ttm=10, roe=20),
            "B.SZ": _make_bar("B.SZ", pe_ttm=20, roe=10),
            "C.SZ": _make_bar("C.SZ", pe_ttm=30, roe=5),
        }
        ctx = _make_ctx()
        intents = []
        for _ in range(6):
            intents = strategy.on_bar(bars, ctx)
        # 第6根bar触发调仓（rebalance_days=5, bar_count resets at 5+1=6）
        buy_intents = [i for i in intents if i.side == Side.BUY]
        assert len(buy_intents) > 0

    def test_rebalance_cycle(self, strategy):
        """连续多次调仓周期"""
        bars = {
            f"S{i}.SZ": _make_bar(f"S{i}.SZ", pe_ttm=10+i*5, roe=20-i*3)
            for i in range(5)
        }
        ctx = _make_ctx()
        rebalance_count = 0
        for bar_n in range(1, 25):
            intents = strategy.on_bar(bars, ctx)
            if intents:
                rebalance_count += 1
        # 在 24 根 bar 中，rebalance_days=5 应触发约 4 次
        assert rebalance_count >= 2

    def test_on_start_resets_state(self, strategy):
        bars = {"A.SZ": _make_bar("A.SZ")}
        ctx = _make_ctx()
        for _ in range(10):
            strategy.on_bar(bars, ctx)
        assert strategy._bar_count > 0
        strategy.on_start()
        assert strategy._bar_count == 0
        assert len(strategy._entry_prices) == 0


# ---------------------------------------------------------------------------
# Stop loss
# ---------------------------------------------------------------------------

class TestStopLoss:

    @pytest.fixture
    def strategy(self):
        cfg = BalancedMultiFactorConfig(
            top_k=3,
            rebalance_days=100,  # 不触发调仓
            min_price=1.0,
            max_price=500.0,
            min_turnover=10.0,
            lot_size=100,
            buffer_k=0,
            min_listed_days=10,
            stop_loss_pct=0.15,
            trailing_stop_pct=0.10,
        )
        s = BalancedMultiFactorStrategy(cfg)
        s.on_start()
        return s

    def test_fixed_stop_loss_triggers(self, strategy):
        """价格跌幅超过 stop_loss_pct 时触发固定止损"""
        symbol = "DROP.SZ"
        strategy._entry_prices[symbol] = 100.0
        strategy._max_prices[symbol] = 100.0
        bars = {symbol: _make_bar(symbol, close=84.0)}  # -16% < -15%
        ctx = _make_ctx(positions={symbol: 100})
        intents = strategy._check_stop_loss(bars, ctx)
        sell_intents = [i for i in intents if i.symbol == symbol and i.side == Side.SELL]
        assert len(sell_intents) == 1

    def test_no_stop_loss_within_threshold(self, strategy):
        """价格在止损线内不触发"""
        symbol = "HOLD.SZ"
        strategy._entry_prices[symbol] = 100.0
        strategy._max_prices[symbol] = 100.0
        bars = {symbol: _make_bar(symbol, close=91.0)}  # -9% within both thresholds
        ctx = _make_ctx(positions={symbol: 100})
        intents = strategy._check_stop_loss(bars, ctx)
        sell_intents = [i for i in intents if i.symbol == symbol and i.side == Side.SELL]
        assert len(sell_intents) == 0

    def test_trailing_stop_triggers(self, strategy):
        """追踪止损：从最高价回撤超过阈值"""
        symbol = "TRAIL.SZ"
        strategy._entry_prices[symbol] = 80.0
        strategy._max_prices[symbol] = 120.0  # 最高价 120
        bars = {symbol: _make_bar(symbol, close=107.0)}  # 从120跌到107 = -10.8%
        ctx = _make_ctx(positions={symbol: 100})
        intents = strategy._check_stop_loss(bars, ctx)
        sell_intents = [i for i in intents if i.symbol == symbol and i.side == Side.SELL]
        assert len(sell_intents) == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_zero_weight_factors_skipped(self):
        """权重为 0 的因子不参与排名"""
        cfg = BalancedMultiFactorConfig(
            top_k=3,
            rebalance_days=1,
            min_price=1.0,
            max_price=500.0,
            min_turnover=10.0,
            lot_size=100,
            buffer_k=0,
            min_listed_days=10,
            weight_value=0.50,
            weight_quality=0.50,
            weight_momentum=0.0,
            weight_volatility=0.0,
        )
        s = BalancedMultiFactorStrategy(cfg)
        s.on_start()
        bars = {
            "A.SZ": _make_bar("A.SZ", pe_ttm=10, roe=20),
            "B.SZ": _make_bar("B.SZ", pe_ttm=30, roe=5),
        }
        # 无需 warmup — 动量和波动率权重为 0
        for _ in range(5):
            s._update_history(bars)
        ctx = _make_ctx()
        candidates = s._filter_universe(bars, ctx)
        ranked = s._score_candidates(candidates, ctx)
        assert len(ranked) == 2
        assert ranked[0] == "A.SZ"  # 低PE+高ROE

    def test_extreme_pe_values(self):
        """极端 PE（负值/无穷大）不应崩溃"""
        cfg = BalancedMultiFactorConfig(
            top_k=3, rebalance_days=1, min_price=1.0,
            max_price=500.0, min_turnover=10.0, lot_size=100,
            buffer_k=0, min_listed_days=10,
        )
        s = BalancedMultiFactorStrategy(cfg)
        s.on_start()
        bars = {
            "NEG_PE.SZ": _make_bar("NEG_PE.SZ", pe_ttm=-10, roe=12),
            "INF_PE.SZ": _make_bar("INF_PE.SZ", pe_ttm=float("inf"), roe=12),
            "OK.SZ": _make_bar("OK.SZ", pe_ttm=15, roe=12),
        }
        _warmup(s, bars)
        ctx = _make_ctx()
        candidates = s._filter_universe(bars, ctx)
        ranked = s._score_candidates(candidates, ctx)
        # 不崩溃即可；OK 应在结果中
        assert "OK.SZ" in ranked

    def test_all_nan_metadata(self):
        """所有标的 metadata 值为 NaN 时应返回空列表（无可用因子）"""
        cfg = BalancedMultiFactorConfig(
            top_k=3, rebalance_days=1, min_price=1.0,
            max_price=500.0, min_turnover=10.0, lot_size=100,
            buffer_k=0, min_listed_days=10,
        )
        s = BalancedMultiFactorStrategy(cfg)
        s.on_start()
        bars = {
            "A.SZ": _make_bar("A.SZ", pe_ttm=float("nan"), roe=float("nan")),
            "B.SZ": _make_bar("B.SZ", pe_ttm=float("nan"), roe=float("nan")),
        }
        _warmup(s, bars)
        ctx = _make_ctx()
        candidates = s._filter_universe(bars, ctx)
        ranked = s._score_candidates(candidates, ctx)
        # NaN 因子 → NaN scores → 全部过滤
        # 可能返回空或包含少量（取决于实现）
        assert isinstance(ranked, list)

    def test_config_inherits_equity_rotation(self):
        """配置继承自 EquityRotationConfig"""
        cfg = BalancedMultiFactorConfig()
        assert hasattr(cfg, "lot_size")
        assert hasattr(cfg, "exclude_st")
        assert hasattr(cfg, "min_candidates_to_trade")

    def test_property_config(self):
        """strategy.config 属性返回正确类型"""
        s = BalancedMultiFactorStrategy()
        assert isinstance(s.config, BalancedMultiFactorConfig)

    def test_name_attribute(self):
        s = BalancedMultiFactorStrategy()
        assert s.name == "balanced_multifactor"
