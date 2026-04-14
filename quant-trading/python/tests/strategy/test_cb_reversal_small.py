"""Tests for CBReversalSmallStrategy."""
from collections import deque
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.cb_reversal_small import CBReversalSmallConfig, CBReversalSmallStrategy
from aipha.strategy.registry import StrategyRegistry


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def _make_bar(symbol: str, close: float, metadata: dict | None = None) -> Bar:
    dt = datetime(2024, 3, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 3, 15, 9, 30),
        end_ts=dt,
        open=close, high=close * 1.02, low=close * 0.98,
        close=close, volume=1000, metadata=metadata,
    )


def _make_cb_bar(
    ts_code: str, close: float,
    amount: float = 100.0,
    cb_over_rate: float = 10.0,
    cb_value: float = 100.0,
    maturity_date: str = "20280101",
    issue_size: float = 5000.0,
    stk_short_name: str = "正股",
    **extra,
) -> Bar:
    meta = {
        "amount": amount,
        "cb_over_rate": cb_over_rate,
        "cb_value": cb_value,
        "maturity_date": maturity_date,
        "issue_size": issue_size,
        "stk_short_name": stk_short_name,
        **extra,
    }
    return _make_bar(ts_code, close, metadata=meta)


def _make_context(
    positions: dict[str, int] | None = None,
    cash: float = 500_000.0,
) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 3, 15, 15, 0),
        positions=positions or {},
        cash=cash, nav=cash,
    )


# ---------------------------------------------------------------------------
# 配置测试
# ---------------------------------------------------------------------------

class TestCBReversalSmallConfig:
    def test_defaults(self):
        cfg = CBReversalSmallConfig()
        assert cfg.top_k == 8
        assert cfg.rebalance_days == 3
        assert cfg.reversal_lookback == 5
        assert cfg.size_weight == 0.30
        assert cfg.reversal_weight == 0.70
        assert cfg.max_single_drop == -0.20

    def test_custom_params(self):
        cfg = CBReversalSmallConfig(top_k=5, reversal_lookback=3)
        assert cfg.top_k == 5
        assert cfg.reversal_lookback == 3


# ---------------------------------------------------------------------------
# 策略测试
# ---------------------------------------------------------------------------

class TestCBReversalSmallStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = CBReversalSmallConfig(
            top_k=3,
            rebalance_days=1,
            buffer_k=1,
            min_turnover=5.0,
            warmup_bars=3,
            reversal_lookback=3,
            max_premium=80.0,
            max_single_drop=-0.20,
            lot_size=10,
        )
        s = CBReversalSmallStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "cb_reversal_small" in StrategyRegistry().list_strategies()

    def test_name(self, strategy):
        assert strategy.name == "cb_reversal_small"

    def test_config_property(self, strategy):
        assert strategy.config.top_k == 3

    def test_warmup_no_signals(self, strategy):
        """预热期内不产生交易信号。"""
        ctx = _make_context()
        bar = _make_cb_bar("A.SZ", close=100.0)
        for _ in range(2):  # warmup_bars=3
            intents = strategy.on_bar({"A.SZ": bar}, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert buys == []

    def test_extra_filter_premium_too_high(self, strategy):
        """溢价率过高被过滤。"""
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=90.0, issue_size=5000.0,
        )
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_extra_filter_no_price_history(self, strategy):
        """无价格历史被过滤。"""
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0,
        )
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_extra_filter_extreme_drop_filtered(self, strategy):
        """极端暴跌（超过 max_single_drop）被过滤。"""
        # 3 天前 130.0 → 现在 100.0 → return = -23%
        strategy._price_history["A.SZ"] = deque([130.0, 120.0, 110.0, 100.0], maxlen=10)
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0,
        )
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_extra_filter_moderate_drop_passes(self, strategy):
        """适度下跌（未超过 max_single_drop）通过。"""
        # 3 天前 110.0 → 现在 100.0 → return = -9%
        strategy._price_history["A.SZ"] = deque([110.0, 107.0, 103.0, 100.0], maxlen=10)
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0,
        )
        assert strategy._extra_filter(ci, _make_context()) is True

    def test_score_empty(self, strategy):
        assert strategy._score_candidates([], _make_context()) == []

    def test_score_single(self, strategy):
        strategy._price_history["A.SZ"] = deque([110.0, 107.0, 105.0, 100.0], maxlen=10)
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0, amount=100.0,
        )
        result = strategy._score_candidates([ci], _make_context())
        assert result == ["A.SZ"]

    def test_score_reversal_ranking(self, strategy):
        """跌幅最大 + 最小规模应排前。"""
        # A: 跌 10%, 小规模
        strategy._price_history["A.SZ"] = deque([110.0, 108.0, 105.0, 99.0], maxlen=10)
        # B: 涨 5%, 大规模
        strategy._price_history["B.SZ"] = deque([100.0, 101.0, 103.0, 105.0], maxlen=10)

        cA = CandidateInfo(
            symbol="A.SZ", close=99.0, metadata={},
            cb_over_rate=10.0, issue_size=3000.0, amount=100.0,
        )
        cB = CandidateInfo(
            symbol="B.SZ", close=105.0, metadata={},
            cb_over_rate=10.0, issue_size=15000.0, amount=100.0,
        )
        result = strategy._score_candidates([cA, cB], _make_context())
        assert result[0] == "A.SZ"  # Bigger drop + smaller size

    def test_score_pure_reversal(self, strategy):
        """相同规模下，跌幅大的排前。"""
        # Both same issue_size
        strategy._price_history["A.SZ"] = deque([120.0, 115.0, 110.0, 100.0], maxlen=10)
        strategy._price_history["B.SZ"] = deque([105.0, 104.0, 103.0, 102.0], maxlen=10)

        cA = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0, amount=100.0,
        )
        cB = CandidateInfo(
            symbol="B.SZ", close=102.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0, amount=100.0,
        )
        result = strategy._score_candidates([cA, cB], _make_context())
        assert result[0] == "A.SZ"  # Greater reversal opportunity

    def test_score_pure_size(self, strategy):
        """相同跌幅下，规模小的排前。"""
        strategy._price_history["A.SZ"] = deque([105.0, 104.0, 103.0, 100.0], maxlen=10)
        strategy._price_history["B.SZ"] = deque([105.0, 104.0, 103.0, 100.0], maxlen=10)

        cA = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=3000.0, amount=100.0,
        )
        cB = CandidateInfo(
            symbol="B.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=15000.0, amount=100.0,
        )
        result = strategy._score_candidates([cA, cB], _make_context())
        assert result[0] == "A.SZ"  # Smaller size

    def test_score_respects_top_k(self, strategy):
        """结果数不超过 top_k + buffer_k。"""
        symbols = [f"S{i}.SZ" for i in range(10)]
        for i, s in enumerate(symbols):
            strategy._price_history[s] = deque([110.0 - i, 108.0, 105.0, 100.0 - i], maxlen=10)

        candidates = [
            CandidateInfo(
                symbol=s, close=100.0 - i, metadata={},
                cb_over_rate=10.0, issue_size=5000.0 + i * 1000, amount=100.0,
            )
            for i, s in enumerate(symbols)
        ]
        result = strategy._score_candidates(candidates, _make_context())
        assert len(result) <= strategy.config.top_k + strategy.config.buffer_k

    def test_stop_loss(self, strategy):
        """止损触发。"""
        bar = _make_cb_bar("X.SZ", close=85.0)
        ctx = _make_context(positions={"X.SZ": 10})
        strategy._entry_prices["X.SZ"] = 100.0
        intents = strategy.on_bar({"X.SZ": bar}, ctx)
        sells = [i for i in intents if i.side == Side.SELL and i.symbol == "X.SZ"]
        assert len(sells) >= 1

    def test_on_fill(self, strategy):
        strategy.on_fill("A.SZ", Side.BUY, 10, 100.0)
        assert strategy._entry_prices["A.SZ"] == 100.0
        strategy.on_fill("A.SZ", Side.SELL, 10, 105.0)
        assert "A.SZ" not in strategy._entry_prices

    def test_on_bar_update_state(self, strategy):
        bar = _make_cb_bar("A.SZ", close=100.0)
        strategy._on_bar_update_state({"A.SZ": bar}, _make_context())
        assert len(strategy._price_history["A.SZ"]) == 1

    def test_should_rebalance_during_warmup(self, strategy):
        strategy._bar_count = 2  # warmup_bars=3
        assert strategy._should_rebalance() is False

    def test_should_rebalance_after_warmup(self, strategy):
        strategy._bar_count = 4  # effective=1
        assert strategy._should_rebalance() is True

    def test_end_to_end_flow(self, strategy):
        """完整流程应在预热后产出订单。"""
        ctx = _make_context()
        bars = {}
        for s in ["A.SZ", "B.SZ", "C.SZ", "D.SZ"]:
            bars[s] = _make_cb_bar(
                s, close=100.0, amount=200.0,
                issue_size=5000.0, cb_over_rate=10.0,
            )
        for _ in range(strategy.config.warmup_bars + strategy.config.reversal_lookback + 2):
            intents = strategy.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) > 0
