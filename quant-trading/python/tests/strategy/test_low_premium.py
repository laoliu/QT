"""Tests for LowPremiumStrategy."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.low_premium import LowPremiumConfig, LowPremiumStrategy


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


class TestLowPremiumConfig:
    def test_defaults(self):
        cfg = LowPremiumConfig()
        assert cfg.top_k == 15
        assert cfg.max_premium == 50.0


class TestLowPremiumStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = LowPremiumConfig(
            top_k=2, rebalance_days=1, max_premium=50.0,
            min_turnover=10.0, lot_size=10,
        )
        s = LowPremiumStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "low_premium" in StrategyRegistry().list_strategies()

    def test_extra_filter_pass(self, strategy):
        ci = CandidateInfo(symbol="A.SZ", close=100.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=30.0, cb_value=100.0)
        assert strategy._extra_filter(ci, _make_context()) is True

    def test_extra_filter_fail_high(self, strategy):
        ci = CandidateInfo(symbol="B.SZ", close=100.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=60.0, cb_value=100.0)
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_extra_filter_fail_none(self, strategy):
        ci = CandidateInfo(symbol="C.SZ", close=100.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=None, cb_value=100.0)
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_score_candidates(self, strategy):
        candidates = [
            CandidateInfo(symbol="A.SZ", close=100.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=5.0, cb_value=100.0),
            CandidateInfo(symbol="B.SZ", close=100.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=15.0, cb_value=100.0),
            CandidateInfo(symbol="C.SZ", close=100.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=25.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[:2] == ["A.SZ", "B.SZ"]

    def test_stop_loss(self, strategy):
        bar = _make_cb_bar("X.SZ", close=80.0)
        ctx = _make_context(positions={"X.SZ": 10})
        strategy._entry_prices["X.SZ"] = 100.0
        strategy.config.stop_loss_pct = 0.10
        intents = strategy.on_bar({"X.SZ": bar}, ctx)
        sells = [i for i in intents if i.side == Side.SELL]
        assert len(sells) >= 1

    def test_score_filters_none_premium(self, strategy):
        """_score_candidates 应过滤掉 cb_over_rate=None 的候选"""
        candidates = [
            CandidateInfo(symbol="OK.SZ", close=100.0, metadata={"issue_size": 5000.0},
                          issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="BAD.SZ", close=100.0, metadata={"issue_size": 5000.0},
                          issue_size=5000.0, cb_over_rate=None, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert "BAD.SZ" not in scored
        assert "OK.SZ" in scored

    def test_fewer_candidates_than_top_k(self, strategy):
        """候选数 < top_k 时应返回全部可用"""
        candidates = [
            CandidateInfo(symbol="ONLY.SZ", close=100.0, metadata={"issue_size": 5000.0},
                          issue_size=5000.0, cb_over_rate=5.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored == ["ONLY.SZ"]

    def test_negative_premium_sorted_first(self, strategy):
        """负溢价应排在最前"""
        candidates = [
            CandidateInfo(symbol="POS.SZ", close=100.0, metadata={"issue_size": 5000.0},
                          issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="NEG.SZ", close=100.0, metadata={"issue_size": 5000.0},
                          issue_size=5000.0, cb_over_rate=-3.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[0] == "NEG.SZ"

    def test_empty_candidates(self, strategy):
        """空候选列表应返回空"""
        scored = strategy._score_candidates([], _make_context())
        assert scored == []

    def test_first_rebalance_generates_buys(self, strategy):
        """首次调仓应生成买入信号"""
        bar = _make_cb_bar("A.SZ", close=100.0, cb_over_rate=5.0)
        ctx = _make_context()
        intents = strategy.on_bar({"A.SZ": bar}, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) > 0
