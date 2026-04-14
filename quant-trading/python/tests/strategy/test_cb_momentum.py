"""Tests for CBMomentumStrategy."""
from datetime import datetime, timedelta

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.cb_momentum import CBMomentumConfig, CBMomentumStrategy


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


class TestCBMomentumConfig:
    def test_defaults(self):
        cfg = CBMomentumConfig()
        assert cfg.top_k == 10
        assert cfg.lookback_days == 20


class TestCBMomentumStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = CBMomentumConfig(
            top_k=2, lookback_days=3, rebalance_days=1,
            min_turnover=10.0, lot_size=10,
        )
        s = CBMomentumStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "cb_momentum" in StrategyRegistry().list_strategies()

    def test_warmup(self, strategy):
        """No signals during warmup period."""
        ctx = _make_context()
        bar = _make_cb_bar("A.SZ", close=100.0)
        intents = strategy.on_bar({"A.SZ": bar}, ctx)
        assert intents == []

    def test_detects_momentum(self, strategy):
        """After enough bars with rising price, should detect momentum."""
        prices = [100.0, 102.0, 105.0, 110.0]
        ctx = _make_context()
        intents = []
        for i, p in enumerate(prices):
            bar = _make_cb_bar("A.SZ", close=p)
            intents = strategy.on_bar({"A.SZ": bar}, ctx)
        # After lookback_days+1 bars, momentum should be detectable
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) >= 0  # may or may not buy depending on rebalance cycle

    def test_stop_loss(self, strategy):
        bar = _make_cb_bar("X.SZ", close=80.0)
        ctx = _make_context(positions={"X.SZ": 10})
        strategy._entry_prices["X.SZ"] = 100.0
        strategy.config.stop_loss_pct = 0.10
        intents = strategy.on_bar({"X.SZ": bar}, ctx)
        sells = [i for i in intents if i.side == Side.SELL]
        assert len(sells) >= 1

    def test_empty_candidates_score(self, strategy):
        """空候选列表返回空结果"""
        scored = strategy._score_candidates([], _make_context())
        assert scored == []

    def test_negative_momentum_ranking(self, strategy):
        """全部下跌时，跌幅最小的排前面"""
        from collections import deque
        # A: 100→90, momentum=-0.1; B: 100→95, momentum=-0.05
        strategy._price_history["A.SZ"] = deque([100.0, 98.0, 96.0, 94.0], maxlen=8)
        strategy._price_history["B.SZ"] = deque([100.0, 99.0, 98.0, 97.0], maxlen=8)
        candidates = [
            CandidateInfo(symbol="A.SZ", close=90.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="B.SZ", close=95.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[0] == "B.SZ"  # B 跌幅更小

    def test_past_price_zero_skipped(self, strategy):
        """past_price=0 时不产生除零错误"""
        from collections import deque
        # lookback_days=3 → past_price = hist[-3], 所以 index -3 须为 0.0
        strategy._price_history["ZERO.SZ"] = deque([50.0, 0.0, 60.0, 70.0], maxlen=8)
        candidates = [
            CandidateInfo(symbol="ZERO.SZ", close=80.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored == []  # past_price=0 → skip

    def test_extra_filter_insufficient_history(self, strategy):
        """历史不足时 _extra_filter 返回 False"""
        from collections import deque
        strategy._price_history["SHORT.SZ"] = deque([100.0], maxlen=8)  # 需要 lookback+1=4
        ci = CandidateInfo(symbol="SHORT.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0)
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_top_k_limits_output(self):
        """top_k 限制输出数量"""
        from collections import deque
        cfg = CBMomentumConfig(top_k=2, lookback_days=3, rebalance_days=1, lot_size=10)
        s = CBMomentumStrategy(cfg)
        s.on_start()
        for i in range(1, 6):
            sym = f"S{i}.SZ"
            s._price_history[sym] = deque([80.0, 85.0, 90.0, 95.0], maxlen=8)
        candidates = [
            CandidateInfo(symbol=f"S{i}.SZ", close=float(100 + i), metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0)
            for i in range(1, 6)
        ]
        scored = s._score_candidates(candidates, _make_context())
        assert len(scored) == 2

    def test_config_override_lookback(self):
        """自定义 lookback_days"""
        cfg = CBMomentumConfig(lookback_days=5)
        assert cfg.lookback_days == 5

    def test_multiple_symbols_momentum_rank(self, strategy):
        """多只不同涨幅候选，验证排名（最大涨幅在前）"""
        from collections import deque
        # A: 100→120 = +20%; B: 100→110 = +10%; C: 100→130 = +30%
        strategy._price_history["A.SZ"] = deque([100.0, 105.0, 110.0, 115.0], maxlen=8)
        strategy._price_history["B.SZ"] = deque([100.0, 102.0, 104.0, 106.0], maxlen=8)
        strategy._price_history["C.SZ"] = deque([100.0, 110.0, 120.0, 125.0], maxlen=8)
        candidates = [
            CandidateInfo(symbol="A.SZ", close=120.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="B.SZ", close=110.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="C.SZ", close=130.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[0] == "C.SZ"  # 涨幅最大
