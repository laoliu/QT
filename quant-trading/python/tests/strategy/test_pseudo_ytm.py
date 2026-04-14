"""Tests for PseudoYTMStrategy."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.pseudo_ytm import PseudoYTMConfig, PseudoYTMStrategy


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


class TestPseudoYTMConfig:
    def test_defaults(self):
        cfg = PseudoYTMConfig()
        assert cfg.top_k == 15
        assert cfg.face_value == 100.0
        assert cfg.min_years_to_maturity == 0.5


class TestPseudoYTMStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = PseudoYTMConfig(
            top_k=2, rebalance_days=1, min_turnover=10.0, lot_size=10,
        )
        s = PseudoYTMStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        assert "pseudo_ytm" in StrategyRegistry().list_strategies()

    def test_extra_filter_pass(self, strategy):
        ci = CandidateInfo(symbol="A.SZ", close=95.0, metadata={"issue_size": 5000.0, "maturity_date": "20280101"}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, maturity_date="20280101")
        assert strategy._extra_filter(ci, _make_context()) is True

    def test_extra_filter_fail(self, strategy):
        ci = CandidateInfo(symbol="B.SZ", close=110.0, metadata={"issue_size": 5000.0, "maturity_date": "20280101"}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, maturity_date="20280101")
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_score_candidates(self, strategy):
        candidates = [
            CandidateInfo(symbol="A.SZ", close=95.0, metadata={"issue_size": 5000.0, "maturity_date": "20280101"}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, maturity_date="20280101"),
            CandidateInfo(symbol="B.SZ", close=98.0, metadata={"issue_size": 5000.0, "maturity_date": "20280101"}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, maturity_date="20280101"),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        # 95 has higher pseudo-YTM → ranks first
        assert scored[0] == "A.SZ"

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

    def test_filter_no_maturity_date(self, strategy):
        """maturity_date 为空 → _extra_filter 返回 False"""
        ci = CandidateInfo(symbol="NOMAT.SZ", close=95.0, metadata={},
                           issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, maturity_date="")
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_filter_too_close_to_maturity(self, strategy):
        """到期不足 min_years_to_maturity → 被过滤"""
        # 2024-03-15 + 30天 = 到期不足0.5年
        near_maturity = "20240415"
        ci = CandidateInfo(symbol="NEAR.SZ", close=95.0, metadata={"maturity_date": near_maturity},
                           issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, maturity_date=near_maturity)
        assert strategy._extra_filter(ci, _make_context()) is False

    def test_ytm_ranking_multiple(self, strategy):
        """多只 YTM 排序正确"""
        # A: close=90, years≈4 → ytm=(100-90)/90/4*100≈2.78%
        # B: close=95, years≈4 → ytm=(100-95)/95/4*100≈1.32%
        # C: close=85, years≈4 → ytm=(100-85)/85/4*100≈4.41%
        candidates = [
            CandidateInfo(symbol="A.SZ", close=90.0, metadata={"maturity_date": "20280101"}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, maturity_date="20280101"),
            CandidateInfo(symbol="B.SZ", close=95.0, metadata={"maturity_date": "20280101"}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, maturity_date="20280101"),
            CandidateInfo(symbol="C.SZ", close=85.0, metadata={"maturity_date": "20280101"}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, maturity_date="20280101"),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored[0] == "C.SZ"  # YTM 最高

    def test_years_to_maturity_zero_skipped(self, strategy):
        """到期年限 ≤ 0 的候选在 _score_candidates 中被跳过"""
        # 已过期的到期日
        candidates = [
            CandidateInfo(symbol="EXP.SZ", close=95.0, metadata={"maturity_date": "20230101"}, issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, maturity_date="20230101"),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored == []  # years ≈ 0 或已过期 → skip

    def test_config_override_face_value(self):
        """自定义 face_value 后过滤阈值变化"""
        cfg = PseudoYTMConfig(face_value=105.0, top_k=2, rebalance_days=1, lot_size=10)
        s = PseudoYTMStrategy(cfg)
        s.on_start()
        # close=102 < face_value=105 → 通过
        ci = CandidateInfo(symbol="A.SZ", close=102.0, metadata={"maturity_date": "20280101"},
                           issue_size=5000.0, cb_over_rate=10.0, cb_value=100.0, maturity_date="20280101")
        assert s._extra_filter(ci, _make_context()) is True
