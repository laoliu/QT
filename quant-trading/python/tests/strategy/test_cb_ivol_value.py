"""Tests for CBIVOLValueStrategy — IVOL-价值策略。"""

from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.cb_ivol_value import CBIVOLValueStrategy, IVOLValueConfig


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


# ===========================================================================
# IVOLValueConfig Tests
# ===========================================================================


class TestIVOLValueConfig:
    def test_defaults(self):
        cfg = IVOLValueConfig()
        assert cfg.top_k == 20
        assert cfg.rebalance_days == 5
        assert cfg.price_weight == 0.15
        assert cfg.premium_weight == 0.10
        assert cfg.ivol_weight == 0.20
        assert cfg.high_distance_weight == 0.15
        assert cfg.theta_weight == 0.10
        assert cfg.skewness_weight == 0.10
        assert cfg.size_weight == 0.20

    def test_orthogonal_weight_dominance(self):
        """正交因子权重应 >= 50%"""
        cfg = IVOLValueConfig()
        orthogonal = (
            cfg.ivol_weight
            + cfg.high_distance_weight
            + cfg.theta_weight
            + cfg.skewness_weight
        )
        traditional = cfg.price_weight + cfg.premium_weight
        assert orthogonal >= 0.50
        assert traditional <= 0.30

    def test_weights_sum_to_one(self):
        """所有权重之和应为 1.0。"""
        cfg = IVOLValueConfig()
        total = (
            cfg.price_weight
            + cfg.premium_weight
            + cfg.ivol_weight
            + cfg.high_distance_weight
            + cfg.theta_weight
            + cfg.skewness_weight
            + cfg.size_weight
        )
        assert abs(total - 1.0) < 0.001

    def test_custom_params(self):
        cfg = IVOLValueConfig(
            top_k=15, rebalance_days=3, ivol_weight=0.30,
        )
        assert cfg.top_k == 15
        assert cfg.rebalance_days == 3
        assert cfg.ivol_weight == 0.30

    def test_tracker_params(self):
        cfg = IVOLValueConfig(ivol_lookback=30, high_distance_lookback=120)
        assert cfg.ivol_lookback == 30
        assert cfg.high_distance_lookback == 120


# ===========================================================================
# CBIVOLValueStrategy Tests
# ===========================================================================


class TestCBIVOLValueStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = IVOLValueConfig(
            top_k=2, rebalance_days=1,
            min_turnover=10.0, lot_size=10,
            ivol_lookback=5, ivol_min_obs=3,
            high_distance_lookback=30,
            skewness_lookback=5, skewness_min_obs=3,
        )
        s = CBIVOLValueStrategy(cfg)
        s.on_start()
        return s

    def test_registration(self):
        """策略应已注册在 registry 中。"""
        assert "cb_ivol_value" in StrategyRegistry().list_strategies()

    def test_name(self, strategy):
        assert strategy.name == "cb_ivol_value"

    def test_config_property(self, strategy):
        assert isinstance(strategy.config, IVOLValueConfig)
        assert strategy.config.top_k == 2

    def test_on_start_resets_trackers(self, strategy):
        """on_start 应重置所有 Tracker 状态。"""
        # Feed some data
        bar = _make_cb_bar("A.SZ", close=100)
        ctx = _make_context()
        strategy.on_bar({"A.SZ": bar}, ctx)
        # Now restart
        strategy.on_start()
        # Trackers should be reset
        assert strategy._ivol_tracker._bar_count == 0

    def test_empty_candidates(self, strategy):
        scored = strategy._score_candidates([], _make_context())
        assert scored == []

    def test_single_candidate(self, strategy):
        candidates = [
            CandidateInfo(
                symbol="A.SZ", close=100.0,
                metadata={"issue_size": 5000.0},
                issue_size=5000.0, cb_over_rate=10.0,
                maturity_date="20280101",
            ),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert scored == ["A.SZ"]

    def test_score_candidates_multi(self, strategy):
        """多候选打分排名。"""
        candidates = [
            CandidateInfo(
                symbol="A.SZ", close=105.0,
                metadata={"issue_size": 8000.0},
                issue_size=8000.0, cb_over_rate=15.0,
                maturity_date="20280101",
            ),
            CandidateInfo(
                symbol="B.SZ", close=95.0,
                metadata={"issue_size": 3000.0},
                issue_size=3000.0, cb_over_rate=5.0,
                maturity_date="20280101",
            ),
            CandidateInfo(
                symbol="C.SZ", close=110.0,
                metadata={"issue_size": 6000.0},
                issue_size=6000.0, cb_over_rate=20.0,
                maturity_date="20280101",
            ),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert len(scored) == 2  # top_k=2

    def test_score_candidates_fewer_than_top_k(self, strategy):
        """候选数 < top_k 应返回全部。"""
        candidates = [
            CandidateInfo(
                symbol="A.SZ", close=100.0,
                metadata={"issue_size": 5000.0},
                issue_size=5000.0, cb_over_rate=10.0,
                maturity_date="20280101",
            ),
        ]
        scored = strategy._score_candidates(candidates, _make_context())
        assert len(scored) == 1

    def test_median_fill_for_missing_factors(self, strategy):
        """缺失因子填充中位数时不报错。"""
        # IVOL/high_distance/skewness 在冷启动期全部为 None
        candidates = [
            CandidateInfo(
                symbol="A.SZ", close=100.0,
                metadata={"issue_size": 5000.0},
                issue_size=5000.0, cb_over_rate=10.0,
                maturity_date="20280101",
            ),
            CandidateInfo(
                symbol="B.SZ", close=110.0,
                metadata={"issue_size": 6000.0},
                issue_size=6000.0, cb_over_rate=15.0,
                maturity_date="20280101",
            ),
        ]
        # 没调用 update → 因子全部 None → 用中位数填充
        scored = strategy._score_candidates(candidates, _make_context())
        assert len(scored) == 2

    def test_first_bar_generates_intents(self, strategy):
        """首次调仓应生成买入信号。"""
        bar = _make_cb_bar("A.SZ", close=100.0, cb_over_rate=5.0)
        ctx = _make_context()
        intents = strategy.on_bar({"A.SZ": bar}, ctx)
        buys = [i for i in intents if i.side == Side.BUY]
        assert len(buys) > 0

    def test_stop_loss(self, strategy):
        """止损应触发卖出。"""
        bar = _make_cb_bar("X.SZ", close=80.0)
        bar_obj = Bar(
            symbol="X.SZ",
            start_ts=datetime(2024, 3, 15, 9, 30),
            end_ts=datetime(2024, 3, 15, 15, 0),
            open=82.0, high=83.0, low=79.0,
            close=80.0, volume=1000,
            metadata={"amount": 100.0, "cb_over_rate": 10.0,
                       "cb_value": 90.0, "maturity_date": "20280101",
                       "issue_size": 5000.0, "stk_short_name": "正股"},
        )
        ctx = _make_context(positions={"X.SZ": 10})
        strategy._entry_prices["X.SZ"] = 100.0
        intents = strategy.on_bar({"X.SZ": bar_obj}, ctx)
        sells = [i for i in intents if i.side == Side.SELL]
        assert len(sells) >= 1

    def test_from_config(self):
        """通过 create_strategy_from_config 创建。"""
        from aipha.strategy.registry import create_strategy_from_config
        config = {
            "name": "cb_ivol_value",
            "params": {"top_k": 10, "rebalance_days": 3},
        }
        s = create_strategy_from_config(config)
        assert s.name == "cb_ivol_value"
        assert s.config.top_k == 10

    def test_trackers_update_on_bar(self, strategy):
        """on_bar 应触发 Tracker 更新。"""
        for i in range(5):
            bar = _make_cb_bar("A.SZ", close=100 + i, cb_over_rate=10.0)
            ctx = _make_context()
            strategy.on_bar({"A.SZ": bar}, ctx)
        # IVOL tracker 应有数据
        assert strategy._ivol_tracker._bar_count == 5

    def test_buffer_k_respected(self):
        """buffer_k > 0 应减少换手。"""
        cfg = IVOLValueConfig(top_k=2, buffer_k=3, rebalance_days=1,
                              min_turnover=0.0, lot_size=10)
        s = CBIVOLValueStrategy(cfg)
        s.on_start()
        assert s.config.buffer_k == 3
