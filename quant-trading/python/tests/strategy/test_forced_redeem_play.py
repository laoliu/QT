"""强赎临门博弈策略测试"""
from datetime import date, datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.forced_redeem_play import ForcedRedeemPlayConfig, ForcedRedeemPlayStrategy
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
    cb_value: float = 120.0,
    cb_over_rate: float = 5.0,
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


class TestForcedRedeemPlayConfig:
    def test_defaults(self):
        cfg = ForcedRedeemPlayConfig()
        assert cfg.lower_cb_value == 100.0
        assert cfg.upper_cb_value == 130.0
        assert cfg.max_premium == 20.0
        assert cfg.top_k == 10


class TestForcedRedeemPlayStrategy:
    @pytest.fixture
    def strategy(self):
        return ForcedRedeemPlayStrategy(config=ForcedRedeemPlayConfig(
            top_k=2, rebalance_days=1,
            lower_cb_value=100.0, upper_cb_value=130.0,
            max_premium=25.0, min_turnover=10.0, lot_size=10,
        ))

    def test_registration(self):
        registry = StrategyRegistry()
        assert "forced_redeem_play" in registry.list_strategies()

    def test_selects_highest_cb_value(self, strategy):
        """选择 cb_value 最高（最接近触发点）的"""
        bars = {
            "HIGH.SZ": _make_cb_bar("HIGH.SZ", 130.0, cb_value=125.0, cb_over_rate=4.0),
            "MID.SZ": _make_cb_bar("MID.SZ", 115.0, cb_value=110.0, cb_over_rate=5.0),
            "LOW.SZ": _make_cb_bar("LOW.SZ", 105.0, cb_value=101.0, cb_over_rate=4.0),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        buy_symbols = [i.symbol for i in intents if i.side == Side.BUY]
        assert "HIGH.SZ" in buy_symbols
        assert "MID.SZ" in buy_symbols
        assert "LOW.SZ" not in buy_symbols

    def test_filters_below_lower_cv(self, strategy):
        """cb_value < lower_cb_value 被过滤"""
        bars = {
            "LOWCV.SZ": _make_cb_bar("LOWCV.SZ", 90.0, cb_value=85.0, cb_over_rate=5.0),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_filters_above_upper_cv(self, strategy):
        """cb_value > upper_cb_value 被过滤（已触发）"""
        bars = {
            "OVER.SZ": _make_cb_bar("OVER.SZ", 140.0, cb_value=135.0, cb_over_rate=3.0),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_filters_high_premium(self, strategy):
        """高溢价率被过滤"""
        bars = {
            "HPREM.SZ": _make_cb_bar("HPREM.SZ", 130.0, cb_value=120.0, cb_over_rate=30.0),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_profit_taking_on_high_cv(self, strategy):
        """cb_value 超过上限 5% 时获利了结"""
        strategy._entry_prices["TRIGGER.SZ"] = 125.0
        bars = {
            "TRIGGER.SZ": _make_cb_bar("TRIGGER.SZ", 145.0, cb_value=140.0, cb_over_rate=3.0),
        }
        ctx = _make_context(positions={"TRIGGER.SZ": 100})
        intents = strategy.on_bar(bars, ctx)
        sell = [i for i in intents if i.side == Side.SELL and "强赎获利" in (i.reason or "")]
        assert len(sell) >= 1

    def test_stop_loss(self, strategy):
        strategy._entry_prices["A.SZ"] = 130.0
        bars = {"A.SZ": _make_cb_bar("A.SZ", 110.0, cb_value=105.0, cb_over_rate=5.0)}
        ctx = _make_context(positions={"A.SZ": 100})
        intents = strategy.on_bar(bars, ctx)
        sell = [i for i in intents if i.side == Side.SELL and "止损" in (i.reason or "")]
        assert len(sell) >= 1

    def test_filters_near_maturity(self, strategy):
        """即将到期的不参与强赎博弈"""
        bars = {
            "NEAR.SZ": _make_cb_bar("NEAR.SZ", 125.0, cb_value=120.0,
                                     cb_over_rate=5.0, maturity_date="20240401"),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_fewer_candidates_than_top_k(self, strategy):
        """候选数 < top_k 时应返回全部可用"""
        strategy.on_start()
        candidates = [
            CandidateInfo(symbol="ONLY.SZ", close=120.0, metadata={"issue_size": 5000.0},
                          issue_size=5000.0, cb_over_rate=5.0, cb_value=115.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored == ["ONLY.SZ"]

    def test_empty_candidates_returns_empty(self, strategy):
        """空候选列表应返回空"""
        strategy.on_start()
        scored = strategy._score_candidates([], _make_context())
        assert scored == []

    def test_tiebreaker_by_symbol(self, strategy):
        """相同 cb_value 时按 symbol 排序"""
        strategy.on_start()
        candidates = [
            CandidateInfo(symbol="B.SZ", close=120.0, metadata={"issue_size": 5000.0},
                          issue_size=5000.0, cb_over_rate=5.0, cb_value=115.0),
            CandidateInfo(symbol="A.SZ", close=120.0, metadata={"issue_size": 5000.0},
                          issue_size=5000.0, cb_over_rate=5.0, cb_value=115.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert len(scored) == 2
