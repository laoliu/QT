"""双低轮动策略测试"""
from datetime import datetime
from decimal import Decimal

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.double_low import DoubleLowConfig, DoubleLowMetrics, DoubleLowStrategy
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry


class TestDoubleLowMetrics:
    """双低指标测试"""

    def test_calculate_double_low(self):
        """测试双低指标计算"""
        metrics = DoubleLowMetrics.from_bar_and_premium(
            ts_code="127075.SZ",
            bond_price=120.0,
            premium_rate=10.0,
        )

        assert metrics.double_low == 130.0  # 120 + 10

    def test_negative_premium(self):
        """测试负溢价率"""
        metrics = DoubleLowMetrics.from_bar_and_premium(
            ts_code="127075.SZ",
            bond_price=100.0,
            premium_rate=-5.0,  # 折价
        )

        assert metrics.double_low == 95.0  # 100 + (-5)


class TestDoubleLowStrategy:
    """双低轮动策略测试"""

    @pytest.fixture
    def strategy(self):
        """创建策略实例"""
        return DoubleLowStrategy(
            config=DoubleLowConfig(
                top_k=3,
                rebalance_days=5,
                max_price=130.0,
                max_premium=50.0,
                lot_size=10,
            ),
        )

    def test_strategy_registration(self):
        """测试策略注册"""
        registry = StrategyRegistry()
        assert "double_low" in registry.list_strategies()

    def test_score_candidates_ranking(self, strategy):
        """测试 _score_candidates 按双低指标排序"""
        context = StrategyContext(
            current_time=datetime(2024, 1, 15, 15, 0),
            positions={},
            cash=100000.0,
            nav=100000.0,
        )
        candidates = [
            CandidateInfo(symbol="128888.SZ", close=125.0, metadata={}, cb_over_rate=20.0),  # DL=145
            CandidateInfo(symbol="113001.SH", close=100.0, metadata={}, cb_over_rate=5.0),   # DL=105
            CandidateInfo(symbol="123456.SZ", close=110.0, metadata={}, cb_over_rate=8.0),   # DL=118
            CandidateInfo(symbol="127075.SZ", close=105.0, metadata={}, cb_over_rate=15.0),  # DL=120
        ]
        ranked = strategy._score_candidates(candidates, context)
        assert len(ranked) == 3  # top_k=3
        assert ranked[0] == "113001.SH"  # DL=105, lowest
        assert ranked[1] == "123456.SZ"  # DL=118
        assert ranked[2] == "127075.SZ"  # DL=120
        assert "128888.SZ" not in ranked  # DL=145, excluded

    def test_first_rebalance(self, strategy):
        """测试首次轮动（bar 1 触发调仓）"""
        strategy.on_start()
        context = StrategyContext(
            current_time=datetime(2024, 1, 15, 15, 0),
            positions={},
            cash=100000.0,
            nav=100000.0,
        )

        bars = {
            "113001.SH": Bar(
                symbol="113001.SH",
                start_ts=datetime(2024, 1, 15, 9, 30),
                end_ts=datetime(2024, 1, 15, 15, 0),
                open=100.0, high=102.0, low=99.0, close=101.0,
                volume=10000,
                metadata={"cb_over_rate": 5.0, "amount": 100.0,
                          "issue_size": 5000.0, "maturity_date": "20280101"},
            ),
            "123456.SZ": Bar(
                symbol="123456.SZ",
                start_ts=datetime(2024, 1, 15, 9, 30),
                end_ts=datetime(2024, 1, 15, 15, 0),
                open=110.0, high=112.0, low=109.0, close=111.0,
                volume=8000,
                metadata={"cb_over_rate": 8.0, "amount": 80.0,
                          "issue_size": 3000.0, "maturity_date": "20280101"},
            ),
        }

        intents = strategy.on_bar(bars, context)

        # 首次轮动应生成买入指令
        buy_symbols = {i.symbol for i in intents if i.side == Side.BUY}
        assert len(buy_symbols) > 0

    def test_rebalance_skip_within_period(self, strategy):
        """测试轮动周期内跳过（基于 bar 计数）"""
        # bar_count=1 时应轮动（首次）
        strategy._bar_count = 1
        assert strategy._should_rebalance() is True

        # bar_count=2 时不应轮动（rebalance_days=5，下次应在 bar 6）
        strategy._bar_count = 2
        assert strategy._should_rebalance() is False

        # bar_count=6 时应轮动 (6-1)%5==0
        strategy._bar_count = 6
        assert strategy._should_rebalance() is True


class TestConversionArbitrageStrategy:
    """转股套利策略测试"""

    def test_strategy_registration(self):
        """测试策略注册"""
        registry = StrategyRegistry()
        assert "conversion_arbitrage" in registry.list_strategies()


class TestDoubleLowEdgeCases:
    """双低策略边界测试"""

    @pytest.fixture
    def strategy(self):
        return DoubleLowStrategy(
            config=DoubleLowConfig(
                top_k=3, rebalance_days=5, max_price=130.0,
                max_premium=50.0, lot_size=10
            )
        )

    def _ctx(self):
        return StrategyContext(
            current_time=datetime(2024, 1, 15, 15, 0),
            positions={}, cash=100000.0, nav=100000.0,
        )

    def test_extra_filter_pass(self, strategy):
        """溢价率低于上限时应通过"""
        ci = CandidateInfo(
            symbol="A.SZ", close=100.0, metadata={},
            cb_over_rate=30.0, cb_value=100.0,
        )
        assert strategy._extra_filter(ci, self._ctx()) is True

    def test_extra_filter_fail_high_premium(self, strategy):
        """溢价率超过上限时应被过滤"""
        ci = CandidateInfo(
            symbol="B.SZ", close=100.0, metadata={},
            cb_over_rate=60.0, cb_value=100.0,
        )
        assert strategy._extra_filter(ci, self._ctx()) is False

    def test_extra_filter_fail_none(self, strategy):
        """溢价率为None时应被过滤"""
        ci = CandidateInfo(
            symbol="C.SZ", close=100.0, metadata={},
            cb_over_rate=None, cb_value=100.0,
        )
        assert strategy._extra_filter(ci, self._ctx()) is False

    def test_score_with_negative_premium(self, strategy):
        """负溢价率应产生更低的双低值（排名靠前）"""
        candidates = [
            CandidateInfo(symbol="POS.SZ", close=110.0, metadata={}, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="NEG.SZ", close=100.0, metadata={}, cb_over_rate=-5.0, cb_value=100.0),
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        assert ranked[0] == "NEG.SZ"  # 100 + (-5) = 95 < 110 + 10 = 120

    def test_fewer_candidates_than_top_k(self, strategy):
        """候选数 < top_k 时应返回全部"""
        candidates = [
            CandidateInfo(symbol="ONLY.SZ", close=100.0, metadata={}, cb_over_rate=5.0, cb_value=100.0),
        ]
        ranked = strategy._score_candidates(candidates, self._ctx())
        assert ranked == ["ONLY.SZ"]

    def test_score_does_not_mutate_metadata(self, strategy):
        """_score_candidates 不应修改 candidate.metadata（C-2 副作用消除）"""
        candidates = [
            CandidateInfo(symbol="A.SZ", close=110.0, metadata={"keep": 1}, cb_over_rate=10.0, cb_value=100.0),
            CandidateInfo(symbol="B.SZ", close=100.0, metadata={}, cb_over_rate=5.0, cb_value=100.0),
        ]
        strategy._score_candidates(candidates, self._ctx())
        # metadata 不应被注入 _double_low 键
        assert "_double_low" not in candidates[0].metadata
        assert "_double_low" not in candidates[1].metadata
        # 原有字段未被破坏
        assert candidates[0].metadata == {"keep": 1}

    def test_score_does_not_reorder_input_list(self, strategy):
        """_score_candidates 不应改变传入列表的元素顺序（无 in-place sort 副作用）"""
        c1 = CandidateInfo(symbol="HIGH.SZ", close=130.0, metadata={}, cb_over_rate=20.0, cb_value=100.0)
        c2 = CandidateInfo(symbol="LOW.SZ", close=90.0, metadata={}, cb_over_rate=2.0, cb_value=100.0)
        candidates = [c1, c2]
        strategy._score_candidates(candidates, self._ctx())
        # 原列表顺序不变
        assert candidates[0] is c1
        assert candidates[1] is c2
