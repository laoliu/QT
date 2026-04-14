"""Tests for CBMultiAlphaStrategy."""
from collections import deque
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.cb_multi_alpha import CBMultiAlphaConfig, CBMultiAlphaStrategy
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

class TestCBMultiAlphaConfig:
    def test_defaults(self):
        cfg = CBMultiAlphaConfig()
        assert cfg.top_k == 8
        assert cfg.max_price == 150.0
        assert cfg.max_issue_size == 20000.0
        assert cfg.w_size == 0.30
        assert cfg.w_momentum == 0.20
        assert cfg.w_surge == 0.20
        assert cfg.w_premium == 0.15
        assert cfg.w_price == 0.15
        assert cfg.warmup_bars == 25
        assert cfg.momentum_lookback == 10
        assert cfg.surge_lookback == 20

    def test_custom_weights(self):
        cfg = CBMultiAlphaConfig(w_size=0.50, w_momentum=0.10)
        assert cfg.w_size == 0.50
        assert cfg.w_momentum == 0.10

    def test_kwargs_construction(self):
        cfg = CBMultiAlphaConfig(top_k=5, buffer_k=2, max_price=130.0)
        assert cfg.top_k == 5
        assert cfg.buffer_k == 2
        assert cfg.max_price == 130.0


# ---------------------------------------------------------------------------
# 策略测试
# ---------------------------------------------------------------------------

class TestCBMultiAlphaStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = CBMultiAlphaConfig(
            top_k=3,
            rebalance_days=1,
            buffer_k=1,
            min_turnover=5.0,
            warmup_bars=5,
            momentum_lookback=3,
            surge_lookback=5,
            max_premium=80.0,
            lot_size=10,
        )
        s = CBMultiAlphaStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "cb_multi_alpha" in StrategyRegistry().list_strategies()

    def test_name(self, strategy):
        assert strategy.name == "cb_multi_alpha"

    def test_config_property(self, strategy):
        assert strategy.config.top_k == 3

    def test_warmup_no_signals(self, strategy):
        """预热期内不产生交易信号。"""
        ctx = _make_context()
        bar = _make_cb_bar("A.SZ", close=100.0)
        for _ in range(4):  # warmup_bars=5, so bars 1-4 should have no rebalance
            intents = strategy.on_bar({"A.SZ": bar}, ctx)
        # During warmup, should not produce buy intents (may have stop-loss)
        buys = [i for i in intents if i.side == Side.BUY]
        assert buys == []

    def test_extra_filter_premium(self, strategy):
        """溢价率过高应被过滤。"""
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=90.0, issue_size=5000.0,
        )
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_extra_filter_no_price_history(self, strategy):
        """无价格历史应被过滤。"""
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0,
        )
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_extra_filter_no_amount_history(self, strategy):
        """无成交额历史应被过滤。"""
        strategy._price_history["A.SZ"] = deque([100.0] * 10, maxlen=15)
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0,
        )
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_extra_filter_pass(self, strategy):
        """符合条件应通过。"""
        strategy._price_history["A.SZ"] = deque([100.0] * 10, maxlen=15)
        strategy._amount_history["A.SZ"] = deque([200.0] * 10, maxlen=30)
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0,
        )
        assert strategy._extra_filter(ci, _make_context()) is True

    def test_score_empty_candidates(self, strategy):
        """空候选列表返回空。"""
        assert strategy._score_candidates([], _make_context()) == []

    def test_score_single_candidate(self, strategy):
        """单个候选直接返回。"""
        strategy._price_history["A.SZ"] = deque([90.0, 92.0, 95.0, 100.0], maxlen=15)
        strategy._amount_history["A.SZ"] = deque([100.0] * 6 + [200.0], maxlen=30)
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0, amount=200.0,
        )
        result = strategy._score_candidates([ci], _make_context())
        assert result == ["A.SZ"]

    def test_score_multi_candidates_ranking(self, strategy):
        """多候选排名：小规模+高动量+高放量应排前。"""
        # A: small, rising, high surge
        strategy._price_history["A.SZ"] = deque([90.0, 92.0, 95.0, 110.0], maxlen=15)
        strategy._amount_history["A.SZ"] = deque([50.0] * 5 + [300.0], maxlen=30)
        # B: large, flat, low surge
        strategy._price_history["B.SZ"] = deque([100.0, 100.0, 100.0, 100.0], maxlen=15)
        strategy._amount_history["B.SZ"] = deque([200.0] * 5 + [200.0], maxlen=30)

        cA = CandidateInfo(
            symbol="A.SZ", close=110.0, metadata={},
            cb_over_rate=5.0, issue_size=3000.0, amount=300.0,
        )
        cB = CandidateInfo(
            symbol="B.SZ", close=100.0, metadata={},
            cb_over_rate=20.0, issue_size=15000.0, amount=200.0,
        )
        result = strategy._score_candidates([cA, cB], _make_context())
        # A should rank higher (smaller, more momentum, more surge, lower premium)
        assert result[0] == "A.SZ"

    def test_score_respects_top_k(self, strategy):
        """结果数量不超过 top_k + buffer_k。"""
        symbols = [f"S{i}.SZ" for i in range(10)]
        for s in symbols:
            strategy._price_history[s] = deque([100.0, 102.0, 104.0, 106.0], maxlen=15)
            strategy._amount_history[s] = deque([100.0] * 6 + [150.0], maxlen=30)

        candidates = [
            CandidateInfo(
                symbol=s, close=100.0 + i, metadata={},
                cb_over_rate=10.0, issue_size=5000.0 + i * 1000, amount=150.0,
            )
            for i, s in enumerate(symbols)
        ]
        result = strategy._score_candidates(candidates, _make_context())
        assert len(result) <= strategy.config.top_k + strategy.config.buffer_k

    def test_stop_loss_triggers(self, strategy):
        """止损触发。"""
        bar = _make_cb_bar("X.SZ", close=85.0)
        ctx = _make_context(positions={"X.SZ": 10})
        strategy._entry_prices["X.SZ"] = 100.0
        intents = strategy.on_bar({"X.SZ": bar}, ctx)
        sells = [i for i in intents if i.side == Side.SELL and i.symbol == "X.SZ"]
        assert len(sells) >= 1

    def test_on_fill_updates_entry_price(self, strategy):
        """on_fill 应更新入场价。"""
        strategy.on_fill("A.SZ", Side.BUY, 10, 105.0)
        assert strategy._entry_prices["A.SZ"] == 105.0
        strategy.on_fill("A.SZ", Side.SELL, 10, 110.0)
        assert "A.SZ" not in strategy._entry_prices

    def test_on_bar_update_state_builds_history(self, strategy):
        """_on_bar_update_state 应正确构建历史。"""
        bar = _make_cb_bar("A.SZ", close=100.0, amount=200.0)
        strategy._on_bar_update_state({"A.SZ": bar}, _make_context())
        assert len(strategy._price_history["A.SZ"]) == 1
        assert strategy._price_history["A.SZ"][-1] == 100.0
        assert len(strategy._amount_history["A.SZ"]) == 1
        assert strategy._amount_history["A.SZ"][-1] == 200.0

    def test_should_rebalance_during_warmup(self, strategy):
        """预热期应返回 False。"""
        strategy._bar_count = 3  # warmup_bars=5
        assert strategy._should_rebalance() is False

    def test_should_rebalance_after_warmup(self, strategy):
        """预热后首根 bar 应触发调仓。"""
        strategy._bar_count = 6  # warmup_bars=5, effective=1
        assert strategy._should_rebalance() is True

    def test_end_to_end_produces_orders(self, strategy):
        """完整流程（预热+交易）应在预热结束后产出订单。"""
        ctx = _make_context()
        bars = {}
        for s in ["A.SZ", "B.SZ", "C.SZ", "D.SZ"]:
            bars[s] = _make_cb_bar(
                s, close=100.0, amount=200.0,
                issue_size=5000.0, cb_over_rate=10.0,
            )
        # Feed warmup bars
        for _ in range(strategy.config.warmup_bars + strategy.config.momentum_lookback + 2):
            intents = strategy.on_bar(bars, ctx)
        # Should now produce buy orders
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) > 0

    def test_premium_none_handled(self, strategy):
        """cb_over_rate=None 时使用默认值 50。"""
        strategy._price_history["A.SZ"] = deque([100.0, 102.0, 104.0, 106.0], maxlen=15)
        strategy._amount_history["A.SZ"] = deque([100.0] * 6 + [150.0], maxlen=30)
        ci = CandidateInfo(
            symbol="A.SZ", close=106.0, metadata={},
            cb_over_rate=None, issue_size=5000.0, amount=150.0,
        )
        result = strategy._score_candidates([ci], _make_context())
        assert result == ["A.SZ"]

    def test_weight_normalization(self, strategy):
        """权重总和不为1时也能正确归一化。"""
        strategy._config.w_size = 1.0
        strategy._config.w_momentum = 1.0
        strategy._config.w_surge = 1.0
        strategy._config.w_premium = 1.0
        strategy._config.w_price = 1.0

        strategy._price_history["A.SZ"] = deque([100.0, 102.0, 104.0, 106.0], maxlen=15)
        strategy._amount_history["A.SZ"] = deque([100.0] * 6 + [150.0], maxlen=30)
        ci = CandidateInfo(
            symbol="A.SZ", close=106.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0, amount=150.0,
        )
        result = strategy._score_candidates([ci], _make_context())
        assert result == ["A.SZ"]
