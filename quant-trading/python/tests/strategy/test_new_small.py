"""次新小盘复合策略测试"""
from datetime import date, datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.new_small import NewSmallConfig, NewSmallStrategy
from aipha.strategy.registry import StrategyRegistry


def _make_bar(
    symbol: str,
    close: float,
    metadata: dict | None = None,
) -> Bar:
    dt = datetime(2024, 3, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 3, 15, 9, 30),
        end_ts=dt,
        open=close,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=1000,
        metadata=metadata,
    )


def _make_cb_bar(
    ts_code: str,
    close: float,
    issue_size: float = 30000.0,
    list_date: str = "20240201",
    amount: float = 100.0,
    stk_short_name: str = "正股",
    maturity_date: str = "20280101",
    remain_size: float = 5000.0,
) -> Bar:
    meta = {
        "issue_size": issue_size,
        "list_date": list_date,
        "amount": amount,
        "stk_short_name": stk_short_name,
        "maturity_date": maturity_date,
        "remain_size": remain_size,
    }
    return _make_bar(ts_code, close, metadata=meta)


def _make_context(
    positions: dict[str, int] | None = None,
    cash: float = 500_000.0,
) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 3, 15, 15, 0),
        positions=positions or {},
        cash=cash,
        nav=cash,
    )


class TestNewSmallConfig:
    def test_defaults(self):
        cfg = NewSmallConfig()
        assert cfg.top_k == 10
        assert cfg.max_age_days == 90
        assert cfg.max_issue_size == 50000.0
        assert cfg.min_issue_size == 3000.0
        assert cfg.stop_loss_pct == 0.15

    def test_custom(self):
        cfg = NewSmallConfig(top_k=5, max_age_days=60)
        assert cfg.top_k == 5
        assert cfg.max_age_days == 60


class TestNewSmallStrategy:
    @pytest.fixture
    def strategy(self):
        return NewSmallStrategy(config=NewSmallConfig(
            top_k=2, rebalance_days=1, max_age_days=90,
            max_issue_size=50000.0, min_turnover=10.0, lot_size=10,
        ))

    def test_registration(self):
        registry = StrategyRegistry()
        assert "new_small" in registry.list_strategies()

    def test_rebalance_schedule(self, strategy):
        strategy._bar_count = 1
        assert strategy._should_rebalance() is True
        strategy._bar_count = 2
        assert strategy._should_rebalance() is True  # rebalance_days=1 → every bar is rebalance

    def test_first_rebalance_selects_smallest(self, strategy):
        """选择次新中发行规模最小的"""
        bars = {
            "A.SZ": _make_cb_bar("A.SZ", 105.0, issue_size=10000.0, list_date="20240301"),
            "B.SZ": _make_cb_bar("B.SZ", 110.0, issue_size=5000.0, list_date="20240301"),
            "C.SZ": _make_cb_bar("C.SZ", 100.0, issue_size=20000.0, list_date="20240301"),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        buy_symbols = [i.symbol for i in intents if i.side == Side.BUY]
        # B.SZ(5000) < A.SZ(10000) < C.SZ(20000)
        assert "B.SZ" in buy_symbols
        assert "A.SZ" in buy_symbols
        assert "C.SZ" not in buy_symbols

    def test_filters_too_old(self, strategy):
        """超过 max_age_days 的被过滤"""
        bars = {
            "OLD.SZ": _make_cb_bar("OLD.SZ", 105.0, issue_size=5000.0, list_date="20230101"),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_filters_too_new(self, strategy):
        """min_age_days 之内的被过滤"""
        strategy.config.min_age_days = 5
        bars = {
            "NEW.SZ": _make_cb_bar("NEW.SZ", 105.0, issue_size=5000.0, list_date="20240314"),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_filters_large_issue(self, strategy):
        """发行规模超过上限的被过滤"""
        bars = {
            "BIG.SZ": _make_cb_bar("BIG.SZ", 105.0, issue_size=100000.0, list_date="20240301"),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_filters_st(self, strategy):
        """ST 正股被过滤"""
        bars = {
            "ST.SZ": _make_cb_bar("ST.SZ", 105.0, issue_size=5000.0,
                                   list_date="20240301", stk_short_name="*ST坏蛋"),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_stop_loss(self, strategy):
        """止损触发"""
        strategy._entry_prices["A.SZ"] = 120.0
        bars = {"A.SZ": _make_cb_bar("A.SZ", 95.0, issue_size=5000.0, list_date="20240301")}
        ctx = _make_context(positions={"A.SZ": 100})
        intents = strategy.on_bar(bars, ctx)
        sell_intents = [i for i in intents if i.side == Side.SELL and "止损" in (i.reason or "")]
        assert len(sell_intents) >= 1

    def test_fewer_candidates_than_top_k(self, strategy):
        """候选数 < top_k 时返回全部"""
        strategy.on_start()
        candidates = [
            CandidateInfo(symbol="ONLY.SZ", close=100.0, metadata={"issue_size": 1000.0},
                          issue_size=1000.0, cb_over_rate=5.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored == ["ONLY.SZ"]

    def test_empty_candidates_returns_empty(self, strategy):
        """空候选列表返回空"""
        strategy.on_start()
        scored = strategy._score_candidates([], _make_context())
        assert scored == []
