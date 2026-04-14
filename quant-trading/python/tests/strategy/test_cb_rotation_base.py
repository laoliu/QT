"""可转债轮动策略基类测试

测试 CBRotationStrategy 的通用逻辑，使用最小具体子类。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_to_maturity,
    rating_score,
)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

@dataclass
class SimpleConfig(CBRotationConfig):
    top_k: int = 2
    rebalance_days: int = 1
    max_price: float = 200.0
    min_turnover: float = 10.0
    stop_loss_pct: float = 0.15
    lot_size: int = 10


class SimpleCBStrategy(CBRotationStrategy):
    """最小可测试具体子类，按 close 升序排名。"""
    name = "simple_cb_test"

    def __init__(self, cfg: SimpleConfig | None = None):
        super().__init__()
        self._config = cfg or SimpleConfig()

    @property
    def config(self) -> SimpleConfig:
        return self._config

    def _score_candidates(self, candidates, context):
        candidates.sort(key=lambda c: c.close)
        return [c.symbol for c in candidates[: self._config.top_k]]


def _bar(symbol: str, close: float, **meta) -> Bar:
    dt = datetime(2024, 3, 15, 15, 0)
    default_meta = {
        "amount": 100.0, "issue_size": 5000.0,
        "maturity_date": "20280101", "stk_short_name": "正股",
    }
    default_meta.update(meta)
    return Bar(
        symbol=symbol, start_ts=datetime(2024, 3, 15, 9, 30), end_ts=dt,
        open=close, high=close * 1.02, low=close * 0.98, close=close,
        volume=1000, metadata=default_meta,
    )


def _ctx(positions=None, cash=500_000.0) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 3, 15, 15, 0),
        positions=positions or {}, cash=cash, nav=cash,
    )


# ---------------------------------------------------------------------------
# CandidateInfo 测试
# ---------------------------------------------------------------------------

class TestCandidateInfo:
    def test_defaults(self):
        ci = CandidateInfo(symbol="A.SZ", close=100.0, metadata={})
        assert ci.cb_over_rate is None
        assert ci.cb_value is None
        assert ci.amount == 0.0

    def test_custom_fields(self):
        ci = CandidateInfo(
            symbol="B.SZ", close=110.0, metadata={},
            cb_over_rate=5.0, cb_value=95.0, issue_size=3000.0,
        )
        assert ci.cb_over_rate == 5.0
        assert ci.issue_size == 3000.0


# ---------------------------------------------------------------------------
# on_bar 模板方法测试
# ---------------------------------------------------------------------------

class TestOnBar:
    @pytest.fixture
    def strategy(self):
        s = SimpleCBStrategy()
        s.on_start()
        return s

    def test_first_bar_triggers_rebalance(self, strategy):
        bars = {"A.SZ": _bar("A.SZ", 100.0)}
        intents = strategy.on_bar(bars, _ctx())
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) > 0

    def test_empty_bars_no_crash(self, strategy):
        intents = strategy.on_bar({}, _ctx())
        assert intents == []

    def test_non_rebalance_day_no_buys(self, strategy):
        cfg = SimpleConfig(rebalance_days=5)
        s = SimpleCBStrategy(cfg)
        s.on_start()
        # bar 1 = rebalance
        s.on_bar({"A.SZ": _bar("A.SZ", 100.0)}, _ctx())
        # bar 2 = no rebalance
        intents = s.on_bar({"A.SZ": _bar("A.SZ", 100.0)}, _ctx())
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) == 0


# ---------------------------------------------------------------------------
# 止损测试
# ---------------------------------------------------------------------------

class TestStopLoss:
    @pytest.fixture
    def strategy(self):
        s = SimpleCBStrategy()
        s.on_start()
        return s

    def test_stop_loss_triggers(self, strategy):
        strategy._entry_prices["A.SZ"] = 100.0
        bars = {"A.SZ": _bar("A.SZ", 80.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy.on_bar(bars, ctx)
        sells = [i for i in intents if i.side == Side.SELL and "止损" in (i.reason or "")]
        assert len(sells) >= 1

    def test_no_stop_loss_above_threshold(self, strategy):
        strategy._entry_prices["A.SZ"] = 100.0
        bars = {"A.SZ": _bar("A.SZ", 95.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy._check_stop_loss(bars, ctx)
        assert len(intents) == 0

    def test_no_entry_price_no_crash(self, strategy):
        """无入场价时止损不触发"""
        bars = {"A.SZ": _bar("A.SZ", 50.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy._check_stop_loss(bars, ctx)
        assert len(intents) == 0

    def test_stop_loss_triggers_on_low_not_close(self, strategy):
        """bar.low 触穿止损线但 close 未触穿 → 应触发止损（bar.low 修复回归验证）。

        stop_loss_pct=0.15, entry=100 → threshold=85
        close=87 > 85 (原实现不会触发), low=84 < 85 (正确实现应触发)
        """
        strategy._entry_prices["A.SZ"] = 100.0
        dt = datetime(2024, 3, 15, 15, 0)
        bar = Bar(
            symbol="A.SZ",
            start_ts=datetime(2024, 3, 15, 9, 30),
            end_ts=dt,
            open=90.0,
            high=92.0,
            low=84.0,   # < 85：触穿止损
            close=87.0,  # > 85：收盘未触穿
            volume=1000,
            metadata={
                "amount": 100.0, "issue_size": 5000.0,
                "maturity_date": "20280101", "stk_short_name": "正股",
            },
        )
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy._check_stop_loss({"A.SZ": bar}, ctx)
        assert len(intents) == 1
        assert intents[0].side == Side.SELL
        assert "止损" in (intents[0].reason or "")

    def test_no_stop_loss_when_low_above_threshold(self, strategy):
        """close 低于止损阈值但 low 未触穿 → 不应触发（bar.low 语义一致性验证）。

        _bar(close=87) → low = 87*0.98 = 85.26 > threshold(85) → 不触发
        """
        strategy._entry_prices["A.SZ"] = 100.0
        # _bar 构造: low = close * 0.98 = 87 * 0.98 = 85.26，略高于 threshold=85
        bars = {"A.SZ": _bar("A.SZ", 87.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy._check_stop_loss(bars, ctx)
        assert len(intents) == 0

    def test_stop_loss_reason_contains_low_and_entry(self, strategy):
        """止损触发时 reason 字段须包含 low 价与 entry 价，便于追踪。"""
        strategy._entry_prices["B.SH"] = 100.0
        bars = {"B.SH": _bar("B.SH", 80.0)}  # low = 80*0.98 = 78.4 << 85
        ctx = _ctx(positions={"B.SH": 50})
        intents = strategy._check_stop_loss(bars, ctx)
        assert len(intents) == 1
        reason = intents[0].reason or ""
        assert "low=" in reason
        assert "entry=" in reason


# ---------------------------------------------------------------------------
# 追踪止损测试
# ---------------------------------------------------------------------------

class TestTrailingStop:
    @pytest.fixture
    def strategy(self):
        cfg = SimpleConfig(stop_loss_pct=0.15, trailing_stop_pct=0.05)
        s = SimpleCBStrategy(cfg)
        s.on_start()
        return s

    def test_trailing_stop_not_triggered_initially(self, strategy):
        """刚买入时，价格没下跌不触发追踪止损。"""
        strategy._entry_prices["A.SZ"] = 100.0
        strategy._peak_prices["A.SZ"] = 100.0
        bars = {"A.SZ": _bar("A.SZ", 98.0)}  # low=96.04, trail=95 → 不触发
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy._check_stop_loss(bars, ctx)
        assert len(intents) == 0

    def test_trailing_stop_triggers_after_peak(self, strategy):
        """价格先涨后跌，跌破 peak*(1-5%) 触发追踪止损。"""
        strategy._entry_prices["A.SZ"] = 100.0
        strategy._peak_prices["A.SZ"] = 120.0  # 模拟涨到 120
        # trail_price = 120 * 0.95 = 114, bar low = 110*0.98 = 107.8 < 114 → 触发
        bars = {"A.SZ": _bar("A.SZ", 110.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy._check_stop_loss(bars, ctx)
        assert len(intents) == 1
        assert intents[0].side == Side.SELL
        assert "追踪止损" in (intents[0].reason or "")

    def test_trailing_stop_updates_peak(self, strategy):
        """高价更新 peak_prices。"""
        strategy._entry_prices["A.SZ"] = 100.0
        strategy._peak_prices["A.SZ"] = 100.0
        dt = datetime(2024, 3, 15, 15, 0)
        bar = Bar(
            symbol="A.SZ", start_ts=datetime(2024, 3, 15, 9, 30), end_ts=dt,
            open=105.0, high=110.0, low=104.0, close=108.0,
            volume=1000, metadata={
                "amount": 100.0, "issue_size": 5000.0,
                "maturity_date": "20280101", "stk_short_name": "正股",
            },
        )
        ctx = _ctx(positions={"A.SZ": 100})
        strategy._check_stop_loss({"A.SZ": bar}, ctx)
        assert strategy._peak_prices["A.SZ"] == 110.0

    def test_trailing_stop_disabled_when_zero(self):
        """trailing_stop_pct=0 不启用追踪止损。"""
        cfg = SimpleConfig(stop_loss_pct=0.15, trailing_stop_pct=0.0)
        s = SimpleCBStrategy(cfg)
        s.on_start()
        s._entry_prices["A.SZ"] = 100.0
        s._peak_prices["A.SZ"] = 120.0
        # trail would be 114, but disabled
        bars = {"A.SZ": _bar("A.SZ", 110.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = s._check_stop_loss(bars, ctx)
        assert len(intents) == 0  # 不触发追踪止损（固定止损 85 也不触发）

    def test_fixed_stop_takes_priority(self, strategy):
        """固定止损先于追踪止损触发。"""
        strategy._entry_prices["A.SZ"] = 100.0
        strategy._peak_prices["A.SZ"] = 100.0
        # 固定止损 = 85, low = 80*0.98 = 78.4 < 85 → 固定止损触发
        bars = {"A.SZ": _bar("A.SZ", 80.0)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy._check_stop_loss(bars, ctx)
        assert len(intents) == 1
        assert "止损" in (intents[0].reason or "")
        # 应该是固定止损而非追踪止损
        assert "追踪" not in (intents[0].reason or "")

    def test_on_fill_initializes_peak(self, strategy):
        """买入成交时 peak_prices 初始化为成交价。"""
        strategy.on_fill("A.SZ", Side.BUY, 100, 105.0)
        assert strategy._peak_prices["A.SZ"] == 105.0

    def test_on_fill_sell_clears_peak(self, strategy):
        """卖出成交时清除 peak_prices。"""
        strategy._peak_prices["A.SZ"] = 120.0
        strategy._entry_prices["A.SZ"] = 100.0
        strategy.on_fill("A.SZ", Side.SELL, 100, 115.0)
        assert "A.SZ" not in strategy._peak_prices


# ---------------------------------------------------------------------------
# 强赎风控测试
# ---------------------------------------------------------------------------

class TestForceRedeem:
    @pytest.fixture
    def strategy(self):
        cfg = SimpleConfig(force_redeem_threshold=130.0, force_redeem_days=3)
        s = SimpleCBStrategy(cfg)
        s.on_start()
        return s

    def test_called_immediate_sell(self, strategy):
        """已公告强赎立即卖出"""
        bars = {"A.SZ": _bar("A.SZ", 135.0, is_called=True)}
        ctx = _ctx(positions={"A.SZ": 100})
        intents = strategy._check_force_redeem(bars, ctx)
        assert any("强赎" in (i.reason or "") for i in intents)

    def test_consecutive_days_trigger(self, strategy):
        """连续 N 日 cb_value >= threshold 触发卖出"""
        ctx = _ctx(positions={"A.SZ": 100})
        for _ in range(2):
            strategy._check_force_redeem(
                {"A.SZ": _bar("A.SZ", 135.0, cb_value=132.0)}, ctx,
            )
        # 第 3 日触发
        intents = strategy._check_force_redeem(
            {"A.SZ": _bar("A.SZ", 135.0, cb_value=132.0)}, ctx,
        )
        assert any("强赎预警" in (i.reason or "") for i in intents)

    def test_counter_resets_on_drop(self, strategy):
        """中断后计数器重置"""
        ctx = _ctx(positions={"A.SZ": 100})
        strategy._check_force_redeem(
            {"A.SZ": _bar("A.SZ", 135.0, cb_value=132.0)}, ctx,
        )
        assert strategy._force_redeem_counter.get("A.SZ", 0) == 1
        strategy._check_force_redeem(
            {"A.SZ": _bar("A.SZ", 115.0, cb_value=110.0)}, ctx,
        )
        assert strategy._force_redeem_counter.get("A.SZ", 0) == 0


# ---------------------------------------------------------------------------
# 过滤 pipeline 测试
# ---------------------------------------------------------------------------

class TestFilterCandidates:
    @pytest.fixture
    def strategy(self):
        cfg = SimpleConfig(max_price=130.0, min_turnover=50.0, exclude_st=True)
        s = SimpleCBStrategy(cfg)
        s.on_start()
        return s

    def test_filters_high_price(self, strategy):
        bars = {"A.SZ": _bar("A.SZ", 150.0, amount=100.0)}
        candidates = strategy._filter_candidates(bars, _ctx())
        assert len(candidates) == 0

    def test_filters_low_turnover(self, strategy):
        bars = {"A.SZ": _bar("A.SZ", 100.0, amount=10.0)}
        candidates = strategy._filter_candidates(bars, _ctx())
        assert len(candidates) == 0

    def test_filters_st(self, strategy):
        bars = {"A.SZ": _bar("A.SZ", 100.0, amount=100.0, stk_short_name="*ST坏蛋")}
        candidates = strategy._filter_candidates(bars, _ctx())
        assert len(candidates) == 0

    def test_passes_valid_bar(self, strategy):
        bars = {"A.SZ": _bar("A.SZ", 100.0, amount=100.0)}
        candidates = strategy._filter_candidates(bars, _ctx())
        assert len(candidates) == 1
        assert candidates[0].symbol == "A.SZ"

    def test_filters_near_maturity(self, strategy):
        bars = {"A.SZ": _bar("A.SZ", 100.0, amount=100.0, maturity_date="20240401")}
        candidates = strategy._filter_candidates(bars, _ctx())
        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# on_fill 生命周期
# ---------------------------------------------------------------------------

class TestOnFill:
    def test_buy_records_entry_price(self):
        s = SimpleCBStrategy()
        s.on_fill("A.SZ", Side.BUY, 100, 105.0)
        assert s._entry_prices["A.SZ"] == 105.0

    def test_sell_removes_entry_price(self):
        s = SimpleCBStrategy()
        s._entry_prices["A.SZ"] = 100.0
        s.on_fill("A.SZ", Side.SELL, 100, 95.0)
        assert "A.SZ" not in s._entry_prices

    def test_sell_missing_symbol_no_crash(self):
        s = SimpleCBStrategy()
        s.on_fill("NONE.SZ", Side.SELL, 100, 95.0)  # should not raise


# ---------------------------------------------------------------------------
# 轮动调仓逻辑
# ---------------------------------------------------------------------------

class TestRebalance:
    def test_should_rebalance_first_bar(self):
        s = SimpleCBStrategy(SimpleConfig(rebalance_days=5))
        s._bar_count = 1
        assert s._should_rebalance() is True

    def test_should_not_rebalance_mid_period(self):
        s = SimpleCBStrategy(SimpleConfig(rebalance_days=5))
        s._bar_count = 3
        assert s._should_rebalance() is False

    def test_rebalance_at_period_boundary(self):
        s = SimpleCBStrategy(SimpleConfig(rebalance_days=5))
        s._bar_count = 6
        assert s._should_rebalance() is True


# ---------------------------------------------------------------------------
# 工具函数测试
# ---------------------------------------------------------------------------

class TestUtilFunctions:
    def test_days_to_maturity_normal(self):
        d = days_to_maturity("20250315", datetime(2024, 3, 15))
        assert d == 365 or d == 366  # leap year

    def test_days_to_maturity_invalid_format(self):
        d = days_to_maturity("invalid", datetime(2024, 3, 15))
        assert d == 9999

    def test_rating_score_aaa(self):
        assert rating_score("AAA") == 100

    def test_rating_score_unknown(self):
        assert rating_score("NR") == -1

    def test_rating_score_empty(self):
        assert rating_score("") == -1
