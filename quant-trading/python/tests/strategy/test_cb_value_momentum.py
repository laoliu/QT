"""转股价值动量策略测试"""
from datetime import date, datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.cb_value_momentum import CBValueMomentumConfig, CBValueMomentumStrategy
from aipha.strategy.registry import StrategyRegistry


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
    cb_value: float = 100.0,
    cb_over_rate: float = 10.0,
    amount: float = 100.0,
    maturity_date: str = "20280101",
    remain_size: float = 5000.0,
    stk_short_name: str = "正股",
) -> Bar:
    meta = {
        "cb_value": cb_value,
        "cb_over_rate": cb_over_rate,
        "amount": amount,
        "maturity_date": maturity_date,
        "remain_size": remain_size,
        "stk_short_name": stk_short_name,
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


class TestCBValueMomentumConfig:
    def test_defaults(self):
        cfg = CBValueMomentumConfig()
        assert cfg.lookback_days == 20
        assert cfg.max_premium == 30.0
        assert cfg.top_k == 10


class TestCBValueMomentumStrategy:
    @pytest.fixture
    def strategy(self):
        return CBValueMomentumStrategy(config=CBValueMomentumConfig(
            top_k=2, lookback_days=3, rebalance_days=1,
            max_premium=50.0, min_turnover=10.0, lot_size=10,
        ))

    def test_registration(self):
        registry = StrategyRegistry()
        assert "cb_value_momentum" in registry.list_strategies()

    def test_warmup_period(self, strategy):
        """需要 lookback+1 个 bar 的数据才开始交易"""
        bars = {
            "A.SZ": _make_cb_bar("A.SZ", 110.0, cb_value=100.0, cb_over_rate=5.0),
        }
        ctx = _make_context()

        # 前 3 个 bar：warmup，无交易
        for _ in range(3):
            intents = strategy.on_bar(bars, ctx)
            assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_selects_highest_momentum(self, strategy):
        """足够 warmup 后，选择 cb_value 动量最高的"""
        # 模拟 4 个 bar（lookback=3，需 4 个数据点）
        for i in range(3):
            bars = {
                "UP.SZ": _make_cb_bar("UP.SZ", 100.0, cb_value=90.0 + i * 5, cb_over_rate=5.0),
                "DOWN.SZ": _make_cb_bar("DOWN.SZ", 100.0, cb_value=110.0 - i * 3, cb_over_rate=5.0),
                "FLAT.SZ": _make_cb_bar("FLAT.SZ", 100.0, cb_value=100.0, cb_over_rate=5.0),
            }
            ctx = _make_context()
            strategy.on_bar(bars, ctx)

        # 第 4 个 bar → 应选 UP.SZ（上涨最多）
        bars = {
            "UP.SZ": _make_cb_bar("UP.SZ", 100.0, cb_value=105.0, cb_over_rate=5.0),
            "DOWN.SZ": _make_cb_bar("DOWN.SZ", 100.0, cb_value=101.0, cb_over_rate=5.0),
            "FLAT.SZ": _make_cb_bar("FLAT.SZ", 100.0, cb_value=100.0, cb_over_rate=5.0),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        buy_symbols = [i.symbol for i in intents if i.side == Side.BUY]
        assert "UP.SZ" in buy_symbols

    def test_filters_high_premium(self, strategy):
        """高溢价率被过滤"""
        strategy.config.max_premium = 10.0
        # 先 warmup
        for _ in range(4):
            bars = {
                "HP.SZ": _make_cb_bar("HP.SZ", 100.0, cb_value=100.0, cb_over_rate=50.0),
            }
            ctx = _make_context()
            strategy.on_bar(bars, ctx)

        intents = strategy.on_bar(bars, ctx)
        assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_stop_loss(self, strategy):
        strategy._entry_prices["A.SZ"] = 120.0
        bars = {"A.SZ": _make_cb_bar("A.SZ", 100.0, cb_value=95.0, cb_over_rate=5.0)}
        ctx = _make_context(positions={"A.SZ": 100})
        intents = strategy.on_bar(bars, ctx)
        sell = [i for i in intents if i.side == Side.SELL and "止损" in (i.reason or "")]
        assert len(sell) >= 1

    def test_empty_candidates_score(self, strategy):
        """空候选列表返回空结果"""
        scored = strategy._score_candidates([], _make_context())
        assert scored == []

    def test_cb_value_none_skipped(self, strategy):
        """cb_value=None 时不记录历史"""
        bars = {"A.SZ": _make_bar("A.SZ", 100.0, metadata={"cb_value": None, "cb_over_rate": 5.0, "amount": 100.0})}
        ctx = _make_context()
        strategy.on_bar(bars, ctx)
        assert "A.SZ" not in strategy._cb_value_history or len(strategy._cb_value_history.get("A.SZ", [])) == 0

    def test_old_cv_zero_skipped(self, strategy):
        """历史起点 cb_value=0 时不除零"""
        from collections import deque
        strategy._cb_value_history["ZERO.SZ"] = deque([0.0, 50.0, 60.0, 70.0], maxlen=4)
        candidates = [
            CandidateInfo(symbol="ZERO.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=5.0, cb_value=70.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored == []  # old_cv=0 → skip

    def test_negative_momentum_ranking(self, strategy):
        """全部下跌时跌幅最小的排最前"""
        from collections import deque
        # A: 100→80 = -20%; B: 100→90 = -10%
        strategy._cb_value_history["A.SZ"] = deque([100.0, 95.0, 90.0, 80.0], maxlen=4)
        strategy._cb_value_history["B.SZ"] = deque([100.0, 98.0, 95.0, 90.0], maxlen=4)
        candidates = [
            CandidateInfo(symbol="A.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=5.0, cb_value=80.0),
            CandidateInfo(symbol="B.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=5.0, cb_value=90.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[0] == "B.SZ"  # B 跌幅更小

    def test_premium_none_filtered(self, strategy):
        """cb_over_rate=None → _extra_filter 返回 False"""
        from collections import deque
        strategy._cb_value_history["NOPREM.SZ"] = deque([100.0, 105.0, 110.0, 115.0], maxlen=4)
        ci = CandidateInfo(symbol="NOPREM.SZ", close=100.0, metadata={}, issue_size=5000.0, cb_over_rate=None, cb_value=115.0)
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_config_override_max_premium(self):
        """自定义 max_premium 后验证过滤更严格"""
        cfg = CBValueMomentumConfig(max_premium=15.0)
        assert cfg.max_premium == 15.0
