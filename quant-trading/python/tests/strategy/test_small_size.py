"""Tests for SmallSizeStrategy."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.small_size import SmallSizeConfig, SmallSizeStrategy


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


class TestSmallSizeConfig:
    def test_defaults(self):
        cfg = SmallSizeConfig()
        assert cfg.top_k == 20


class TestSmallSizeStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = SmallSizeConfig(
            top_k=2, rebalance_days=1, max_issue_size=50000.0,
            min_turnover=10.0, lot_size=10,
        )
        s = SmallSizeStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "small_size" in StrategyRegistry().list_strategies()

    def test_score_candidates(self, strategy):
        candidates = [
            CandidateInfo(symbol="A.SZ", close=100.0, metadata={"issue_size": 3000.0}, issue_size=3000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="B.SZ", close=100.0, metadata={"issue_size": 1000.0}, issue_size=1000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="C.SZ", close=100.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[:2] == ["B.SZ", "A.SZ"]

    def test_stop_loss(self, strategy):
        bar = _make_cb_bar("X.SZ", close=80.0, issue_size=2000.0)
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

    def test_nan_issue_size_defaults_to_inf(self, strategy):
        """issue_size 使用默认 inf 时排在最后"""
        candidates = [
            CandidateInfo(symbol="A.SZ", close=100.0, metadata={}, issue_size=float("inf"), cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="B.SZ", close=100.0, metadata={}, issue_size=1000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[0] == "B.SZ"

    def test_top_k_limits_output(self):
        """top_k 限制输出数量"""
        cfg = SmallSizeConfig(top_k=3, rebalance_days=1, lot_size=10)
        s = SmallSizeStrategy(cfg)
        s.on_start()
        candidates = [
            CandidateInfo(symbol=f"S{i}.SZ", close=100.0, metadata={}, issue_size=float(i * 100), cb_over_rate=10.0, cb_value=100.0)
            for i in range(1, 8)
        ]
        scored = s._score_candidates(candidates, _make_context())
        assert len(scored) == 3

    def test_config_override_via_kwargs(self):
        """自定义 config 参数传递正确"""
        cfg = SmallSizeConfig(top_k=5, max_price=120.0, min_turnover=50.0)
        s = SmallSizeStrategy(cfg)
        assert s.config.top_k == 5
        assert s.config.max_price == 120.0
        assert s.config.min_turnover == 50.0

    def test_equal_issue_size_tiebreak(self, strategy):
        """issue_size 相同时按 symbol 做二级排序，保证确定性。"""
        candidates = [
            CandidateInfo(symbol="C.SZ", close=100.0, metadata={}, issue_size=2000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="A.SZ", close=100.0, metadata={}, issue_size=2000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="B.SZ", close=100.0, metadata={}, issue_size=2000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert len(scored) == 2  # top_k=2
        assert scored == ["A.SZ", "B.SZ"]

    def test_extreme_issue_size_values(self, strategy):
        """极端 issue_size 值不崩溃"""
        candidates = [
            CandidateInfo(symbol="TINY.SZ", close=100.0, metadata={}, issue_size=0.01, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="HUGE.SZ", close=100.0, metadata={}, issue_size=9999999.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[0] == "TINY.SZ"
        assert scored[1] == "HUGE.SZ"

    def test_fewer_candidates_than_top_k(self, strategy):
        """候选数量 < top_k 时应返回全部可用候选而非报错"""
        # strategy.config.top_k == 2, 但只给 1 个候选
        candidates = [
            CandidateInfo(symbol="ONLY.SZ", close=100.0, metadata={}, issue_size=500.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored == ["ONLY.SZ"]

    def test_on_bar_no_rebalance_day(self):
        """非调仓日不产生买入信号"""
        cfg = SmallSizeConfig(top_k=2, rebalance_days=5, lot_size=10, min_turnover=10.0)
        s = SmallSizeStrategy(cfg)
        s.on_start()
        ctx = _make_context()
        bar = _make_cb_bar("A.SZ", close=100.0, issue_size=2000.0)
        # bar #1 is rebalance day (triggers buy), bar #2 is not
        s.on_bar({"A.SZ": bar}, ctx)
        intents = s.on_bar({"A.SZ": bar}, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) == 0

    # ------ P0-2: min_tradable_candidates 测试 ------

    def test_min_tradable_candidates_skip(self):
        """候选池 < min_tradable_candidates 时返回空列表（跳过调仓）"""
        cfg = SmallSizeConfig(
            top_k=5, rebalance_days=1, lot_size=10,
            min_turnover=10.0, min_tradable_candidates=8,
        )
        s = SmallSizeStrategy(cfg)
        s.on_start()
        # 只给 3 个候选 < 8
        candidates = [
            CandidateInfo(symbol=f"C{i}.SZ", close=100.0, metadata={},
                          issue_size=float(1000 * i), cb_over_rate=10.0,
                          cb_value=100.0, amount=200.0)
            for i in range(1, 4)
        ]
        scored = s._score_candidates(candidates, _make_context())
        assert scored == [], "候选不足时应返回空列表"

    def test_min_tradable_candidates_pass(self):
        """候选池 >= min_tradable_candidates 时正常返回"""
        cfg = SmallSizeConfig(
            top_k=3, rebalance_days=1, lot_size=10,
            min_turnover=10.0, min_tradable_candidates=3,
        )
        s = SmallSizeStrategy(cfg)
        s.on_start()
        candidates = [
            CandidateInfo(symbol=f"C{i}.SZ", close=100.0, metadata={},
                          issue_size=float(1000 * i), cb_over_rate=10.0,
                          cb_value=100.0, amount=200.0)
            for i in range(1, 5)  # 4 个 >= 3
        ]
        scored = s._score_candidates(candidates, _make_context())
        assert len(scored) == 3
        assert scored[0] == "C1.SZ"  # issue_size 最小排第一

    def test_min_tradable_candidates_zero_disabled(self):
        """min_tradable_candidates=0 时不做检查（向后兼容）"""
        cfg = SmallSizeConfig(
            top_k=2, rebalance_days=1, lot_size=10,
            min_turnover=10.0, min_tradable_candidates=0,
        )
        s = SmallSizeStrategy(cfg)
        s.on_start()
        candidates = [
            CandidateInfo(symbol="A.SZ", close=100.0, metadata={},
                          issue_size=500.0, cb_over_rate=10.0,
                          cb_value=100.0, amount=200.0),
        ]
        scored = s._score_candidates(candidates, _make_context())
        assert scored == ["A.SZ"], "min=0 时即使只有 1 个也应正常返回"
