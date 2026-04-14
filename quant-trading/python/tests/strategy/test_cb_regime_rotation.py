"""Tests for CBRegimeRotationStrategy."""
from collections import deque
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.cb_regime_rotation import (
    CBRegimeRotationConfig,
    CBRegimeRotationStrategy,
    MarketRegime,
)
from aipha.strategy.registry import StrategyRegistry


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 配置测试
# ---------------------------------------------------------------------------

class TestCBRegimeRotationConfig:
    def test_defaults(self):
        cfg = CBRegimeRotationConfig()
        assert cfg.top_k == 8
        assert cfg.regime_lookback == 20
        assert cfg.bull_threshold == 0.03
        assert cfg.bear_threshold == -0.02
        assert cfg.bull_w_momentum == 0.35
        assert cfg.bear_w_reversal == 0.40
        assert cfg.neutral_w_size == 0.25

    def test_custom_params(self):
        cfg = CBRegimeRotationConfig(top_k=5, regime_lookback=10)
        assert cfg.top_k == 5
        assert cfg.regime_lookback == 10


# ---------------------------------------------------------------------------
# 市场状态检测测试
# ---------------------------------------------------------------------------

class TestRegimeDetection:
    @pytest.fixture
    def strategy(self):
        cfg = CBRegimeRotationConfig(
            top_k=3,
            rebalance_days=1,
            buffer_k=1,
            min_turnover=5.0,
            warmup_bars=5,
            momentum_lookback=3,
            surge_lookback=5,
            reversal_lookback=3,
            regime_lookback=5,
            bull_threshold=0.03,
            bear_threshold=-0.02,
            max_premium=80.0,
            lot_size=10,
        )
        s = CBRegimeRotationStrategy(cfg)
        s.on_start()
        return s

    def test_default_regime_neutral(self, strategy):
        assert strategy.current_regime == MarketRegime.NEUTRAL

    def test_detect_bull(self, strategy):
        """持续上涨指标应检测为 BULL。"""
        # 构造上涨序列 (5天涨5% -> 超过 bull_threshold=3%)
        for val in [100.0, 101.0, 102.0, 103.0, 104.0, 106.0]:
            strategy._market_index.append(val)
        strategy._detect_regime()
        assert strategy.current_regime == MarketRegime.BULL

    def test_detect_bear(self, strategy):
        """持续下跌指标应检测为 BEAR。"""
        for val in [100.0, 99.0, 98.0, 97.5, 97.0, 96.0]:
            strategy._market_index.append(val)
        strategy._detect_regime()
        assert strategy.current_regime == MarketRegime.BEAR

    def test_detect_neutral(self, strategy):
        """震荡指标应检测为 NEUTRAL。"""
        for val in [100.0, 100.5, 100.0, 100.5, 100.0, 101.0]:
            strategy._market_index.append(val)
        strategy._detect_regime()
        assert strategy.current_regime == MarketRegime.NEUTRAL

    def test_detect_insufficient_data(self, strategy):
        """数据不足时保持 NEUTRAL。"""
        strategy._market_index.append(100.0)
        strategy._detect_regime()
        assert strategy.current_regime == MarketRegime.NEUTRAL


# ---------------------------------------------------------------------------
# 权重切换测试
# ---------------------------------------------------------------------------

class TestRegimeWeights:
    @pytest.fixture
    def strategy(self):
        cfg = CBRegimeRotationConfig(
            top_k=3,
            rebalance_days=1,
            buffer_k=1,
            min_turnover=5.0,
            warmup_bars=5,
            momentum_lookback=3,
            surge_lookback=5,
            reversal_lookback=3,
            regime_lookback=5,
            lot_size=10,
        )
        s = CBRegimeRotationStrategy(cfg)
        s.on_start()
        return s

    def test_bull_weights_favor_momentum(self, strategy):
        strategy._current_regime = MarketRegime.BULL
        w = strategy._get_regime_weights()
        assert w["momentum"] > w["reversal"]
        assert w["surge"] > w["size"]

    def test_bear_weights_favor_reversal(self, strategy):
        strategy._current_regime = MarketRegime.BEAR
        w = strategy._get_regime_weights()
        assert w["reversal"] > w["momentum"]
        assert w["price"] > w["surge"]

    def test_neutral_weights_balanced(self, strategy):
        strategy._current_regime = MarketRegime.NEUTRAL
        w = strategy._get_regime_weights()
        assert w["size"] > 0
        assert w["premium"] > 0
        assert w["reversal"] > 0

    def test_all_weights_sum_to_one(self, strategy):
        for regime in MarketRegime:
            strategy._current_regime = regime
            w = strategy._get_regime_weights()
            total = sum(w.values())
            assert 0.95 <= total <= 1.05


# ---------------------------------------------------------------------------
# 打分测试
# ---------------------------------------------------------------------------

class TestRegimeScoring:
    @pytest.fixture
    def strategy(self):
        cfg = CBRegimeRotationConfig(
            top_k=3,
            rebalance_days=1,
            buffer_k=1,
            min_turnover=5.0,
            warmup_bars=5,
            momentum_lookback=3,
            surge_lookback=5,
            reversal_lookback=3,
            regime_lookback=5,
            max_premium=80.0,
            lot_size=10,
        )
        s = CBRegimeRotationStrategy(cfg)
        s.on_start()
        return s

    def _setup_histories(self, strategy, symbol, prices, amounts):
        """Helper to set up price and amount histories."""
        strategy._price_history[symbol] = deque(prices, maxlen=20)
        strategy._amount_history[symbol] = deque(amounts, maxlen=30)

    def test_score_empty(self, strategy):
        assert strategy._score_candidates([], _make_context()) == []

    def test_score_single(self, strategy):
        self._setup_histories(strategy, "A.SZ",
                              [100.0, 102.0, 104.0, 106.0],
                              [100.0] * 6 + [150.0])
        ci = CandidateInfo(
            symbol="A.SZ", close=106.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0, amount=150.0,
        )
        result = strategy._score_candidates([ci], _make_context())
        assert result == ["A.SZ"]

    def test_bull_favors_momentum(self, strategy):
        """BULL 状态下高动量标的应排前。"""
        strategy._current_regime = MarketRegime.BULL
        # A: 高动量
        self._setup_histories(strategy, "A.SZ",
                              [80.0, 85.0, 90.0, 100.0],
                              [100.0] * 6 + [200.0])
        # B: 低动量
        self._setup_histories(strategy, "B.SZ",
                              [100.0, 100.0, 100.0, 100.0],
                              [100.0] * 6 + [100.0])

        cA = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0, amount=200.0,
        )
        cB = CandidateInfo(
            symbol="B.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0, amount=100.0,
        )
        result = strategy._score_candidates([cA, cB], _make_context())
        assert result[0] == "A.SZ"

    def test_bear_favors_reversal(self, strategy):
        """BEAR 状态下跌幅大（反转）标的应排前。"""
        strategy._current_regime = MarketRegime.BEAR
        # A: 大跌（反转信号强）
        self._setup_histories(strategy, "A.SZ",
                              [120.0, 115.0, 110.0, 100.0],
                              [100.0] * 6 + [100.0])
        # B: 上涨
        self._setup_histories(strategy, "B.SZ",
                              [90.0, 95.0, 100.0, 105.0],
                              [100.0] * 6 + [100.0])

        cA = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0, amount=100.0,
        )
        cB = CandidateInfo(
            symbol="B.SZ", close=105.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0, amount=100.0,
        )
        result = strategy._score_candidates([cA, cB], _make_context())
        assert result[0] == "A.SZ"

    def test_neutral_favors_size(self, strategy):
        """NEUTRAL 状态下小规模标的应有优势。"""
        strategy._current_regime = MarketRegime.NEUTRAL
        # Both same momentum/price/surge
        self._setup_histories(strategy, "A.SZ",
                              [100.0, 101.0, 102.0, 103.0],
                              [100.0] * 6 + [100.0])
        self._setup_histories(strategy, "B.SZ",
                              [100.0, 101.0, 102.0, 103.0],
                              [100.0] * 6 + [100.0])

        cA = CandidateInfo(
            symbol="A.SZ", close=103.0, metadata={},
            cb_over_rate=10.0, issue_size=3000.0, amount=100.0,
        )
        cB = CandidateInfo(
            symbol="B.SZ", close=103.0, metadata={},
            cb_over_rate=10.0, issue_size=20000.0, amount=100.0,
        )
        result = strategy._score_candidates([cA, cB], _make_context())
        assert result[0] == "A.SZ"

    def test_score_respects_top_k(self, strategy):
        symbols = [f"S{i}.SZ" for i in range(10)]
        for i, s in enumerate(symbols):
            self._setup_histories(strategy, s,
                                  [100.0, 101.0, 102.0, 103.0],
                                  [100.0] * 6 + [100.0])

        candidates = [
            CandidateInfo(
                symbol=s, close=103.0, metadata={},
                cb_over_rate=10.0, issue_size=5000.0 + i * 1000, amount=100.0,
            )
            for i, s in enumerate(symbols)
        ]
        result = strategy._score_candidates(candidates, _make_context())
        assert len(result) <= strategy.config.top_k + strategy.config.buffer_k


# ---------------------------------------------------------------------------
# 集成测试
# ---------------------------------------------------------------------------

class TestRegimeRotationIntegration:
    @pytest.fixture
    def strategy(self):
        cfg = CBRegimeRotationConfig(
            top_k=3,
            rebalance_days=1,
            buffer_k=1,
            min_turnover=5.0,
            warmup_bars=3,
            momentum_lookback=3,
            surge_lookback=5,
            reversal_lookback=3,
            regime_lookback=3,
            max_premium=80.0,
            lot_size=10,
        )
        s = CBRegimeRotationStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "cb_regime_rotation" in StrategyRegistry().list_strategies()

    def test_name(self):
        s = CBRegimeRotationStrategy()
        assert s.name == "cb_regime_rotation"

    def test_extra_filter_premium(self, strategy):
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=90.0, issue_size=5000.0,
        )
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_extra_filter_no_history(self, strategy):
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0,
        )
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_extra_filter_pass(self, strategy):
        strategy._price_history["A.SZ"] = deque([100.0] * 10, maxlen=20)
        strategy._amount_history["A.SZ"] = deque([100.0] * 10, maxlen=30)
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=10.0, issue_size=5000.0,
        )
        assert strategy._extra_filter(ci, _make_context()) is True

    def test_stop_loss(self, strategy):
        bar = _make_cb_bar("X.SZ", close=85.0)
        ctx = _make_context(positions={"X.SZ": 10})
        strategy._entry_prices["X.SZ"] = 100.0
        intents = strategy.on_bar({"X.SZ": bar}, ctx)
        sells = [i for i in intents if i.side == Side.SELL and i.symbol == "X.SZ"]
        assert len(sells) >= 1

    def test_on_fill(self, strategy):
        strategy.on_fill("A.SZ", Side.BUY, 10, 100.0)
        assert strategy._entry_prices["A.SZ"] == 100.0

    def test_on_bar_builds_market_index(self, strategy):
        """on_bar 应构建市场等权指数。"""
        bar = _make_cb_bar("A.SZ", close=100.0, amount=200.0)
        strategy.on_bar({"A.SZ": bar}, _make_context())
        assert len(strategy._market_index) == 1

    def test_end_to_end_flow(self, strategy):
        """完整流程。"""
        ctx = _make_context()
        bars = {}
        for s in ["A.SZ", "B.SZ", "C.SZ", "D.SZ"]:
            bars[s] = _make_cb_bar(
                s, close=100.0, amount=200.0,
                issue_size=5000.0, cb_over_rate=10.0,
            )
        n_bars = strategy.config.warmup_bars + max(
            strategy.config.momentum_lookback,
            strategy.config.surge_lookback,
            strategy.config.reversal_lookback,
        ) + 2
        for _ in range(n_bars):
            intents = strategy.on_bar(bars, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) > 0

    def test_regime_transitions(self, strategy):
        """市场状态在喂入不同数据后应正确切换。"""
        ctx = _make_context()
        # Feed rising market
        for i in range(10):
            bars = {"A.SZ": _make_cb_bar("A.SZ", close=100.0 + i * 2, amount=100.0)}
            strategy.on_bar(bars, ctx)

        # Should detect bull after enough rising bars
        # (depends on regime_lookback and bull_threshold)
        regime_after_rise = strategy.current_regime

        # Feed falling market
        for i in range(10):
            bars = {"A.SZ": _make_cb_bar("A.SZ", close=120.0 - i * 3, amount=100.0)}
            strategy.on_bar(bars, ctx)

        regime_after_fall = strategy.current_regime

        # Regimes should differ
        assert regime_after_rise != regime_after_fall or \
               regime_after_fall == MarketRegime.BEAR
