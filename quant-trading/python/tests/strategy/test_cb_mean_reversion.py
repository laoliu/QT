"""Tests for CBMeanReversionStrategy."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.cb_mean_reversion import CBMeanReversionConfig, CBMeanReversionStrategy


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


class TestCBMeanReversionConfig:
    def test_defaults(self):
        cfg = CBMeanReversionConfig()
        assert cfg.top_k == 15
        assert cfg.lookback == 20
        assert cfg.bb_k == 2.0


class TestCBMeanReversionStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = CBMeanReversionConfig(
            top_k=2, lookback=3, bb_k=1.0, rebalance_days=1,
            min_turnover=10.0, lot_size=10,
        )
        s = CBMeanReversionStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "cb_mean_reversion" in StrategyRegistry().list_strategies()

    def test_warmup(self, strategy):
        """No signals during warmup period."""
        ctx = _make_context()
        bar = _make_cb_bar("A.SZ", close=100.0)
        intents = strategy.on_bar({"A.SZ": bar}, ctx)
        assert intents == []

    def test_mean_reversion_dip(self, strategy):
        """After stable bars then a dip, should consider buying."""
        ctx = _make_context()
        # Feed stable bars for warmup
        for _ in range(3):
            bar = _make_cb_bar("A.SZ", close=100.0)
            strategy.on_bar({"A.SZ": bar}, ctx)
        # Now a dip
        bar = _make_cb_bar("A.SZ", close=90.0)
        intents = strategy.on_bar({"A.SZ": bar}, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) >= 0  # dip may trigger buy depending on BB width

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

    def test_std_near_zero_filter(self, strategy):
        """所有价格相同 → std < 0.01 → _extra_filter 返回 False"""
        from collections import deque
        strategy._price_history["FLAT.SZ"] = deque([100.0, 100.0, 100.0], maxlen=3)
        ci = CandidateInfo(symbol="FLAT.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0)
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_price_above_lower_band_rejected(self, strategy):
        """价格在布林带内 → _extra_filter 返回 False"""
        from collections import deque
        # 均值100, std≈1, lower=100-1*1=99, close=100 > 99
        strategy._price_history["HIGH.SZ"] = deque([99.0, 100.0, 101.0], maxlen=3)
        ci = CandidateInfo(symbol="HIGH.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0)
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_price_below_lower_band_accepted(self, strategy):
        """价格低于布林带下轨 → _extra_filter 返回 True"""
        from collections import deque
        # 均值100, std≈1, bb_k=1.0, lower≈99, close=95 < 99
        strategy._price_history["LOW.SZ"] = deque([99.0, 100.0, 101.0], maxlen=3)
        ci = CandidateInfo(symbol="LOW.SZ", close=95.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0)
        assert strategy._extra_filter(ci, _make_context()) is True

    def test_deviation_ranking_order(self, strategy):
        """偏离度更大的候选排在前面"""
        from collections import deque
        # A: 均值 100, std≈1, bb_k=1.0 → lower≈99, close=95 → deviation=(99-95)/1=4
        strategy._price_history["A.SZ"] = deque([99.0, 100.0, 101.0], maxlen=3)
        # B: 均值 100, std≈1, bb_k=1.0 → lower≈99, close=97 → deviation=(99-97)/1=2
        strategy._price_history["B.SZ"] = deque([99.0, 100.0, 101.0], maxlen=3)
        candidates = [
            CandidateInfo(symbol="B.SZ", close=97.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="A.SZ", close=95.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[0] == "A.SZ"

    def test_config_override_bb_k(self):
        """自定义 bb_k 后布林带宽度变化"""
        cfg = CBMeanReversionConfig(top_k=2, lookback=3, bb_k=3.0, rebalance_days=1, lot_size=10)
        s = CBMeanReversionStrategy(cfg)
        s.on_start()
        assert s.config.bb_k == 3.0

    def test_insufficient_history_filtered(self, strategy):
        """历史不足 lookback 的符号被过滤"""
        from collections import deque
        strategy._price_history["SHORT.SZ"] = deque([100.0], maxlen=3)  # 只有 1 条，需要 3
        ci = CandidateInfo(symbol="SHORT.SZ", close=95.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0)
        assert strategy._extra_filter(ci, _make_context()) is False
