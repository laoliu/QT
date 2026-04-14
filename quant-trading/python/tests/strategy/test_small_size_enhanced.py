"""
增强型小规模可转债轮动策略测试

测试覆盖：
- SmallSizeEnhancedConfig 初始化/自定义
- SmallSizeEnhancedStrategy 构造
- _extra_filter: max_premium 过滤
- _score_candidates: 3因子 min-max 归一化打分
- 边界条件/空数据
"""

from datetime import datetime

import pytest

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.small_size_enhanced import (
    SmallSizeEnhancedConfig,
    SmallSizeEnhancedStrategy,
)


def _make_context(dt=None, positions=None, cash=1_000_000.0, nav=1_000_000.0):
    return StrategyContext(
        current_time=dt or datetime(2024, 6, 1),
        positions=positions or {},
        cash=cash,
        nav=nav,
        metadata={},
    )


def _make_cb_candidate(
    symbol="123456.SZ",
    close=105.0,
    issue_size=10_000.0,
    cb_over_rate=15.0,
    amount=500.0,
    **extra_meta,
):
    meta = {
        "cb_over_rate": cb_over_rate,
        **extra_meta,
    }
    return CandidateInfo(
        symbol=symbol,
        close=close,
        metadata=meta,
        amount=amount,
        issue_size=issue_size,
        cb_over_rate=cb_over_rate,
        maturity_date="20301231",
        list_date="20220101",
        stk_short_name="测试转债",
        rating="AA",
    )


# ============================================================
# Config
# ============================================================


class TestSmallSizeEnhancedConfig:
    def test_default_values(self):
        cfg = SmallSizeEnhancedConfig()
        assert cfg.top_k == 20
        assert cfg.rebalance_days == 1
        assert cfg.size_weight == 0.50
        assert cfg.premium_weight == 0.30
        assert cfg.momentum_weight == 0.20
        assert cfg.max_premium == 50.0
        assert cfg.max_issue_size == 30_000.0
        assert cfg.min_issue_size == 3_000.0

    def test_custom_values(self):
        cfg = SmallSizeEnhancedConfig(
            top_k=10,
            size_weight=0.6,
            premium_weight=0.2,
            momentum_weight=0.2,
            max_premium=30.0,
        )
        assert cfg.top_k == 10
        assert cfg.size_weight == 0.6
        assert cfg.max_premium == 30.0

    def test_weights_sum_to_one(self):
        cfg = SmallSizeEnhancedConfig()
        total = cfg.size_weight + cfg.premium_weight + cfg.momentum_weight
        assert abs(total - 1.0) < 1e-9


# ============================================================
# Strategy Construction
# ============================================================


class TestSmallSizeEnhancedConstruction:
    def test_default_construction(self):
        s = SmallSizeEnhancedStrategy()
        assert s.name == "small_size_enhanced"
        assert isinstance(s.config, SmallSizeEnhancedConfig)

    def test_construction_with_config(self):
        cfg = SmallSizeEnhancedConfig(top_k=5, max_premium=25.0)
        s = SmallSizeEnhancedStrategy(config=cfg)
        assert s.config.top_k == 5
        assert s.config.max_premium == 25.0

    def test_construction_with_kwargs(self):
        s = SmallSizeEnhancedStrategy(top_k=8, max_premium=35.0)
        assert s.config.top_k == 8
        assert s.config.max_premium == 35.0


# ============================================================
# _extra_filter
# ============================================================


class TestSmallSizeEnhancedExtraFilter:
    def setup_method(self):
        self.strategy = SmallSizeEnhancedStrategy(
            config=SmallSizeEnhancedConfig(max_premium=50.0)
        )
        self.ctx = _make_context()

    def test_accept_below_max_premium(self):
        c = _make_cb_candidate(cb_over_rate=30.0)
        assert self.strategy._extra_filter(c, self.ctx) is True

    def test_reject_above_max_premium(self):
        c = _make_cb_candidate(cb_over_rate=60.0)
        assert self.strategy._extra_filter(c, self.ctx) is False

    def test_borderline_exact_premium(self):
        c = _make_cb_candidate(cb_over_rate=50.0)
        result = self.strategy._extra_filter(c, self.ctx)
        assert isinstance(result, bool)

    def test_negative_premium(self):
        """折价（负溢价率）应通过"""
        c = _make_cb_candidate(cb_over_rate=-5.0)
        assert self.strategy._extra_filter(c, self.ctx) is True

    def test_zero_premium(self):
        c = _make_cb_candidate(cb_over_rate=0.0)
        assert self.strategy._extra_filter(c, self.ctx) is True

    def test_none_premium_uses_default(self):
        """cb_over_rate=None 时走默认路径"""
        c = CandidateInfo(
            symbol="123456.SZ",
            close=100.0,
            metadata={},
            issue_size=10_000.0,
            cb_over_rate=None,
        )
        result = self.strategy._extra_filter(c, self.ctx)
        assert isinstance(result, bool)


# ============================================================
# _score_candidates
# ============================================================


class TestSmallSizeEnhancedScoring:
    def setup_method(self):
        self.strategy = SmallSizeEnhancedStrategy(
            config=SmallSizeEnhancedConfig(
                top_k=3,
                buffer_k=0,
                max_premium=100.0,
                min_issue_size=0.0,
            )
        )
        self.strategy.on_start()
        self.ctx = _make_context()

    def test_returns_list_of_symbols(self):
        candidates = [
            _make_cb_candidate("A", close=100, issue_size=5000, cb_over_rate=10),
            _make_cb_candidate("B", close=105, issue_size=8000, cb_over_rate=20),
            _make_cb_candidate("C", close=110, issue_size=12000, cb_over_rate=30),
        ]
        result = self.strategy._score_candidates(candidates, self.ctx)
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)

    def test_smaller_size_ranks_better(self):
        """小规模发行应排前面（ascending 排序）"""
        candidates = [
            _make_cb_candidate("BIG", close=100, issue_size=50_000, cb_over_rate=10),
            _make_cb_candidate("SMALL", close=100, issue_size=3_000, cb_over_rate=10),
        ]
        result = self.strategy._score_candidates(candidates, self.ctx)
        assert result[0] == "SMALL"

    def test_lower_premium_ranks_better(self):
        """低溢价率更优"""
        candidates = [
            _make_cb_candidate("HIGH_P", close=100, issue_size=10_000, cb_over_rate=40),
            _make_cb_candidate("LOW_P", close=100, issue_size=10_000, cb_over_rate=5),
        ]
        result = self.strategy._score_candidates(candidates, self.ctx)
        assert result[0] == "LOW_P"

    def test_lower_price_ranks_better(self):
        """低价格更优（momentum_weight 作用于 price）"""
        # 设置 momentum_weight 较大以突出 close 的影响
        strategy = SmallSizeEnhancedStrategy(
            config=SmallSizeEnhancedConfig(
                top_k=3,
                buffer_k=0,
                size_weight=0.0,
                premium_weight=0.0,
                momentum_weight=1.0,
                max_premium=100.0,
                min_issue_size=0.0,
            )
        )
        strategy.on_start()
        candidates = [
            _make_cb_candidate("EXPENSIVE", close=130, issue_size=10_000, cb_over_rate=10),
            _make_cb_candidate("CHEAP", close=90, issue_size=10_000, cb_over_rate=10),
        ]
        result = strategy._score_candidates(candidates, self.ctx)
        assert result[0] == "CHEAP"

    def test_empty_candidates(self):
        result = self.strategy._score_candidates([], self.ctx)
        assert result == []

    def test_single_candidate(self):
        candidates = [
            _make_cb_candidate("ONLY", close=100, issue_size=10_000, cb_over_rate=10),
        ]
        result = self.strategy._score_candidates(candidates, self.ctx)
        assert result == ["ONLY"]

    def test_identical_candidates(self):
        """完全相同的候选应全部返回"""
        candidates = [
            _make_cb_candidate(f"S{i}", close=100, issue_size=10_000, cb_over_rate=10)
            for i in range(5)
        ]
        result = self.strategy._score_candidates(candidates, self.ctx)
        assert len(result) == min(5, self.strategy.config.top_k + self.strategy.config.buffer_k)

    def test_topk_limits_output(self):
        """输出不超过 top_k + buffer_k"""
        strategy = SmallSizeEnhancedStrategy(
            config=SmallSizeEnhancedConfig(
                top_k=2,
                buffer_k=1,
                min_issue_size=0.0,
                max_premium=100.0,
            )
        )
        strategy.on_start()
        candidates = [
            _make_cb_candidate(f"S{i}", close=100 + i, issue_size=5000 + i * 1000, cb_over_rate=10 + i)
            for i in range(10)
        ]
        result = strategy._score_candidates(candidates, self.ctx)
        assert len(result) <= 3  # top_k(2) + buffer_k(1)
