"""Tests for NewBondStrategy."""
from datetime import datetime, timedelta

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.new_bond import NewBondConfig, NewBondStrategy


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


class TestNewBondConfig:
    def test_defaults(self):
        cfg = NewBondConfig()
        assert cfg.top_k == 15
        assert cfg.max_age_days == 90
        assert cfg.min_age_days == 5


class TestNewBondStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = NewBondConfig(
            top_k=2, rebalance_days=1, max_age_days=90,
            min_age_days=3, min_turnover=10.0, lot_size=10,
        )
        s = NewBondStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "new_bond" in StrategyRegistry().list_strategies()

    def test_extra_filter_pass(self, strategy):
        # 10 days ago → within [3, 90]
        list_date = (datetime(2024, 3, 15) - timedelta(days=10)).strftime("%Y%m%d")
        bar = _make_cb_bar("A.SZ", close=100.0, list_date=list_date)
        ci = CandidateInfo(symbol="A.SZ", close=100.0, metadata=bar.metadata, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, list_date=list_date)
        assert strategy._extra_filter(ci, _make_context()) is True

    def test_extra_filter_fail(self, strategy):
        # 200 days ago → outside max_age_days
        list_date = (datetime(2024, 3, 15) - timedelta(days=200)).strftime("%Y%m%d")
        bar = _make_cb_bar("B.SZ", close=100.0, list_date=list_date)
        ci = CandidateInfo(symbol="B.SZ", close=100.0, metadata=bar.metadata, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, list_date=list_date)
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_score_candidates(self, strategy):
        candidates = [
            CandidateInfo(symbol="A.SZ", close=100.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="B.SZ", close=95.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="C.SZ", close=110.0, metadata={"issue_size": 5000.0}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[:2] == ["B.SZ", "A.SZ"]

    def test_stop_loss(self, strategy):
        bar = _make_cb_bar("X.SZ", close=80.0)
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

    def test_filter_too_young(self, strategy):
        """上市天数 < min_age_days → 被过滤"""
        list_date = (datetime(2024, 3, 15) - timedelta(days=1)).strftime("%Y%m%d")
        ci = CandidateInfo(symbol="YOUNG.SZ", close=100.0, metadata={"list_date": list_date},
                           issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, list_date=list_date)
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_filter_no_list_date(self, strategy):
        """list_date 为空 → _extra_filter 返回 False"""
        ci = CandidateInfo(symbol="NODATE.SZ", close=100.0, metadata={},
                           issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, list_date="")
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_adaptive_fallback_widens_age(self):
        """候选不足时 _relax_filters_one_step 放宽 max_age"""
        cfg = NewBondConfig(top_k=5, rebalance_days=1, max_age_days=30,
                            fallback_max_age_days=180, lot_size=10, min_turnover=10.0)
        s = NewBondStrategy(cfg)
        s.on_start()
        changed = s._relax_filters_one_step()
        assert changed is True
        assert s._effective_max_age > 30  # 放宽了

    def test_adaptive_fallback_restores(self):
        """候选充裕时 _maybe_tighten_filters 恢复 max_age"""
        cfg = NewBondConfig(top_k=2, rebalance_days=1, max_age_days=30,
                            fallback_max_age_days=180, lot_size=10, min_turnover=10.0)
        s = NewBondStrategy(cfg)
        s.on_start()
        s._effective_max_age = 60  # 模拟已放宽
        s._maybe_tighten_filters()
        assert s._effective_max_age < 60  # 已收紧

    def test_top_k_limits_output(self, strategy):
        """top_k 限制输出数量"""
        candidates = [
            CandidateInfo(symbol=f"S{i}.SZ", close=float(90 + i), metadata={}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0)
            for i in range(10)
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert len(scored) == 2  # top_k=2
