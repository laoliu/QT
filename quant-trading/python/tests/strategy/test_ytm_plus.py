"""YTM 增强策略测试"""
from datetime import date, datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.ytm_plus import YTMPlusConfig, YTMPlusStrategy
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
    cb_over_rate: float = 10.0,
    amount: float = 100.0,
    maturity_date: str = "20280101",
    remain_size: float = 5000.0,
    stk_short_name: str = "正股",
) -> Bar:
    meta = {
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


class TestYTMPlusConfig:
    def test_defaults(self):
        cfg = YTMPlusConfig()
        assert cfg.ytm_weight == 0.5
        assert cfg.premium_weight == 0.5
        assert cfg.max_price == 115.0
        assert cfg.min_years_to_maturity == 0.5

    def test_weights_sum(self):
        cfg = YTMPlusConfig(ytm_weight=0.7, premium_weight=0.3)
        assert cfg.ytm_weight + cfg.premium_weight == pytest.approx(1.0)


class TestYTMPlusStrategy:
    @pytest.fixture
    def strategy(self):
        return YTMPlusStrategy(config=YTMPlusConfig(
            top_k=2, rebalance_days=1, max_price=115.0,
            ytm_weight=0.5, premium_weight=0.5,
            min_turnover=10.0, lot_size=10,
        ))

    def test_registration(self):
        registry = StrategyRegistry()
        assert "ytm_plus" in registry.list_strategies()

    def test_prefers_high_ytm_low_premium(self, strategy):
        """高 YTM + 低溢价率的排名靠前"""
        bars = {
            # close=90, ytm ≈ (100-90)/90/4 = 2.78%, premium=5 → 好
            "GOOD.SZ": _make_cb_bar("GOOD.SZ", 90.0, cb_over_rate=5.0),
            # close=95, ytm ≈ (100-95)/95/4 = 1.32%, premium=20 → 差
            "BAD.SZ": _make_cb_bar("BAD.SZ", 95.0, cb_over_rate=20.0),
            # close=85, ytm ≈ (100-85)/85/4 = 4.41%, premium=15 → 中
            "MID.SZ": _make_cb_bar("MID.SZ", 85.0, cb_over_rate=15.0),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        buy_symbols = [i.symbol for i in intents if i.side == Side.BUY]
        assert len(buy_symbols) == 2
        # GOOD (high ytm + low prem) and MID (highest ytm) expected
        assert "GOOD.SZ" in buy_symbols

    def test_filters_high_price(self, strategy):
        """超过 max_price 的被过滤"""
        bars = {
            "EXP.SZ": _make_cb_bar("EXP.SZ", 130.0, cb_over_rate=5.0),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_filters_near_maturity(self, strategy):
        """即将到期的被过滤"""
        bars = {
            "NEAR.SZ": _make_cb_bar("NEAR.SZ", 95.0, cb_over_rate=5.0,
                                     maturity_date="20240401"),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_needs_at_least_two_candidates(self, strategy):
        """只有1个候选时不交易（排名无意义）"""
        bars = {
            "ALONE.SZ": _make_cb_bar("ALONE.SZ", 95.0, cb_over_rate=5.0),
        }
        ctx = _make_context()
        intents = strategy.on_bar(bars, ctx)
        assert len([i for i in intents if i.side == Side.BUY]) == 0

    def test_stop_loss(self, strategy):
        strategy._entry_prices["A.SZ"] = 100.0
        bars = {"A.SZ": _make_cb_bar("A.SZ", 85.0, cb_over_rate=5.0)}
        ctx = _make_context(positions={"A.SZ": 100})
        intents = strategy.on_bar(bars, ctx)
        sell = [i for i in intents if i.side == Side.SELL and "止损" in (i.reason or "")]
        assert len(sell) >= 1

    def test_extra_filter_none_premium(self, strategy):
        """cb_over_rate=None 应被过滤"""
        ci = CandidateInfo(
            symbol="N.SZ", close=90.0, metadata={"issue_size": 5000.0},
            issue_size=5000.0, cb_over_rate=None, cb_value=100.0,
        )
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_fewer_candidates_than_top_k(self, strategy):
        """候选数 < top_k（但 >= 2）时返回全部"""
        strategy.on_start()
        candidates = [
            CandidateInfo(symbol="A.SZ", close=90.0, metadata={"issue_size": 5000.0},
                          issue_size=5000.0, cb_over_rate=5.0, cb_value=100.0,
                          maturity_date="20280101"),
            CandidateInfo(symbol="B.SZ", close=85.0, metadata={"issue_size": 5000.0},
                          issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0,
                          maturity_date="20280101"),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert len(scored) == 2

    def test_empty_candidates(self, strategy):
        """空候选列表返回空"""
        strategy.on_start()
        scored = strategy._score_candidates([], _make_context())
        assert scored == []

    def test_config_custom_weights(self):
        """自定义权重配置"""
        cfg = YTMPlusConfig(ytm_weight=0.8, premium_weight=0.2)
        assert cfg.ytm_weight == 0.8
        assert cfg.premium_weight == 0.2
