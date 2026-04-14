"""Tests for DefensiveValueStrategy."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.defensive_value import DefensiveValueConfig, DefensiveValueStrategy


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
    maturity_date: str = "20270601",
    issue_size: float = 50000.0,
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


class TestDefensiveValueConfig:
    def test_defaults(self):
        cfg = DefensiveValueConfig()
        assert cfg.top_k == 25
        assert cfg.rebalance_days == 10
        assert cfg.min_issue_size == 30000.0
        assert cfg.max_price == 115.0
        assert cfg.max_premium == 40.0
        assert cfg.buffer_k == 5
        assert cfg.min_positions == 10

    def test_override(self):
        cfg = DefensiveValueConfig(top_k=30, min_issue_size=50000.0)
        assert cfg.top_k == 30
        assert cfg.min_issue_size == 50000.0


class TestDefensiveValueStrategy:
    def test_registry(self):
        from aipha.strategy.registry import StrategyRegistry
        registry = StrategyRegistry()
        cls = registry.get("defensive_value")
        assert cls is DefensiveValueStrategy

    def test_init_default(self):
        s = DefensiveValueStrategy()
        assert s.config.top_k == 25

    def test_init_kwargs(self):
        s = DefensiveValueStrategy(top_k=30, min_issue_size=50000.0)
        assert s.config.top_k == 30

    def test_filter_excludes_small_issue(self):
        """min_issue_size 必须排除小盘（与 small_size 反向）"""
        s = DefensiveValueStrategy(DefensiveValueConfig(min_issue_size=30000.0))
        s.on_start()
        ctx = _make_context()
        bars = {
            "BIG": _make_cb_bar("BIG", 100.0, issue_size=50000.0),
            "SMALL": _make_cb_bar("SMALL", 100.0, issue_size=10000.0),
        }
        candidates = s._filter_candidates(bars, ctx)
        symbols = [c.symbol for c in candidates]
        assert "BIG" in symbols
        assert "SMALL" not in symbols

    def test_filter_excludes_high_premium(self):
        """溢价率超上限应被排除"""
        s = DefensiveValueStrategy(DefensiveValueConfig(max_premium=30.0))
        s.on_start()
        ctx = _make_context()
        bars = {
            "LOW_PREM": _make_cb_bar("LOW_PREM", 105.0, cb_over_rate=15.0),
            "HIGH_PREM": _make_cb_bar("HIGH_PREM", 105.0, cb_over_rate=50.0),
        }
        candidates = s._filter_candidates(bars, ctx)
        symbols = [c.symbol for c in candidates]
        assert "LOW_PREM" in symbols
        assert "HIGH_PREM" not in symbols

    def test_filter_excludes_long_maturity(self):
        """到期日太远应被排除"""
        s = DefensiveValueStrategy(DefensiveValueConfig(max_years_to_maturity=3.0))
        s.on_start()
        ctx = _make_context()
        bars = {
            "NEAR": _make_cb_bar("NEAR", 100.0, maturity_date="20260601"),   # ~2y
            "FAR": _make_cb_bar("FAR", 100.0, maturity_date="20300601"),     # ~6y
        }
        candidates = s._filter_candidates(bars, ctx)
        symbols = [c.symbol for c in candidates]
        assert "NEAR" in symbols
        assert "FAR" not in symbols

    def test_filter_excludes_near_maturity(self):
        """到期日太近也应被排除"""
        s = DefensiveValueStrategy(DefensiveValueConfig(
            min_years_to_maturity_val=0.5, min_days_to_maturity=30,
        ))
        s.on_start()
        ctx = _make_context()
        bars = {
            "OK": _make_cb_bar("OK", 100.0, maturity_date="20260601"),       # ~2y
            "TOO_NEAR": _make_cb_bar("TOO_NEAR", 100.0, maturity_date="20240601"),  # ~2mo
        }
        candidates = s._filter_candidates(bars, ctx)
        symbols = [c.symbol for c in candidates]
        assert "OK" in symbols
        assert "TOO_NEAR" not in symbols

    def test_score_prefers_high_ytm(self):
        """YTM 更高（价格更低于面值）的应排名在前"""
        s = DefensiveValueStrategy(DefensiveValueConfig(
            top_k=5, ytm_weight=1.0, premium_weight=0.0, maturity_weight=0.0,
        ))
        s.on_start()
        ctx = _make_context()
        candidates = [
            CandidateInfo(symbol="CHEAP", close=85.0, metadata={},
                          cb_over_rate=10.0, maturity_date="20270101",
                          issue_size=50000.0),
            CandidateInfo(symbol="EXPENSIVE", close=110.0, metadata={},
                          cb_over_rate=10.0, maturity_date="20270101",
                          issue_size=50000.0),
        ]
        ranked = s._score_candidates(candidates, ctx)
        assert ranked[0] == "CHEAP"

    def test_score_prefers_low_premium(self):
        """溢价率更低的应排名在前（纯溢价权重时）"""
        s = DefensiveValueStrategy(DefensiveValueConfig(
            top_k=5, ytm_weight=0.0, premium_weight=1.0, maturity_weight=0.0,
        ))
        s.on_start()
        ctx = _make_context()
        candidates = [
            CandidateInfo(symbol="LOW", close=100.0, metadata={},
                          cb_over_rate=5.0, maturity_date="20270101",
                          issue_size=50000.0),
            CandidateInfo(symbol="HIGH", close=100.0, metadata={},
                          cb_over_rate=30.0, maturity_date="20270101",
                          issue_size=50000.0),
        ]
        ranked = s._score_candidates(candidates, ctx)
        assert ranked[0] == "LOW"

    def test_score_prefers_near_maturity(self):
        """到期更近的应排名在前（纯到期权重时）"""
        s = DefensiveValueStrategy(DefensiveValueConfig(
            top_k=5, ytm_weight=0.0, premium_weight=0.0, maturity_weight=1.0,
        ))
        s.on_start()
        ctx = _make_context()
        candidates = [
            CandidateInfo(symbol="NEAR", close=100.0, metadata={},
                          cb_over_rate=10.0, maturity_date="20250601",
                          issue_size=50000.0),
            CandidateInfo(symbol="FAR", close=100.0, metadata={},
                          cb_over_rate=10.0, maturity_date="20290101",
                          issue_size=50000.0),
        ]
        ranked = s._score_candidates(candidates, ctx)
        assert ranked[0] == "NEAR"

    def test_score_balanced(self):
        """综合因子下，多维平衡选出最优者"""
        s = DefensiveValueStrategy(DefensiveValueConfig(top_k=2))
        s.on_start()
        ctx = _make_context()
        candidates = [
            # 完美标的：低价 + 低溢价 + 近到期
            CandidateInfo(symbol="BEST", close=90.0, metadata={},
                          cb_over_rate=5.0, maturity_date="20260101",
                          issue_size=50000.0),
            # 一般：高价 + 高溢价 + 远到期
            CandidateInfo(symbol="WORST", close=112.0, metadata={},
                          cb_over_rate=35.0, maturity_date="20290101",
                          issue_size=50000.0),
            # 中等：低价但远到期
            CandidateInfo(symbol="MID", close=92.0, metadata={},
                          cb_over_rate=20.0, maturity_date="20280101",
                          issue_size=50000.0),
        ]
        ranked = s._score_candidates(candidates, ctx)
        assert ranked[0] == "BEST"
        assert len(ranked) == 2

    def test_on_bar_generates_buys(self):
        """首次调仓应生成买入信号"""
        s = DefensiveValueStrategy(DefensiveValueConfig(
            top_k=2, rebalance_days=1, min_issue_size=30000.0,
            min_turnover=10.0, min_days_to_maturity=30,
        ))
        s.on_start()
        ctx = _make_context(cash=200_000.0)
        bars = {
            "A": _make_cb_bar("A", 95.0, amount=200, issue_size=50000.0,
                              cb_over_rate=10.0, maturity_date="20270101"),
            "B": _make_cb_bar("B", 100.0, amount=200, issue_size=40000.0,
                              cb_over_rate=15.0, maturity_date="20260601"),
        }
        intents = s.on_bar(bars, ctx)
        buy_syms = [i.symbol for i in intents if i.side == Side.BUY]
        assert len(buy_syms) >= 1

    def test_empty_candidates(self):
        """无候选标的时不应报错"""
        s = DefensiveValueStrategy(DefensiveValueConfig(
            min_issue_size=999999.0,   # 基本无标的满足
        ))
        s.on_start()
        ctx = _make_context()
        bars = {
            "A": _make_cb_bar("A", 100.0, issue_size=50000.0),
        }
        intents = s.on_bar(bars, ctx)
        assert isinstance(intents, list)
