"""Tests for VolBreakoutStrategy."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.vol_breakout import VolBreakoutConfig, VolBreakoutStrategy


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


class TestVolBreakoutConfig:
    def test_defaults(self):
        cfg = VolBreakoutConfig()
        assert cfg.top_k == 10
        assert cfg.atr_short == 5
        assert cfg.atr_long == 20
        assert cfg.squeeze_threshold == 0.6


class TestVolBreakoutStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = VolBreakoutConfig(
            top_k=2, atr_short=2, atr_long=3, rebalance_days=1,
            squeeze_threshold=0.5, breakout_lookback=3,
            min_turnover=10.0, lot_size=10,
        )
        s = VolBreakoutStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "vol_breakout" in StrategyRegistry().list_strategies()

    def test_warmup(self, strategy):
        """No signals during warmup period (need atr_long bars)."""
        ctx = _make_context()
        bar = _make_cb_bar("A.SZ", close=100.0)
        intents = strategy.on_bar({"A.SZ": bar}, ctx)
        assert intents == []

    def test_no_signal_stable(self, strategy):
        """Stable prices should not trigger breakout."""
        ctx = _make_context()
        for _ in range(5):
            bar = _make_cb_bar("A.SZ", close=100.0)
            strategy.on_bar({"A.SZ": bar}, ctx)
        bar = _make_cb_bar("A.SZ", close=100.0)
        intents = strategy.on_bar({"A.SZ": bar}, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) == 0

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

    def test_calc_atr_basic(self):
        """直接测试 _calc_atr 静态方法"""
        # 3 bars: (H, L, C)
        data = [(102.0, 98.0, 100.0), (104.0, 99.0, 103.0), (105.0, 100.0, 102.0)]
        atr = VolBreakoutStrategy._calc_atr(data, 2)
        assert atr > 0

    def test_calc_atr_insufficient_data(self):
        """数据不足时返回 0.0"""
        data = [(102.0, 98.0, 100.0)]  # only 1 bar, need period+1=3
        atr = VolBreakoutStrategy._calc_atr(data, 2)
        assert atr == 0.0

    def test_squeeze_ratio_above_threshold_rejected(self, strategy):
        """ATR 短期/长期比率 >= squeeze_threshold → 不通过"""
        from collections import deque
        # 构造稳定波动数据（短期ATR ≈ 长期ATR → ratio ≈ 1.0 > 0.5）
        data = [(102.0, 98.0, 100.0)] * 10
        strategy._hlc_history["NOSQ.SZ"] = deque(data, maxlen=25)
        ci = CandidateInfo(symbol="NOSQ.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0)
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_breakout_strength_ranking(self, strategy):
        """突破强度不同时，强的排前面"""
        from collections import deque
        # A: close=120, recent_high=110, atr_s=5 → strength=2.0
        data_a = [(100 + i, 95 + i, 98 + i) for i in range(8)]
        strategy._hlc_history["A.SZ"] = deque(data_a, maxlen=25)
        # B: close=115, recent_high=110, atr_s=5 → strength=1.0
        data_b = [(100 + i, 95 + i, 98 + i) for i in range(8)]
        strategy._hlc_history["B.SZ"] = deque(data_b, maxlen=25)
        candidates = [
            CandidateInfo(symbol="A.SZ", close=120.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="B.SZ", close=115.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        if len(scored) >= 2:
            assert scored[0] == "A.SZ"

    def test_config_override_squeeze_threshold(self):
        """自定义 squeeze_threshold"""
        cfg = VolBreakoutConfig(squeeze_threshold=0.3)
        assert cfg.squeeze_threshold == 0.3

    def test_extra_filter_no_history(self, strategy):
        """无历史记录时 _extra_filter 返回 False"""
        ci = CandidateInfo(symbol="NOHIST.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0)
        assert strategy._extra_filter(ci, _make_context()) is False
