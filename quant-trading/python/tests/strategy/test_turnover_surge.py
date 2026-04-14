"""放量突破策略测试"""
from datetime import date, datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.turnover_surge import TurnoverSurgeConfig, TurnoverSurgeStrategy
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
    amount: float = 100.0,
    cb_over_rate: float = 10.0,
    maturity_date: str = "20280101",
    remain_size: float = 5000.0,
    stk_short_name: str = "正股",
) -> Bar:
    meta = {
        "amount": amount,
        "cb_over_rate": cb_over_rate,
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


class TestTurnoverSurgeConfig:
    def test_defaults(self):
        cfg = TurnoverSurgeConfig()
        assert cfg.min_surge_ratio == 2.0
        assert cfg.lookback_days == 20
        assert cfg.require_positive_return is True


class TestTurnoverSurgeStrategy:
    @pytest.fixture
    def strategy(self):
        return TurnoverSurgeStrategy(config=TurnoverSurgeConfig(
            top_k=2, lookback_days=3, rebalance_days=1,
            min_surge_ratio=2.0, min_turnover=10.0, lot_size=10,
            require_positive_return=False,  # 简化测试
        ))

    def test_registration(self):
        registry = StrategyRegistry()
        assert "turnover_surge" in registry.list_strategies()

    def test_warmup_needed(self, strategy):
        """需要足够历史才能计算放量比"""
        bars = {
            "A.SZ": _make_cb_bar("A.SZ", 105.0, amount=500.0),
        }
        ctx = _make_context()
        # 前 3 个 bar 不够 warmup
        for _ in range(3):
            intents = strategy.on_bar(bars, ctx)
            assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_detects_surge(self, strategy):
        """放量时产生信号"""
        # 先喂 3 个正常量能的 bar
        for _ in range(3):
            bars = {
                "SURGE.SZ": _make_cb_bar("SURGE.SZ", 100.0, amount=100.0),
                "NORM.SZ": _make_cb_bar("NORM.SZ", 100.0, amount=100.0),
            }
            ctx = _make_context()
            strategy.on_bar(bars, ctx)

        # 第 4 个 bar：SURGE 放量到 300（3倍），NORM 不变
        bars = {
            "SURGE.SZ": _make_cb_bar("SURGE.SZ", 100.0, amount=300.0),
            "NORM.SZ": _make_cb_bar("NORM.SZ", 100.0, amount=100.0),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        buy_symbols = [i.symbol for i in intents if i.side == Side.BUY]
        assert "SURGE.SZ" in buy_symbols

    def test_positive_return_filter(self):
        """require_positive_return=True 时过滤下跌放量"""
        strat = TurnoverSurgeStrategy(config=TurnoverSurgeConfig(
            top_k=2, lookback_days=3, rebalance_days=1,
            min_surge_ratio=2.0, min_turnover=10.0, lot_size=10,
            require_positive_return=True,
        ))

        # warmup 3 bars at price=100
        for _ in range(3):
            bars = {"A.SZ": _make_cb_bar("A.SZ", 100.0, amount=100.0)}
            ctx = _make_context()
            strat.on_bar(bars, ctx)

        # 第 4 bar：放量但下跌
        bars = {"A.SZ": _make_cb_bar("A.SZ", 95.0, amount=300.0)}
        ctx = _make_context()
        intents = strat.on_bar(bars, ctx)
        buy_symbols = [i.symbol for i in intents if i.side == Side.BUY]
        assert "A.SZ" not in buy_symbols

    def test_stop_loss(self, strategy):
        strategy._entry_prices["A.SZ"] = 120.0
        bars = {"A.SZ": _make_cb_bar("A.SZ", 100.0, amount=100.0)}
        ctx = _make_context(positions={"A.SZ": 100})
        intents = strategy.on_bar(bars, ctx)
        sell = [i for i in intents if i.side == Side.SELL and "止损" in (i.reason or "")]
        assert len(sell) >= 1

    def test_empty_candidates_score(self, strategy):
        """空候选列表返回空结果"""
        from aipha.strategy.cb_rotation_base import CandidateInfo
        scored = strategy._score_candidates([], _make_context())
        assert scored == []

    def test_surge_ratio_ranking(self, strategy):
        """不同放量倍数排序正确"""
        from aipha.strategy.cb_rotation_base import CandidateInfo
        candidates = [
            CandidateInfo(symbol="LOW.SZ", close=100.0, metadata={"_surge_ratio": 2.5}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="HIGH.SZ", close=100.0, metadata={"_surge_ratio": 5.0}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[0] == "HIGH.SZ"

    def test_high_premium_filtered(self, strategy):
        """高溢价率被过滤"""
        from collections import deque
        from aipha.strategy.cb_rotation_base import CandidateInfo
        strategy.config.max_premium = 20.0
        strategy._amount_history["HP.SZ"] = deque([100.0, 100.0, 100.0, 300.0], maxlen=4)
        ci = CandidateInfo(symbol="HP.SZ", close=100.0, metadata={"amount": 300.0}, issue_size=5000.0, cb_over_rate=50.0, cb_value=100.0, amount=300.0)
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_avg_amount_zero_filtered(self, strategy):
        """历史成交额全为 0 → avg_amt<=0 → 不通过"""
        from collections import deque
        from aipha.strategy.cb_rotation_base import CandidateInfo
        strategy._amount_history["ZERO.SZ"] = deque([0.0, 0.0, 0.0, 100.0], maxlen=4)
        ci = CandidateInfo(symbol="ZERO.SZ", close=100.0, metadata={"amount": 100.0}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, amount=100.0)
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_config_override_surge_ratio(self):
        """自定义 min_surge_ratio"""
        cfg = TurnoverSurgeConfig(min_surge_ratio=5.0)
        assert cfg.min_surge_ratio == 5.0

    def test_multiple_rebalance_cycles(self):
        """连续多个调仓周期，prev_close 正确更新"""
        strat = TurnoverSurgeStrategy(config=TurnoverSurgeConfig(
            top_k=2, lookback_days=3, rebalance_days=2,
            min_surge_ratio=2.0, min_turnover=10.0, lot_size=10,
            require_positive_return=False,
        ))
        ctx = _make_context()
        # 喂 6 个 bar
        for i in range(6):
            bars = {"A.SZ": _make_cb_bar("A.SZ", float(100 + i), amount=100.0)}
            strat.on_bar(bars, ctx)
        # 验证 prev_close 已更新
        assert "A.SZ" in strat._prev_close
