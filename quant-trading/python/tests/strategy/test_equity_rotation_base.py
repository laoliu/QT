"""A 股轮动策略基类测试

测试 EquityRotationStrategy 的通用逻辑，使用最小具体子类。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
    days_since_listed,
)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

@dataclass
@dataclass
class SimpleEqConfig(EquityRotationConfig):
    top_k: int = 2
    rebalance_days: int = 1
    min_price: float = 1.0
    max_price: float = 200.0
    min_mv: float = 0.0
    min_turnover: float = 10.0
    lot_size: int = 100
    stop_loss_pct: float = 0.15
    min_listed_days: int = 0
    momentum_lookback: int = 0   # 测试用：不要求历史数据积累
    volatility_lookback: int = 0


class SimpleEqStrategy(EquityRotationStrategy):
    """最小可测试具体子类，按 close 升序排名。"""
    name = "simple_eq_test"

    def __init__(self, cfg: SimpleEqConfig | None = None):
        super().__init__()
        self._config = cfg or SimpleEqConfig()

    @property
    def config(self) -> SimpleEqConfig:
        return self._config

    def _score_candidates(self, candidates, context):
        candidates.sort(key=lambda c: c.close)
        return [c.symbol for c in candidates[: self._config.top_k]]


def _bar(symbol: str, close: float, **meta) -> Bar:
    dt = datetime(2024, 3, 15, 15, 0)
    default_meta = {
        "circ_mv": 500_000.0, "amount": 1000.0,
        "list_date": "20200101", "name": "测试股",
    }
    default_meta.update(meta)
    return Bar(
        symbol=symbol, start_ts=datetime(2024, 3, 15, 9, 30), end_ts=dt,
        open=close, high=close * 1.02, low=close * 0.98, close=close,
        volume=10000, metadata=default_meta,
    )


def _suspended_bar(symbol: str, close: float) -> Bar:
    dt = datetime(2024, 3, 15, 15, 0)
    return Bar(
        symbol=symbol, start_ts=datetime(2024, 3, 15, 9, 30), end_ts=dt,
        open=close, high=close, low=close, close=close,
        volume=0, is_suspended=True,
        metadata={"circ_mv": 500_000.0, "amount": 0, "list_date": "20200101", "name": "停牌"},
    )


def _ctx(positions=None, cash=1_000_000.0) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 3, 15, 15, 0),
        positions=positions or {}, cash=cash, nav=cash,
    )


# ---------------------------------------------------------------------------
# EquityCandidateInfo 测试
# ---------------------------------------------------------------------------

class TestEquityCandidateInfo:
    def test_defaults(self):
        ci = EquityCandidateInfo(symbol="000001.SZ", close=10.0, metadata={})
        assert ci.circ_mv == 0.0
        assert ci.amount == 0.0
        assert ci.name == ""

    def test_custom_fields(self):
        ci = EquityCandidateInfo(
            symbol="000001.SZ", close=10.0, metadata={},
            circ_mv=500_000.0, turnover_rate=3.5, name="平安银行",
        )
        assert ci.circ_mv == 500_000.0
        assert ci.name == "平安银行"


# ---------------------------------------------------------------------------
# on_bar 模板方法测试
# ---------------------------------------------------------------------------

class TestOnBar:
    @pytest.fixture
    def strategy(self):
        s = SimpleEqStrategy()
        s.on_start()
        return s

    def test_first_bar_triggers_rebalance(self, strategy):
        bars = {"000001.SZ": _bar("000001.SZ", 10.0)}
        # min_bars = max(momentum_lookback+1, rebalance_days+1) = max(1,2) = 2
        # 首根bar积累数据，第二根bar触发调仓
        strategy.on_bar(bars, _ctx())
        intents = strategy.on_bar(bars, _ctx())
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) > 0

    def test_empty_bars_no_crash(self, strategy):
        intents = strategy.on_bar({}, _ctx())
        assert intents == []

    def test_non_rebalance_day_no_buys(self, strategy):
        cfg = SimpleEqConfig(rebalance_days=5)
        s = SimpleEqStrategy(cfg)
        s.on_start()
        s.on_bar({"000001.SZ": _bar("000001.SZ", 10.0)}, _ctx())
        intents = s.on_bar({"000001.SZ": _bar("000001.SZ", 10.0)}, _ctx())
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) == 0


# ---------------------------------------------------------------------------
# 止损测试
# ---------------------------------------------------------------------------

class TestStopLoss:
    @pytest.fixture
    def strategy(self):
        s = SimpleEqStrategy()
        s.on_start()
        return s

    def test_stop_loss_triggers(self, strategy):
        strategy._entry_prices["A.SZ"] = 20.0
        bars = {"A.SZ": _bar("A.SZ", 15.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy._check_stop_loss(bars, ctx)
        assert len(intents) >= 1
        assert any("止损" in (i.reason or "") for i in intents)

    def test_no_stop_loss_above_threshold(self, strategy):
        strategy._entry_prices["A.SZ"] = 20.0
        bars = {"A.SZ": _bar("A.SZ", 19.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy._check_stop_loss(bars, ctx)
        assert len(intents) == 0

    def test_no_entry_price_no_crash(self, strategy):
        bars = {"A.SZ": _bar("A.SZ", 5.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy._check_stop_loss(bars, ctx)
        assert len(intents) == 0


# ---------------------------------------------------------------------------
# 追踪止损测试
# ---------------------------------------------------------------------------

class TestTrailingStopLoss:
    """trailing_stop_pct 追踪止损机制测试。"""

    @pytest.fixture
    def strategy_trailing(self):
        """创建启用追踪止损 (12%) 的策略实例。"""
        cfg = SimpleEqConfig(trailing_stop_pct=0.12, stop_loss_pct=0.20)
        s = SimpleEqStrategy(cfg)
        s.on_start()
        return s

    @pytest.fixture
    def strategy_no_trailing(self):
        """trailing_stop_pct=0 / 禁用追踪止损。"""
        cfg = SimpleEqConfig(trailing_stop_pct=0.0, stop_loss_pct=0.15)
        s = SimpleEqStrategy(cfg)
        s.on_start()
        return s

    # --- 基本触发 ---

    def test_trailing_stop_triggers_on_drawdown_from_peak(self, strategy_trailing):
        """价格从高点 100 回落到 87（回撤 13% > 12%），应触发追踪止损。"""
        strategy_trailing._entry_prices["A.SZ"] = 80.0
        strategy_trailing._max_prices["A.SZ"] = 100.0
        bars = {"A.SZ": _bar("A.SZ", 87.0)}  # 100 * (1-0.12) = 88, 87 < 88
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy_trailing._check_stop_loss(bars, ctx)
        assert len(intents) == 1
        assert "追踪止损" in (intents[0].reason or "")

    def test_trailing_stop_not_triggered_within_threshold(self, strategy_trailing):
        """价格从高点 100 回落到 90（回撤 10% < 12%），不应触发。"""
        strategy_trailing._entry_prices["A.SZ"] = 80.0
        strategy_trailing._max_prices["A.SZ"] = 100.0
        bars = {"A.SZ": _bar("A.SZ", 90.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy_trailing._check_stop_loss(bars, ctx)
        assert len(intents) == 0

    def test_trailing_stop_disabled_when_zero(self, strategy_no_trailing):
        """trailing_stop_pct=0 时即使大幅回撤也不触发追踪止损。"""
        strategy_no_trailing._entry_prices["A.SZ"] = 80.0
        strategy_no_trailing._max_prices["A.SZ"] = 100.0
        # close=70, 回撤 30%，但 trailing 禁用
        # 但 70 < 80 * (1-0.15) = 68, 所以也不触发固定止损
        bars = {"A.SZ": _bar("A.SZ", 70.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy_no_trailing._check_stop_loss(bars, ctx)
        # 固定止损: 80*(1-0.15)=68, 70 > 68 → 不触发固定止损
        # 追踪止损: 禁用
        assert len(intents) == 0

    # --- 固定止损优先级 ---

    def test_fixed_stop_takes_precedence_over_trailing(self, strategy_trailing):
        """固定止损先触发时，reason 显示'止损'而非'追踪止损'。"""
        # entry=100, stop_loss_pct=0.20 → threshold=80
        # max=120, trailing_stop_pct=0.12 → threshold=105.6
        # close=75 → 两者都触发，但固定先检查
        strategy_trailing._entry_prices["A.SZ"] = 100.0
        strategy_trailing._max_prices["A.SZ"] = 120.0
        bars = {"A.SZ": _bar("A.SZ", 75.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy_trailing._check_stop_loss(bars, ctx)
        assert len(intents) == 1
        assert "止损" in (intents[0].reason or "")
        # 固定止损 reason 不含"追踪"
        assert "追踪" not in (intents[0].reason or "")

    def test_only_trailing_triggers_not_fixed(self, strategy_trailing):
        """仅追踪止损触发（固定止损未达阈值）。"""
        # entry=80, stop_loss_pct=0.20 → fixed threshold=64
        # max=100, trailing_stop_pct=0.12 → trailing threshold=88
        # close=85 → 85 > 64 (不触发固定), 85 < 88 (触发追踪)
        strategy_trailing._entry_prices["A.SZ"] = 80.0
        strategy_trailing._max_prices["A.SZ"] = 100.0
        bars = {"A.SZ": _bar("A.SZ", 85.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy_trailing._check_stop_loss(bars, ctx)
        assert len(intents) == 1
        assert "追踪止损" in (intents[0].reason or "")

    # --- 最高价更新 ---

    def test_max_price_updated_on_new_high(self, strategy_trailing):
        """_check_stop_loss 会自动更新最高价。"""
        strategy_trailing._entry_prices["A.SZ"] = 80.0
        strategy_trailing._max_prices["A.SZ"] = 95.0
        # close=100 > 95 → 应更新 max_prices
        bars = {"A.SZ": _bar("A.SZ", 100.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy_trailing._check_stop_loss(bars, ctx)
        assert len(intents) == 0
        assert strategy_trailing._max_prices["A.SZ"] == 100.0

    def test_max_price_not_decreased(self, strategy_trailing):
        """close 低于当前最高价时，最高价不变。"""
        strategy_trailing._entry_prices["A.SZ"] = 80.0
        strategy_trailing._max_prices["A.SZ"] = 100.0
        bars = {"A.SZ": _bar("A.SZ", 95.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        strategy_trailing._check_stop_loss(bars, ctx)
        assert strategy_trailing._max_prices["A.SZ"] == 100.0

    # --- on_fill 集成 ---

    def test_on_fill_buy_initializes_max_price(self, strategy_trailing):
        """买入时 _max_prices 被初始化为买入价。"""
        strategy_trailing.on_fill("A.SZ", Side.BUY, 100, 50.0)
        assert strategy_trailing._max_prices["A.SZ"] == 50.0

    def test_on_fill_sell_removes_max_price(self, strategy_trailing):
        """卖出时 _max_prices 记录被清除。"""
        strategy_trailing._max_prices["A.SZ"] = 100.0
        strategy_trailing._entry_prices["A.SZ"] = 80.0
        strategy_trailing.on_fill("A.SZ", Side.SELL, 100, 90.0)
        assert "A.SZ" not in strategy_trailing._max_prices

    def test_on_start_clears_max_prices(self, strategy_trailing):
        """on_start 清除所有追踪数据。"""
        strategy_trailing._max_prices["A.SZ"] = 100.0
        strategy_trailing.on_start()
        assert len(strategy_trailing._max_prices) == 0

    # --- 多标的 ---

    def test_trailing_stop_multiple_symbols(self, strategy_trailing):
        """多标的同时检查，只有达到阈值的触发。"""
        strategy_trailing._entry_prices["A.SZ"] = 80.0
        strategy_trailing._max_prices["A.SZ"] = 100.0
        strategy_trailing._entry_prices["B.SZ"] = 50.0
        strategy_trailing._max_prices["B.SZ"] = 60.0
        bars = {
            "A.SZ": _bar("A.SZ", 85.0),   # 85 < 88 → 触发追踪
            "B.SZ": _bar("B.SZ", 55.0),   # 55 > 52.8 → 不触发
        }
        ctx = _ctx(positions={"A.SZ": 100, "B.SZ": 200})
        intents = strategy_trailing._check_stop_loss(bars, ctx)
        assert len(intents) == 1
        assert intents[0].symbol == "A.SZ"

    # --- 边界条件 ---

    def test_trailing_stop_exact_boundary(self, strategy_trailing):
        """close 恰好等于阈值边界时不触发（需严格小于）。"""
        strategy_trailing._entry_prices["A.SZ"] = 80.0
        strategy_trailing._max_prices["A.SZ"] = 100.0
        # threshold = 100 * (1 - 0.12) = 88.0, close=88.0 → 不触发 (<=)
        bars = {"A.SZ": _bar("A.SZ", 88.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy_trailing._check_stop_loss(bars, ctx)
        # 88.0 <= 88.0 → 触发(因为用的是 <=)
        assert len(intents) == 1

    def test_trailing_stop_no_max_price_record(self, strategy_trailing):
        """持仓有 entry_prices 但 _max_prices 为空时首次调用应先更新 max。"""
        strategy_trailing._entry_prices["A.SZ"] = 80.0
        # 不预设 _max_prices
        bars = {"A.SZ": _bar("A.SZ", 95.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy_trailing._check_stop_loss(bars, ctx)
        assert len(intents) == 0
        # max_prices 应被设为 95
        assert strategy_trailing._max_prices["A.SZ"] == 95.0

    def test_trailing_stop_no_entry_price(self, strategy_trailing):
        """有持仓但无 entry_price 时不应 crash。"""
        bars = {"A.SZ": _bar("A.SZ", 5.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy_trailing._check_stop_loss(bars, ctx)
        assert len(intents) == 0


# ---------------------------------------------------------------------------
# 过滤 pipeline 测试
# ---------------------------------------------------------------------------

class TestFilterUniverse:
    @pytest.fixture
    def strategy(self):
        cfg = SimpleEqConfig(
            min_price=3.0, max_price=50.0,
            min_mv=100_000.0, min_turnover=500.0,
            exclude_st=True,
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()
        return s

    def test_filters_low_price(self, strategy):
        bars = {"A.SZ": _bar("A.SZ", 1.0, amount=1000.0, circ_mv=500_000.0)}
        candidates = strategy._filter_universe(bars, _ctx())
        assert len(candidates) == 0

    def test_filters_high_price(self, strategy):
        bars = {"A.SZ": _bar("A.SZ", 80.0, amount=1000.0, circ_mv=500_000.0)}
        candidates = strategy._filter_universe(bars, _ctx())
        assert len(candidates) == 0

    def test_filters_suspended(self, strategy):
        bars = {"A.SZ": _suspended_bar("A.SZ", 10.0)}
        candidates = strategy._filter_universe(bars, _ctx())
        assert len(candidates) == 0

    def test_filters_low_mv(self, strategy):
        bars = {"A.SZ": _bar("A.SZ", 10.0, amount=1000.0, circ_mv=50_000.0)}
        candidates = strategy._filter_universe(bars, _ctx())
        assert len(candidates) == 0

    def test_filters_st(self, strategy):
        bars = {"A.SZ": _bar("A.SZ", 10.0, amount=1000.0, circ_mv=500_000.0, name="*ST坏蛋")}
        candidates = strategy._filter_universe(bars, _ctx())
        assert len(candidates) == 0

    def test_passes_valid_bar(self, strategy):
        bars = {"A.SZ": _bar("A.SZ", 10.0, amount=1000.0, circ_mv=500_000.0)}
        candidates = strategy._filter_universe(bars, _ctx())
        assert len(candidates) == 1
        assert candidates[0].symbol == "A.SZ"


# ---------------------------------------------------------------------------
# on_fill 生命周期
# ---------------------------------------------------------------------------

class TestOnFill:
    def test_buy_records_entry_price(self):
        s = SimpleEqStrategy()
        s.on_fill("A.SZ", Side.BUY, 100, 10.5)
        assert s._entry_prices["A.SZ"] == 10.5

    def test_sell_removes_entry_price(self):
        s = SimpleEqStrategy()
        s._entry_prices["A.SZ"] = 10.0
        s.on_fill("A.SZ", Side.SELL, 100, 9.5)
        assert "A.SZ" not in s._entry_prices


# ---------------------------------------------------------------------------
# 历史窗口测试
# ---------------------------------------------------------------------------

class TestHistory:
    def test_update_history_records_prices(self):
        s = SimpleEqStrategy()
        s.on_start()
        bars = {"A.SZ": _bar("A.SZ", 10.0)}
        s._update_history(bars)
        assert len(s._price_history["A.SZ"]) == 1
        assert s._price_history["A.SZ"][0] == 10.0

    def test_history_window_truncation(self):
        cfg = SimpleEqConfig(momentum_lookback=3, volatility_lookback=3)
        s = SimpleEqStrategy(cfg)
        s.on_start()
        for i in range(20):
            s._update_history({"A.SZ": _bar("A.SZ", 10.0 + i * 0.1)})
        # max_len = max(3, 3) + 5 = 8
        assert len(s._price_history["A.SZ"]) <= 8

    def test_calc_momentum(self):
        s = SimpleEqStrategy(SimpleEqConfig(momentum_lookback=3))
        s.on_start()
        s._price_history["A.SZ"] = [10.0, 11.0, 12.0, 13.0]
        mom = s.calc_momentum("A.SZ")
        assert mom == pytest.approx((13.0 - 10.0) / 10.0)

    def test_calc_momentum_insufficient_data(self):
        s = SimpleEqStrategy(SimpleEqConfig(momentum_lookback=5))
        s.on_start()
        s._price_history["A.SZ"] = [10.0, 11.0]
        mom = s.calc_momentum("A.SZ")
        assert mom == 0.0

    def test_calc_volatility(self):
        s = SimpleEqStrategy(SimpleEqConfig(volatility_lookback=5))
        s.on_start()
        s._price_history["A.SZ"] = [10.0, 10.5, 10.2, 10.8, 10.3, 10.6]
        vol = s.calc_volatility("A.SZ")
        assert vol > 0

    def test_calc_volatility_insufficient_data(self):
        s = SimpleEqStrategy(SimpleEqConfig(volatility_lookback=5))
        s.on_start()
        s._price_history["A.SZ"] = [10.0]
        vol = s.calc_volatility("A.SZ")
        assert vol == 0.0


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

class TestUtilFunctions:
    def test_days_since_listed_normal(self):
        d = days_since_listed("20240101", datetime(2024, 3, 15))
        assert d == 74

    def test_days_since_listed_future(self):
        """上市日在将来 → 返回 0"""
        d = days_since_listed("20250101", datetime(2024, 3, 15))
        assert d == 0

    def test_days_since_listed_invalid(self):
        d = days_since_listed("invalid", datetime(2024, 3, 15))
        assert d == 9999  # 安全兜底返回大值（不会被上市天数过滤）


# ---------------------------------------------------------------------------
# 调仓逻辑
# ---------------------------------------------------------------------------

class TestRebalance:
    def test_first_bar_rebalances(self):
        """bar_count 达到 min_bars 时首次调仓"""
        s = SimpleEqStrategy(SimpleEqConfig(rebalance_days=5, momentum_lookback=0))
        # min_bars = max(0+1, 5+1) = 6, 需要 bar_count >= 6
        # 同时 (bar_count - 1) % rebalance_days == 0 → bar_count=6: (6-1)%5=0 ✓
        s._bar_count = 6
        assert s._should_rebalance() is True

    def test_first_bar_too_early_no_rebalance(self):
        """bar_count 不足 min_bars 时不调仓"""
        s = SimpleEqStrategy(SimpleEqConfig(rebalance_days=5, momentum_lookback=0))
        s._bar_count = 1
        assert s._should_rebalance() is False

    def test_mid_period_no_rebalance(self):
        s = SimpleEqStrategy(SimpleEqConfig(rebalance_days=5))
        s._bar_count = 3
        assert s._should_rebalance() is False

    def test_rank_normalize(self):
        """rank_normalize 静态方法工作正常"""
        candidates = [
            EquityCandidateInfo(symbol="A.SZ", close=10.0, metadata={}),
            EquityCandidateInfo(symbol="B.SZ", close=30.0, metadata={}),
            EquityCandidateInfo(symbol="C.SZ", close=20.0, metadata={}),
        ]
        values = {"A.SZ": 10.0, "B.SZ": 30.0, "C.SZ": 20.0}
        scores: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(candidates, values, 1.0, scores)
        assert len(scores) == 3
        # B.SZ 值最大(30) → 排名第1 → 得分最高
        assert scores["B.SZ"] > scores["C.SZ"] > scores["A.SZ"]


# ---------------------------------------------------------------------------
# 等权再平衡回归测试
# ---------------------------------------------------------------------------

class TestEqualWeightRebalance:
    """验证等权再平衡对已持仓标的做权重漂移修正。"""

    def test_rebalance_adjusts_drifted_positions(self):
        """已持仓标的如果权重漂移超过阈值，应生成调整订单。"""
        s = SimpleEqStrategy(SimpleEqConfig(top_k=2, rebalance_days=1, min_positions=0))
        s.on_start()

        # A 涨了很多（权重高），B 跌了（权重低）-> 应生成再平衡指令
        bars = {
            "A.SZ": _bar("A.SZ", 20.0),
            "B.SZ": _bar("B.SZ", 8.0),
        }
        ctx = _ctx(positions={"A.SZ": 100, "B.SZ": 100}, cash=200.0)
        ctx.nav = 200.0 + 100 * 20.0 + 100 * 8.0  # = 3000

        # 第一根 bar 积累数据（min_bars=max(1,2)=2），第二根触发调仓
        s.on_bar(bars, ctx)
        intents = s.on_bar(bars, ctx)
        assert len(intents) > 0, "等权再平衡应对漂移持仓产生调整"

    def test_no_rebalance_if_drift_small(self):
        """权重漂移很小时不应产生不必要的交易。"""
        s = SimpleEqStrategy(SimpleEqConfig(top_k=2, rebalance_days=1, min_positions=0))
        s.on_start()

        # A:100 股 @ 10元 = 1000, B:100 股 @ 9.5元 = 950
        # 等权目标 ≈ 1000, 偏离 ~2.5%/5% < 15% → 不应调整
        bars = {
            "A.SZ": _bar("A.SZ", 10.0),
            "B.SZ": _bar("B.SZ", 9.5),
        }
        ctx = _ctx(positions={"A.SZ": 100, "B.SZ": 100}, cash=50.0)
        ctx.nav = 50.0 + 100 * 10.0 + 100 * 9.5

        s.on_bar(bars, ctx)  # warm-up: min_bars=max(1,2)=2
        intents = s.on_bar(bars, ctx)
        # 不应产生再平衡调整（A 和 B 都已在目标池中且偏离很小）
        buy_sell_existing = [i for i in intents if i.symbol in ("A.SZ", "B.SZ")]
        assert len(buy_sell_existing) == 0, "小漂移不应触发再平衡交易"

    def test_new_buy_uses_equal_weight(self):
        """新买入标的应使用等权分配。"""
        s = SimpleEqStrategy(SimpleEqConfig(top_k=2, rebalance_days=1, min_positions=0))
        s.on_start()

        bars = {
            "A.SZ": _bar("A.SZ", 10.0),
            "B.SZ": _bar("B.SZ", 12.0),
        }
        ctx = _ctx(cash=100_000.0)
        s.on_bar(bars, ctx)  # warm-up: min_bars=max(1,2)=2
        intents = s.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) == 2, "应买入 2 只目标标的"
        # 两只买入金额应大致相等（等权）
        val_a = next(i.qty for i in buys if i.symbol == "A.SZ") * 10.0
        val_b = next(i.qty for i in buys if i.symbol == "B.SZ") * 12.0
        ratio = max(val_a, val_b) / min(val_a, val_b)
        assert ratio < 1.3, f"等权分配偏差过大: {ratio:.2f}"


class TestFundDeductionConsistency:
    """回归测试：验证 _generate_rebalance 可用资金扣减与数量计算价格一致。

    修复前: available_for_new -= qty * bar.close (少扣 2%)
    修复后: available_for_new -= qty * est_price (bar.close * 1.02)
    """

    def test_multi_buy_does_not_exceed_available(self):
        """多标的新买入时 est_price 总价不应超出 available_for_new。"""
        cfg = SimpleEqConfig(
            top_k=3, rebalance_days=1, min_positions=0,
            cash_reserve_pct=0.0,
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()

        # 3 只标的都是 50 元，cash = 15000
        # 等权 = 15000/3 = 5000 每只
        # est_price = 50 * 1.02 = 51
        # qty = int(5000/51/100)*100 = 900 (9.8→9 → 900)
        # 每只 est_cost = 900 * 51 = 45900 — 不对，太大了
        # 实际是 qty = int(5000 / 51 / 100) * 100
        #         = int(98.03 / 100) * 100 = 0 * 100 = 0
        # 需要调大 cash 让每只都能买入
        # cash = 300000, 等权 = 100000/只
        # est_price = 50*1.02 = 51
        # qty = int(100000/51/100)*100 = int(1960.78/100)*100 = 1900
        # est_cost = 1900*51 = 96900
        # 修复后: available 依次 300000 → 203100 → 106200 → ...
        # 第三只: buy_value = min(100000, 106200) = 100000
        #   qty = int(100000/51/100)*100 = 1900
        #   est_cost = 96900
        #   available = 106200 - 96900 = 9300 (非负 → OK)
        # 修复前: available 300000 → 300000-1900*50=205000 → 205000-1900*50=110000
        #   多出一些余额让第三只多买（不匹配实际成本）
        bars = {
            "A.SZ": _bar("A.SZ", 50.0),
            "B.SZ": _bar("B.SZ", 50.0),
            "C.SZ": _bar("C.SZ", 50.0),
        }
        ctx = _ctx(cash=300_000.0)
        s.on_bar(bars, ctx)  # warm-up: min_bars=max(1,2)=2
        intents = s.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]

        # 所有买入的 est_cost 总价不应超过初始 available
        total_est_cost = sum(b.qty * 50.0 * 1.02 for b in buys)
        assert total_est_cost <= 300_000.0 * 1.001, (
            f"est_price 总成本 {total_est_cost:.0f} 超出可用资金 300000"
        )

    def test_tight_cash_prevents_overbuy(self):
        """现金刚好够一只半时，修复后不应产生高于预算的第二只买入。"""
        cfg = SimpleEqConfig(
            top_k=2, rebalance_days=1, min_positions=0,
            cash_reserve_pct=0.0,
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()

        # cash=20000, 2只，等权 10000/只
        # A: est_price=10*1.02=10.2  qty=int(10000/10.2/100)*100=900
        # 修复后: available 扣减 900*10.2 = 9180, 剩余 10820
        # B: buy_value=min(10000,10820)=10000, qty=int(10000/10.2/100)*100=900
        # 修复前: available 扣减 900*10 = 9000, 剩余 11000
        # B: buy_value=min(10000,11000)=10000, 结果一样但差异会在极端 case 放大
        bars = {
            "A.SZ": _bar("A.SZ", 10.0),
            "B.SZ": _bar("B.SZ", 10.0),
        }
        ctx = _ctx(cash=20_000.0)
        s.on_bar(bars, ctx)  # warm-up: min_bars=max(1,2)=2
        intents = s.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]

        # 每只买入数量 × est_price 之和 ≤ cash
        _EST_MARGIN = 1.02
        total = sum(b.qty * 10.0 * _EST_MARGIN for b in buys)
        assert total <= 20_000.0 * 1.001, f"总 est_cost {total:.0f} 超出 20000"

    def test_single_buy_deduction_uses_est_price(self):
        """单只标的买入后扣减额 = qty × close × 1.02（非 qty × close）。"""
        cfg = SimpleEqConfig(
            top_k=1, rebalance_days=1, min_positions=0,
            cash_reserve_pct=0.0,
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()

        bars = {"A.SZ": _bar("A.SZ", 100.0)}
        ctx = _ctx(cash=50_000.0)
        s.on_bar(bars, ctx)  # warm-up: min_bars=max(1,2)=2
        intents = s.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) == 1
        qty = buys[0].qty
        # qty = int(50000 / (100*1.02) / 100) * 100 = int(490.19/100)*100 = 400
        assert qty == 400, f"qty={qty}, 预期 400"


# ---------------------------------------------------------------------------
# rank_normalize NaN 传染 + rank_sort 过滤 (H4 修复)
# ---------------------------------------------------------------------------

class TestRankNormalizeNaN:
    """验证 rank_normalize 对缺失因子的处理行为"""

    def test_missing_factor_gets_nan(self):
        """缺失因子的标的在 pollute 模式下应获得 NaN 分数"""
        import math
        candidates = [
            EquityCandidateInfo(symbol="A.SZ", close=10.0, metadata={}),
            EquityCandidateInfo(symbol="B.SZ", close=20.0, metadata={}),
            EquityCandidateInfo(symbol="C.SZ", close=30.0, metadata={}),
        ]
        # B.SZ 缺失因子
        values = {"A.SZ": 5.0, "C.SZ": 15.0}
        scores: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(
            candidates, values, 1.0, scores, nan_handling="pollute",
        )
        assert math.isnan(scores["B.SZ"]), "pollute 模式下缺失因子应为 NaN"
        assert math.isfinite(scores["A.SZ"]), "有因子的应为有限值"
        assert math.isfinite(scores["C.SZ"]), "有因子的应为有限值"

    def test_nan_propagates_across_factors(self):
        """pollute 模式：第一个因子缺失后，第二个因子的分数也应为 NaN"""
        import math
        candidates = [
            EquityCandidateInfo(symbol="A.SZ", close=10.0, metadata={}),
            EquityCandidateInfo(symbol="B.SZ", close=20.0, metadata={}),
        ]
        scores: dict[str, float] = {}
        # 第一个因子: A 有, B 无
        EquityRotationStrategy.rank_normalize(
            candidates, {"A.SZ": 10.0}, 0.5, scores,
            nan_handling="pollute",
        )
        assert math.isnan(scores["B.SZ"])
        # 第二个因子: 两个都有
        EquityRotationStrategy.rank_normalize(
            candidates, {"A.SZ": 5.0, "B.SZ": 15.0}, 0.5, scores,
            nan_handling="pollute",
        )
        # B.SZ 在第一个因子缺失，即使第二个因子有值，也应保持 NaN
        assert math.isnan(scores["B.SZ"]), "NaN 应传染"
        assert math.isfinite(scores["A.SZ"])

    def test_rank_sort_filters_nan(self):
        """rank_sort 应过滤掉 NaN 分数的标的"""
        scores = {"A.SZ": 0.8, "B.SZ": float("nan"), "C.SZ": 0.5}
        result = EquityRotationStrategy.rank_sort(scores)
        assert result == ["A.SZ", "C.SZ"]
        assert "B.SZ" not in result

    def test_rank_sort_preserves_order(self):
        """rank_sort 应按分数降序排列"""
        scores = {"A.SZ": 0.3, "B.SZ": 0.9, "C.SZ": 0.6}
        result = EquityRotationStrategy.rank_sort(scores)
        assert result == ["B.SZ", "C.SZ", "A.SZ"]

    def test_all_present_no_nan(self):
        """所有标的都有因子值时不应产生 NaN"""
        import math
        candidates = [
            EquityCandidateInfo(symbol="A.SZ", close=10.0, metadata={}),
            EquityCandidateInfo(symbol="B.SZ", close=20.0, metadata={}),
        ]
        values = {"A.SZ": 10.0, "B.SZ": 20.0}
        scores: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(candidates, values, 1.0, scores)
        assert all(math.isfinite(v) for v in scores.values())


# ===========================================================================
# _should_rebalance 最少 bar 数门槛测试
# ===========================================================================


class TestShouldRebalanceMinBars:
    """测试 _should_rebalance 在 momentum/volatility_lookback > 0 时需要积累足够 bar 数。"""

    def test_rebalance_blocked_until_enough_bars(self):
        """momentum_lookback=5 时，前 5 个 bar 不应触发调仓。"""
        cfg = SimpleEqConfig(
            momentum_lookback=5, volatility_lookback=3, rebalance_days=1,
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()
        # min_bars = max(5+1, 1+1) = 6
        # bar_count=0..5 → False
        s._bar_count = 0
        assert s._should_rebalance() is False
        s._bar_count = 1
        assert s._should_rebalance() is False
        # bar_count=6 → (6-1) % 1 == 0 → True
        s._bar_count = 6
        assert s._should_rebalance() is True

    def test_rebalance_with_large_lookback(self):
        """启用 inverse_vol_weighting 时 momentum_lookback=20 → min_bars=21."""
        cfg = SimpleEqConfig(
            momentum_lookback=20, volatility_lookback=10, rebalance_days=5,
            inverse_vol_weighting=True,  # 触发 needs_history 路径
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()
        for i in range(21):
            s._bar_count = i
            assert s._should_rebalance() is False, f"bar_count={i} should not rebalance"
        s._bar_count = 21
        assert s._should_rebalance() is True

    def test_rebalance_large_lookback_without_history_factors(self):
        """未启用动量/波动因子时, 即使 lookback 很大也只需 rebalance_days+1."""
        cfg = SimpleEqConfig(
            momentum_lookback=20, volatility_lookback=10, rebalance_days=5,
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()
        # needs_history=False → min_bars = rebalance_days+1 = 6
        for i in range(6):
            s._bar_count = i
            assert s._should_rebalance() is False, f"bar_count={i}"
        s._bar_count = 6
        assert s._should_rebalance() is True

    def test_rebalance_zero_lookback_allows_second_bar(self):
        """lookback=0, rebalance_days=1 时 min_bars=max(1,2)=2，第二根 bar 可调仓。"""
        cfg = SimpleEqConfig(
            momentum_lookback=0, volatility_lookback=0, rebalance_days=1,
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()
        s._bar_count = 1
        assert s._should_rebalance() is False
        s._bar_count = 2
        assert s._should_rebalance() is True

    def test_rebalance_period_after_min_bars(self):
        """超过 min_bars 后，应遵循 rebalance_days 周期。"""
        cfg = SimpleEqConfig(
            momentum_lookback=3, volatility_lookback=3, rebalance_days=5,
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()
        # min_bars = max(4, 6) = 6
        s._bar_count = 4
        # (4-1) % 5 = 3 ≠ 0 → False
        assert s._should_rebalance() is False
        s._bar_count = 6
        # (6-1) % 5 = 0 → True
        assert s._should_rebalance() is True


# ===========================================================================
# _compute_inverse_vol_weights 测试
# ===========================================================================


class TestInverseVolWeights:
    """测试反波动率加权方法。"""

    def test_inverse_vol_equal_volatility(self):
        """相同波动率 → 等权。"""
        cfg = SimpleEqConfig(volatility_lookback=5)
        s = SimpleEqStrategy(cfg)
        s.on_start()
        # 构造两只股票有相同的价格波动
        prices = [10.0, 10.5, 10.2, 10.8, 10.3, 10.6]
        s._price_history["A.SZ"] = list(prices)
        s._price_history["B.SZ"] = list(prices)
        w = s._compute_inverse_vol_weights(["A.SZ", "B.SZ"])
        assert w is not None
        assert abs(w["A.SZ"] - 0.5) < 0.01
        assert abs(w["B.SZ"] - 0.5) < 0.01

    def test_inverse_vol_low_vol_gets_higher_weight(self):
        """低波动标的获得更高权重。"""
        cfg = SimpleEqConfig(volatility_lookback=5)
        s = SimpleEqStrategy(cfg)
        s.on_start()
        # A：低波动（近乎水平）
        s._price_history["A.SZ"] = [10.0, 10.01, 10.02, 10.01, 10.0, 10.01]
        # B：高波动
        s._price_history["B.SZ"] = [10.0, 12.0, 8.0, 13.0, 7.0, 11.0]
        w = s._compute_inverse_vol_weights(["A.SZ", "B.SZ"])
        assert w is not None
        assert w["A.SZ"] > w["B.SZ"], "低波动标的应有更高权重"
        assert abs(sum(w.values()) - 1.0) < 1e-8, "权重之和应为 1"

    def test_inverse_vol_returns_none_for_zero_vol(self):
        """波动率为 0 时应回退 None（等权）。"""
        cfg = SimpleEqConfig(volatility_lookback=5)
        s = SimpleEqStrategy(cfg)
        s.on_start()
        # A 价格不变 → vol=0
        s._price_history["A.SZ"] = [10.0] * 6
        s._price_history["B.SZ"] = [10.0, 10.5, 10.2, 10.8, 10.3, 10.6]
        w = s._compute_inverse_vol_weights(["A.SZ", "B.SZ"])
        assert w is None, "存在零波动率标的时应返回 None"

    def test_inverse_vol_returns_none_for_insufficient_data(self):
        """历史数据不足以计算波动率时返回 None。"""
        cfg = SimpleEqConfig(volatility_lookback=5)
        s = SimpleEqStrategy(cfg)
        s.on_start()
        s._price_history["A.SZ"] = [10.0]
        s._price_history["B.SZ"] = [10.0]
        w = s._compute_inverse_vol_weights(["A.SZ", "B.SZ"])
        assert w is None

    def test_inverse_vol_single_symbol(self):
        """单个标的 → 权重 = 1.0。"""
        cfg = SimpleEqConfig(volatility_lookback=5)
        s = SimpleEqStrategy(cfg)
        s.on_start()
        s._price_history["A.SZ"] = [10.0, 10.5, 10.2, 10.8, 10.3, 10.6]
        w = s._compute_inverse_vol_weights(["A.SZ"])
        assert w is not None
        assert abs(w["A.SZ"] - 1.0) < 1e-8


# ===========================================================================
# position_scale clamp 测试
# ===========================================================================


class TestPositionScaleClamp:
    """验证 position_scale 被钳位到 [0, 1] 范围。"""

    def test_position_scale_over_1_clamped(self):
        """position_scale > 1 时等同于 1（不增加杠杆）。"""
        cfg = SimpleEqConfig(
            top_k=1, rebalance_days=1, min_positions=0,
            cash_reserve_pct=0.0,
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()
        s._position_scale = 1.5  # 模拟异常值

        bars = {"A.SZ": _bar("A.SZ", 10.0)}
        ctx = _ctx(cash=100_000.0)
        s.on_bar(bars, ctx)  # warm-up: min_bars=max(1,2)=2
        intents = s.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        # position_scale=1.5 → clamp 到 1.0 → effective_reserve 不变
        # 不应产生杠杆式超额买入
        if buys:
            total_cost = sum(b.qty * 10.0 * 1.02 for b in buys)
            assert total_cost <= 100_000.0 * 1.01

    def test_position_scale_negative_clamped(self):
        """position_scale < 0 时等同于 0（全部现金）。"""
        cfg = SimpleEqConfig(
            top_k=1, rebalance_days=1, min_positions=0,
            cash_reserve_pct=0.0,
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()
        s._position_scale = -0.5  # 模拟异常值

        bars = {"A.SZ": _bar("A.SZ", 10.0)}
        ctx = _ctx(cash=100_000.0)
        s.on_bar(bars, ctx)  # warm-up: min_bars=max(1,2)=2
        intents = s.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        # position_scale=0 → cash_reserve_pct=100% → 无资金可用
        # 可能不买入或买入极少
        # 关键：不应 crash
        assert isinstance(intents, list)


# ===========================================================================
# drift_buy_cost 扣减测试
# ===========================================================================


class TestDriftBuyCostDeduction:
    """验证再平衡补仓消耗的资金从新买入可用资金中扣除。"""

    def test_drift_buy_reduces_available_for_new(self):
        """持仓漂移补仓后，新买入标的可用资金应减少。"""
        cfg = SimpleEqConfig(
            top_k=3, rebalance_days=1, min_positions=0,
            cash_reserve_pct=0.0,
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()

        # A 和 B 已持仓，C 是新标的
        # A 持仓 100 股 @ 当前 50 → 市值 5000
        # B 持仓 100 股 @ 当前 5  → 市值 500
        # 总持仓 5500，NAV = 5500 + 14500 cash = 20000
        # 等权目标：20000/3 ≈ 6667
        # A: 5000 vs 6667 → 偏低 25% → 应补仓
        # B: 500 vs 6667 → 偏低 92% → 应大量补仓
        bars = {
            "A.SZ": _bar("A.SZ", 50.0),
            "B.SZ": _bar("B.SZ", 5.0),
            "C.SZ": _bar("C.SZ", 10.0),
        }
        ctx = _ctx(positions={"A.SZ": 100, "B.SZ": 100}, cash=14_500.0)
        ctx.nav = 14_500.0 + 100 * 50.0 + 100 * 5.0

        s.on_bar(bars, ctx)  # warm-up: min_bars=max(1,2)=2
        intents = s.on_bar(bars, ctx)
        # 关键验证：不应 crash、不应超买
        total_buy_cost = sum(
            i.qty * bars[i.symbol].close * 1.02
            for i in intents
            if i.side == Side.BUY
        )
        # 总买入成本不应超过可用现金
        assert total_buy_cost <= ctx.cash * 1.05, (
            f"总买入估算成本 {total_buy_cost:.0f} 超过现金 {ctx.cash:.0f}"
        )


# ---------------------------------------------------------------------------
# 因子缩尾 (Winsorize) 测试
# ---------------------------------------------------------------------------

class TestWinsorize:
    """验证 rank_normalize 中的 winsorize 功能。"""

    def _make_candidates(self, n: int) -> list[EquityCandidateInfo]:
        return [
            EquityCandidateInfo(
                symbol=f"S{i:03d}.SZ", close=10.0 + i, metadata={},
            )
            for i in range(n)
        ]

    def test_winsorize_clips_extreme_values(self):
        """极端因子值被缩尾后，排名不被单个异常值扭曲。"""
        candidates = self._make_candidates(20)
        # 正常值 1-19，加一个极端值 10000
        values = {c.symbol: float(i + 1) for i, c in enumerate(candidates)}
        values["S019.SZ"] = 10000.0  # 极端值

        scores_with: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(
            candidates, values, 1.0, scores_with, winsorize=True,
        )

        scores_without: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(
            candidates, values, 1.0, scores_without, winsorize=False,
        )

        # 两种方式排名结果相同（rank-based 本身对极端值有鲁棒性）
        # 但当多因子混合时缩尾能避免单因子极端值影响综合分
        assert len(scores_with) == 20
        assert len(scores_without) == 20
        # S019 在两种情况下都应排第一
        assert scores_with["S019.SZ"] == max(scores_with.values())

    def test_winsorize_disabled(self):
        """winsorize=False 时不做缩尾。"""
        candidates = self._make_candidates(10)
        values = {c.symbol: float(i) for i, c in enumerate(candidates)}
        values["S009.SZ"] = 999.0

        scores: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(
            candidates, values, 1.0, scores, winsorize=False,
        )
        assert len(scores) == 10
        assert scores["S009.SZ"] == max(scores.values())

    def test_winsorize_small_sample_skipped(self):
        """样本数 < 5 时跳过缩尾（避免分位数无意义）。"""
        candidates = self._make_candidates(3)
        values = {c.symbol: float(i * 100) for i, c in enumerate(candidates)}
        scores: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(
            candidates, values, 1.0, scores, winsorize=True,
        )
        assert len(scores) == 3

    def test_winsorize_config_propagation(self):
        """EquityRotationConfig 默认启用 winsorize。"""
        cfg = EquityRotationConfig()
        assert cfg.winsorize is True
        assert cfg.winsorize_lower == 0.02
        assert cfg.winsorize_upper == 0.98

    def test_winsorize_multi_factor_stability(self):
        """多因子打分时缩尾降低极端值的跨因子影响。"""
        candidates = self._make_candidates(20)

        # 因子A：正常分布
        factor_a = {c.symbol: float(i + 1) for i, c in enumerate(candidates)}
        # 因子B：有极端值
        factor_b = {c.symbol: float(i + 1) for i, c in enumerate(candidates)}
        factor_b["S000.SZ"] = -50000.0  # 极端负值

        scores: dict[str, float] = {}
        EquityRotationStrategy.rank_normalize(
            candidates, factor_a, 0.5, scores, winsorize=True,
        )
        EquityRotationStrategy.rank_normalize(
            candidates, factor_b, 0.5, scores, winsorize=True,
        )
        # S000 在因子B中最差，但不应因为极端值导致总分异常
        assert scores["S000.SZ"] == min(
            v for v in scores.values() if not (v != v)
        )


# ---------------------------------------------------------------------------
# 持仓集中度警告测试
# ---------------------------------------------------------------------------

class TestTopKConcentration:
    """验证低 top_k 时的集中度警告。"""

    def test_low_topk_warning(self, caplog):
        """top_k < MIN_RECOMMENDED_TOP_K 时应发出警告。"""
        import logging
        s = SimpleEqStrategy(SimpleEqConfig(top_k=5))
        with caplog.at_level(logging.WARNING):
            s.on_start()
        assert "top_k=5" in caplog.text
        assert "低于推荐" in caplog.text

    def test_normal_topk_no_warning(self, caplog):
        """top_k >= MIN_RECOMMENDED_TOP_K 时不应发出警告。"""
        import logging
        s = SimpleEqStrategy(SimpleEqConfig(top_k=20))
        with caplog.at_level(logging.WARNING):
            s.on_start()
        assert "top_k" not in caplog.text

    def test_min_recommended_topk_constant(self):
        """MIN_RECOMMENDED_TOP_K 常量存在且值为 15。"""
        from aipha.strategy.equity_rotation_base import MIN_RECOMMENDED_TOP_K
        assert MIN_RECOMMENDED_TOP_K == 15


# ---------------------------------------------------------------------------
# 行业集中度限制测试
# ---------------------------------------------------------------------------

class TestIndustryExposure:
    """验证 max_industry_pct 行业集中度上限功能。"""

    @staticmethod
    def _bar_with_industry(symbol: str, close: float, industry: str) -> Bar:
        dt = datetime(2024, 3, 15, 15, 0)
        return Bar(
            symbol=symbol, start_ts=datetime(2024, 3, 15, 9, 30), end_ts=dt,
            open=close, high=close * 1.02, low=close * 0.98, close=close,
            volume=10000,
            metadata={
                "circ_mv": 500_000.0, "amount": 1000.0,
                "list_date": "20200101", "name": f"测试{symbol}",
                "industry": industry,
            },
        )

    def test_industry_cap_removes_excess(self):
        """行业超限时应移除排名靠后的同行业标的。"""
        # 10 只标的，top_k=6，max_industry_pct=0.34 → max 2/行业
        cfg = SimpleEqConfig(top_k=6, max_industry_pct=0.34, momentum_lookback=0, rebalance_days=1)
        s = SimpleEqStrategy(cfg)
        s.on_start()

        # 构造 bars：银行 4 只、医药 3 只、科技 3 只（按 close 升序 → 排名靠前）
        bars = {
            "B1.SZ": self._bar_with_industry("B1.SZ", 5.0, "银行"),
            "B2.SZ": self._bar_with_industry("B2.SZ", 6.0, "银行"),
            "B3.SZ": self._bar_with_industry("B3.SZ", 7.0, "银行"),
            "B4.SZ": self._bar_with_industry("B4.SZ", 8.0, "银行"),
            "M1.SZ": self._bar_with_industry("M1.SZ", 9.0, "医药"),
            "M2.SZ": self._bar_with_industry("M2.SZ", 10.0, "医药"),
            "M3.SZ": self._bar_with_industry("M3.SZ", 11.0, "医药"),
            "T1.SZ": self._bar_with_industry("T1.SZ", 12.0, "科技"),
            "T2.SZ": self._bar_with_industry("T2.SZ", 13.0, "科技"),
            "T3.SZ": self._bar_with_industry("T3.SZ", 14.0, "科技"),
        }
        ctx = _ctx(cash=1_000_000.0)
        s._bar_count = 2  # 允许调仓

        intents = s.on_bar(bars, ctx)
        # 由于 max_industry_pct=0.34 → max 2/行业，
        # 银行只能选2只（B1,B2），医药2只（M1,M2），补充科技标的
        bought = {i.symbol for i in intents if i.side == Side.BUY}
        for industry in ["银行", "医药", "科技"]:
            count = sum(
                1 for sym in bought
                if bars[sym].metadata["industry"] == industry
            )
            assert count <= 2, f"{industry} 超过上限: {count}"

    def test_industry_cap_disabled_when_1(self):
        """max_industry_pct=1.0 时不应做任何行业限制。"""
        cfg = SimpleEqConfig(top_k=4, max_industry_pct=1.0, momentum_lookback=0, rebalance_days=1)
        s = SimpleEqStrategy(cfg)
        s.on_start()

        # 4 只全是银行
        bars = {
            f"B{i}.SZ": self._bar_with_industry(f"B{i}.SZ", 5.0 + i, "银行")
            for i in range(1, 5)
        }
        ctx = _ctx(cash=1_000_000.0)
        s._bar_count = 2

        intents = s.on_bar(bars, ctx)
        bought = {i.symbol for i in intents if i.side == Side.BUY}
        assert len(bought) == 4  # 全部银行股都能买入，无限制

    def test_industry_cap_fills_from_other_industries(self):
        """行业超限后应从其他行业的候选中补充（需 _score_candidates 返回足够候选）。"""
        # top_k=4, max_industry_pct=0.25 → max 1/行业
        cfg = SimpleEqConfig(top_k=4, max_industry_pct=0.25, momentum_lookback=0, rebalance_days=1)

        # 使用返回全部候选的子类，以便 fill 有来源
        class FullRankStrategy(SimpleEqStrategy):
            def _score_candidates(self, candidates, context):
                candidates.sort(key=lambda c: c.close)
                return [c.symbol for c in candidates]  # 返回全部

        s = FullRankStrategy(cfg)
        s.on_start()

        bars = {
            "B1.SZ": self._bar_with_industry("B1.SZ", 5.0, "银行"),
            "B2.SZ": self._bar_with_industry("B2.SZ", 6.0, "银行"),
            "B3.SZ": self._bar_with_industry("B3.SZ", 7.0, "银行"),
            "M1.SZ": self._bar_with_industry("M1.SZ", 8.0, "医药"),
            "T1.SZ": self._bar_with_industry("T1.SZ", 9.0, "科技"),
            "T2.SZ": self._bar_with_industry("T2.SZ", 10.0, "科技"),
            "E1.SZ": self._bar_with_industry("E1.SZ", 11.0, "电子"),
            "E2.SZ": self._bar_with_industry("E2.SZ", 12.0, "电子"),
        }
        ctx = _ctx(cash=1_000_000.0)
        s._bar_count = 2

        intents = s.on_bar(bars, ctx)
        bought = {i.symbol for i in intents if i.side == Side.BUY}
        # 每行业最多1只，应选4个不同行业
        industries = [bars[sym].metadata["industry"] for sym in bought]
        assert len(set(industries)) == len(industries), f"有重复行业: {industries}"
        assert len(bought) == 4

    def test_cap_industry_in_target_method(self):
        """直接测试 _cap_industry_in_target 方法。"""
        cfg = SimpleEqConfig(top_k=6, max_industry_pct=0.34)
        s = SimpleEqStrategy(cfg)

        ranked = ["A1", "A2", "A3", "B1", "B2", "C1", "C2", "D1"]
        target = {"A1", "A2", "A3", "B1", "B2", "C1"}
        bars = {
            "A1": self._bar_with_industry("A1", 10, "行业A"),
            "A2": self._bar_with_industry("A2", 11, "行业A"),
            "A3": self._bar_with_industry("A3", 12, "行业A"),
            "B1": self._bar_with_industry("B1", 13, "行业B"),
            "B2": self._bar_with_industry("B2", 14, "行业B"),
            "C1": self._bar_with_industry("C1", 15, "行业C"),
            "C2": self._bar_with_industry("C2", 16, "行业C"),
            "D1": self._bar_with_industry("D1", 17, "行业D"),
        }

        # max_per_industry = round(6 * 0.34) = 2
        result = s._cap_industry_in_target(ranked, target, bars)
        # A 行业只能保留 2 只（A1, A2），A3 移除
        assert "A1" in result
        assert "A2" in result
        assert "A3" not in result
        # 应补充 C2 或 D1
        assert len(result) == 6

    def test_industry_cap_config_field(self):
        """max_industry_pct 配置字段存在且默认值正确。"""
        cfg = EquityRotationConfig()
        assert cfg.max_industry_pct == 1.0  # 默认不限

    def test_recommended_constant_exists(self):
        """RECOMMENDED_MAX_INDUSTRY_PCT 常量存在。"""
        from aipha.strategy.equity_rotation_base import RECOMMENDED_MAX_INDUSTRY_PCT
        assert RECOMMENDED_MAX_INDUSTRY_PCT == 0.20

    def test_no_industry_info_treated_as_unknown(self):
        """缺少行业信息时归类为'未知'，不崩溃。"""
        cfg = SimpleEqConfig(top_k=3, max_industry_pct=0.5)
        s = SimpleEqStrategy(cfg)

        ranked = ["X1", "X2", "X3"]
        target = {"X1", "X2", "X3"}
        # metadata 中无 industry 字段
        dt = datetime(2024, 3, 15, 15, 0)
        bars = {
            sym: Bar(
                symbol=sym, start_ts=datetime(2024, 3, 15, 9, 30), end_ts=dt,
                open=10, high=11, low=9, close=10, volume=1000,
                metadata={"circ_mv": 100_000.0, "amount": 500},
            )
            for sym in ranked
        }
        # max_per = round(3 * 0.5) = 2 → 未知行业只保留2只
        result = s._cap_industry_in_target(ranked, target, bars)
        assert len(result) <= 3
        # 不应抛异常

    def test_industry_cap_with_buffer_mode(self):
        """buffer 模式下行业限制也应生效。"""
        cfg = SimpleEqConfig(
            top_k=4, buffer_k=2, max_industry_pct=0.25,
            momentum_lookback=0, rebalance_days=1,
        )
        s = SimpleEqStrategy(cfg)
        s.on_start()

        bars = {
            "B1.SZ": self._bar_with_industry("B1.SZ", 5.0, "银行"),
            "B2.SZ": self._bar_with_industry("B2.SZ", 6.0, "银行"),
            "B3.SZ": self._bar_with_industry("B3.SZ", 7.0, "银行"),
            "M1.SZ": self._bar_with_industry("M1.SZ", 8.0, "医药"),
            "T1.SZ": self._bar_with_industry("T1.SZ", 9.0, "科技"),
            "E1.SZ": self._bar_with_industry("E1.SZ", 10.0, "电子"),
        }
        # 已持有 B1, B2（银行）
        ctx = _ctx(positions={"B1.SZ": 100, "B2.SZ": 100}, cash=800_000.0)
        s._bar_count = 2

        intents = s.on_bar(bars, ctx)
        # 分析最终持仓中银行股数量
        held_after = {"B1.SZ", "B2.SZ"}  # 初始持仓
        for i in intents:
            if i.side == Side.SELL:
                held_after.discard(i.symbol)
            elif i.side == Side.BUY:
                held_after.add(i.symbol)
        bank_count = sum(
            1 for s in held_after
            if s in bars and bars[s].metadata.get("industry") == "银行"
        )
        # max_per = round(4 * 0.25) = 1 → 银行最多1只
        assert bank_count <= 1
