"""Tests for LowPriceStrategy."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.low_price import LowPriceConfig, LowPriceStrategy


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


class TestLowPriceConfig:
    def test_defaults(self):
        cfg = LowPriceConfig()
        assert cfg.top_k == 30


class TestLowPriceStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = LowPriceConfig(
            top_k=2, rebalance_days=1, min_turnover=10.0, lot_size=10,
        )
        s = LowPriceStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "low_price" in StrategyRegistry().list_strategies()

    def test_score_candidates(self, strategy):
        candidates = [
            CandidateInfo(symbol="A.SZ", close=120.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="B.SZ", close=105.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="C.SZ", close=110.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[:2] == ["B.SZ", "C.SZ"]

    def test_stop_loss(self, strategy):
        bar = _make_cb_bar("X.SZ", close=80.0)
        ctx = _make_context(positions={"X.SZ": 10})
        strategy._entry_prices["X.SZ"] = 100.0
        strategy.config.stop_loss_pct = 0.10
        intents = strategy.on_bar({"X.SZ": bar}, ctx)
        sells = [i for i in intents if i.side == Side.SELL]
        assert len(sells) >= 1

    def test_empty_candidates_returns_empty(self, strategy):
        """空候选列表返回空结果"""
        scored = strategy._score_candidates([], _make_context())
        assert scored == []

    def test_top_k_limits_output(self):
        """top_k 限制输出数量"""
        cfg = LowPriceConfig(top_k=3, rebalance_days=1, lot_size=10)
        s = LowPriceStrategy(cfg)
        s.on_start()
        candidates = [
            CandidateInfo(symbol=f"S{i}.SZ", close=float(100 + i), metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0)
            for i in range(1, 8)
        ]
        scored = s._score_candidates(candidates, _make_context())
        assert len(scored) == 3

    def test_config_override(self):
        """自定义 config 参数传递正确"""
        cfg = LowPriceConfig(top_k=5, max_price=105.0)
        s = LowPriceStrategy(cfg)
        assert s.config.top_k == 5
        assert s.config.max_price == 105.0

    def test_equal_close_tiebreak(self, strategy):
        """close 相同时排序稳定"""
        candidates = [
            CandidateInfo(symbol="C.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="A.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="B.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert len(scored) == 2
        assert len(set(scored)) == 2

    def test_extreme_low_price(self, strategy):
        """极端低价不崩溃且排在第一"""
        candidates = [
            CandidateInfo(symbol="CHEAP.SZ", close=0.01, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="NORMAL.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[0] == "CHEAP.SZ"

    def test_no_rebalance_on_off_day(self):
        """非调仓日不产生买入信号"""
        cfg = LowPriceConfig(top_k=2, rebalance_days=5, lot_size=10, min_turnover=10.0)
        s = LowPriceStrategy(cfg)
        s.on_start()
        ctx = _make_context()
        bar = _make_cb_bar("A.SZ", close=95.0)
        s.on_bar({"A.SZ": bar}, ctx)
        intents = s.on_bar({"A.SZ": bar}, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) == 0

    def test_fewer_candidates_than_top_k(self, strategy):
        """候选数量 < top_k 时应返回全部可用候选而非报错"""
        # strategy.config.top_k == 2, 但只给 1 个候选
        candidates = [
            CandidateInfo(symbol="ONLY.SZ", close=90.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored == ["ONLY.SZ"]

    def test_multiple_rebalance_periods(self):
        """多次调仓周期验证轮动正确"""
        cfg = LowPriceConfig(top_k=1, rebalance_days=2, lot_size=10, min_turnover=10.0)
        s = LowPriceStrategy(cfg)
        s.on_start()
        ctx = _make_context()
        bar = _make_cb_bar("A.SZ", close=95.0)
        all_buys = []
        for i in range(6):
            intents = s.on_bar({"A.SZ": bar}, ctx)
            buys = [x for x in intents if x.side == Side.BUY]
            all_buys.append(len(buys))
        # rebalance_days=2: bars 1,3,5 trigger, bars 2,4,6 don't
        assert all_buys[0] >= 1  # bar 1: rebalance
        assert all_buys[1] == 0  # bar 2: no rebalance
