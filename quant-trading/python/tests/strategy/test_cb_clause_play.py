"""Tests for CBClausePlayStrategy — 条款博弈可转债策略。

测试覆盖（≥12 用例 per §6.2）：
- 策略构造与配置
- 注册表集成
- _extra_filter: 溢价率上限 + 次新排除
- _score_candidates: 回售接近度 + 到期催化 + 价格 + 溢价率 排名
- on_bar 全流程
- 边界条件
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.cb_clause_play import (
    CBClausePlayStrategy,
    ClausePlayCBConfig,
)
from aipha.strategy.registry import StrategyRegistry


# ---------------------------------------------------------------------------
# Helpers
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
    ts_code: str,
    close: float,
    amount: float = 100.0,
    cb_over_rate: float = 10.0,
    cb_value: float = 70.0,
    maturity_date: str = "20260601",
    issue_size: float = 5000.0,
    stk_short_name: str = "正股",
    list_date: str = "20230101",
    conv_price: float | None = None,
    **extra: Any,
) -> Bar:
    meta = {
        "amount": amount,
        "cb_over_rate": cb_over_rate,
        "cb_value": cb_value,
        "maturity_date": maturity_date,
        "issue_size": issue_size,
        "stk_short_name": stk_short_name,
        "list_date": list_date,
        **extra,
    }
    if conv_price is not None:
        meta["conv_price"] = conv_price
    return _make_bar(ts_code, close, metadata=meta)


def _make_context(
    positions: dict[str, int] | None = None,
    cash: float = 500_000.0,
    current_time: datetime | None = None,
) -> StrategyContext:
    return StrategyContext(
        current_time=current_time or datetime(2024, 3, 15, 15, 0),
        positions=positions or {},
        cash=cash, nav=cash,
    )


def _make_candidate(
    symbol: str,
    close: float = 100.0,
    issue_size: float = 5000.0,
    cb_over_rate: float = 10.0,
    cb_value: float = 70.0,
    maturity_date: str = "20260601",
    list_date: str = "20230101",
    amount: float = 100.0,
    conv_price: float | None = None,
) -> CandidateInfo:
    meta: dict[str, Any] = {}
    if conv_price is not None:
        meta["conv_price"] = conv_price
    return CandidateInfo(
        symbol=symbol,
        close=close,
        metadata=meta,
        amount=amount,
        issue_size=issue_size,
        cb_over_rate=cb_over_rate,
        cb_value=cb_value,
        conv_price=conv_price,
        maturity_date=maturity_date,
        list_date=list_date,
        stk_short_name="正股",
    )


# ---------------------------------------------------------------------------
# 1. 构造与配置
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_default_config(self) -> None:
        s = CBClausePlayStrategy()
        cfg = s.config
        assert cfg.top_k == 15
        assert cfg.weight_putback == 0.30
        assert cfg.weight_maturity == 0.25
        assert cfg.weight_price == 0.20
        assert cfg.weight_premium == 0.15
        assert cfg.weight_issue_size == 0.10
        assert cfg.putback_trigger_ratio == 0.70
        assert cfg.max_issue_size == float("inf")

    def test_custom_config(self) -> None:
        cfg = ClausePlayCBConfig(
            top_k=10, weight_putback=0.40, putback_trigger_ratio=0.75,
        )
        s = CBClausePlayStrategy(config=cfg)
        assert s.config.top_k == 10
        assert s.config.weight_putback == 0.40
        assert s.config.putback_trigger_ratio == 0.75

    def test_kwargs_config(self) -> None:
        s = CBClausePlayStrategy(top_k=8, weight_maturity=0.35)
        assert s.config.top_k == 8
        assert s.config.weight_maturity == 0.35

    def test_weight_sum_approximately_1(self) -> None:
        cfg = ClausePlayCBConfig()
        total = (
            cfg.weight_price + cfg.weight_premium
            + cfg.weight_putback + cfg.weight_maturity
            + cfg.weight_issue_size
        )
        assert abs(total - 1.0) < 0.01


# ---------------------------------------------------------------------------
# 2. 注册表
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_registered(self) -> None:
        cls = StrategyRegistry().get("cb_clause_play")
        assert cls is not None
        assert cls is CBClausePlayStrategy


# ---------------------------------------------------------------------------
# 3. _extra_filter: 溢价率 + 次新
# ---------------------------------------------------------------------------

class TestExtraFilterPremium:
    def test_low_premium_passes(self) -> None:
        s = CBClausePlayStrategy()
        s.on_start()
        c = _make_candidate("CB001", cb_over_rate=10.0)
        ctx = _make_context()
        assert s._extra_filter(c, ctx) is True

    def test_high_premium_rejected(self) -> None:
        """溢价率 > max_premium → 过滤"""
        s = CBClausePlayStrategy(max_premium=30.0)
        s.on_start()
        c = _make_candidate("CB001", cb_over_rate=35.0)
        ctx = _make_context()
        assert s._extra_filter(c, ctx) is False

    def test_premium_at_limit_passes(self) -> None:
        s = CBClausePlayStrategy(max_premium=50.0)
        s.on_start()
        c = _make_candidate("CB001", cb_over_rate=50.0)
        ctx = _make_context()
        assert s._extra_filter(c, ctx) is True

    def test_none_premium_passes(self) -> None:
        s = CBClausePlayStrategy()
        s.on_start()
        c = _make_candidate("CB001", cb_over_rate=None)
        ctx = _make_context()
        assert s._extra_filter(c, ctx) is True


class TestExtraFilterAge:
    def test_old_bond_passes(self) -> None:
        s = CBClausePlayStrategy(min_age_days=30)
        s.on_start()
        c = _make_candidate("CB001", list_date="20230101")
        ctx = _make_context()  # 2024-03-15
        assert s._extra_filter(c, ctx) is True

    def test_new_bond_rejected(self) -> None:
        """上市不足 30 天 → 过滤"""
        s = CBClausePlayStrategy(min_age_days=30)
        s.on_start()
        c = _make_candidate("CB001", list_date="20240310")
        ctx = _make_context()  # 2024-03-15, 仅5天
        assert s._extra_filter(c, ctx) is False


# ---------------------------------------------------------------------------
# 4. _score_candidates: 排名加权
# ---------------------------------------------------------------------------

class TestScoreCandidates:
    def _make_strategy(self, **kwargs: Any) -> CBClausePlayStrategy:
        s = CBClausePlayStrategy(**kwargs)
        s.on_start()
        return s

    def test_low_parity_favored(self) -> None:
        """低 cb_value（接近回售触发线）→ 排名更高"""
        s = self._make_strategy(top_k=2)
        candidates = [
            _make_candidate("LOW", cb_value=55.0, close=90.0),    # parity=0.55, 接近0.70
            _make_candidate("MID", cb_value=80.0, close=100.0),
            _make_candidate("HIGH", cb_value=120.0, close=115.0),
        ]
        ctx = _make_context()
        selected = s._score_candidates(candidates, ctx)
        assert len(selected) == 2
        assert "LOW" in selected

    def test_optimal_maturity_favored(self) -> None:
        """2年到期（催化高峰）优于5年到期"""
        s = self._make_strategy(top_k=1, weight_maturity=1.0,
                                weight_price=0.0, weight_premium=0.0,
                                weight_putback=0.0, weight_issue_size=0.0)
        candidates = [
            _make_candidate("PEAK", maturity_date="20260315"),   # ~2年
            _make_candidate("FAR", maturity_date="20290315"),    # ~5年
            _make_candidate("NEAR", maturity_date="20240615"),   # ~3月
        ]
        ctx = _make_context()
        selected = s._score_candidates(candidates, ctx)
        assert selected == ["PEAK"]

    def test_putback_only_scoring(self) -> None:
        """纯回售因子权重：低 parity 的排名最高"""
        s = self._make_strategy(top_k=1, weight_putback=1.0,
                                weight_price=0.0, weight_premium=0.0,
                                weight_maturity=0.0, weight_issue_size=0.0)
        candidates = [
            _make_candidate("A", cb_value=50.0),   # parity=0.50 < 0.70
            _make_candidate("B", cb_value=80.0),   # parity=0.80 > 0.70
            _make_candidate("C", cb_value=150.0),  # parity=1.50 >> 0.70
        ]
        ctx = _make_context()
        selected = s._score_candidates(candidates, ctx)
        assert selected == ["A"]

    def test_empty_candidates(self) -> None:
        s = self._make_strategy()
        ctx = _make_context()
        assert s._score_candidates([], ctx) == []

    def test_single_candidate(self) -> None:
        s = self._make_strategy(top_k=5)
        candidates = [_make_candidate("ONLY")]
        ctx = _make_context()
        selected = s._score_candidates(candidates, ctx)
        assert selected == ["ONLY"]

    def test_top_k_limits_output(self) -> None:
        s = self._make_strategy(top_k=2)
        candidates = [_make_candidate(f"CB{i:02d}") for i in range(10)]
        ctx = _make_context()
        selected = s._score_candidates(candidates, ctx)
        assert len(selected) == 2

    def test_small_size_slightly_favored(self) -> None:
        """issue_size 作为弱因子，应对排名有轻微影响"""
        s = self._make_strategy(weight_issue_size=0.10)
        # 两个标的仅规模不同
        c1 = _make_candidate("SMALL", issue_size=3000.0)
        c2 = _make_candidate("BIG", issue_size=100000.0)
        ctx = _make_context()
        selected = s._score_candidates([c1, c2], ctx)
        # SMALL 应因规模加分排在前面（其他因子相同时）
        assert selected[0] == "SMALL"


# ---------------------------------------------------------------------------
# 5. on_bar 全流程
# ---------------------------------------------------------------------------

class TestOnBar:
    def test_first_bar_rebalances(self) -> None:
        """首根 bar 应触发调仓（bar_count=1 → should_rebalance）"""
        s = CBClausePlayStrategy(top_k=2, rebalance_days=5)
        s.on_start()
        bars = {
            "CB001": _make_cb_bar("CB001", 95.0, cb_value=60.0),
            "CB002": _make_cb_bar("CB002", 105.0, cb_value=80.0),
        }
        ctx = _make_context()
        intents = s.on_bar(bars, ctx)
        assert len(intents) > 0

    def test_non_rebalance_bar_no_intent(self) -> None:
        """非调仓日 → 无意图"""
        s = CBClausePlayStrategy(top_k=2, rebalance_days=5)
        s.on_start()
        bars = {
            "CB001": _make_cb_bar("CB001", 95.0, cb_value=60.0),
        }
        ctx = _make_context()
        # 第1根 bar 调仓
        s.on_bar(bars, ctx)
        # 第2-4根不调仓
        for _ in range(3):
            intents = s.on_bar(bars, ctx)
            assert len(intents) == 0

    def test_rebalance_after_interval(self) -> None:
        """间隔 rebalance_days 后再次调仓"""
        s = CBClausePlayStrategy(top_k=2, rebalance_days=3)
        s.on_start()
        bars = {
            "CB001": _make_cb_bar("CB001", 95.0, cb_value=60.0),
        }
        ctx = _make_context()
        # bar 1: rebalance
        intents1 = s.on_bar(bars, ctx)
        assert len(intents1) > 0
        # bar 2, 3: no rebalance
        s.on_bar(bars, ctx)
        s.on_bar(bars, ctx)
        # bar 4: rebalance again (bar_count=4, (4-1)%3==0)
        intents4 = s.on_bar(bars, ctx)
        assert len(intents4) > 0

    def test_intent_is_buy_side(self) -> None:
        """调仓意图应为 BUY"""
        s = CBClausePlayStrategy(top_k=2)
        s.on_start()
        bars = {
            "CB001": _make_cb_bar("CB001", 90.0, cb_value=55.0),
        }
        ctx = _make_context()
        intents = s.on_bar(bars, ctx)
        for intent in intents:
            assert intent.side == Side.BUY


# ---------------------------------------------------------------------------
# 6. 边界条件
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_all_filtered_no_intent(self) -> None:
        """所有标的被过滤 → 无意图（除卖出）"""
        s = CBClausePlayStrategy(max_price=50.0, top_k=2)
        s.on_start()
        bars = {
            "CB001": _make_cb_bar("CB001", 110.0, cb_value=60.0),
            "CB002": _make_cb_bar("CB002", 120.0, cb_value=70.0),
        }
        ctx = _make_context()
        intents = s.on_bar(bars, ctx)
        # 价格全超限，无买入意图
        buy_intents = [i for i in intents if i.side == Side.BUY]
        assert len(buy_intents) == 0

    def test_missing_cb_value_still_scores(self) -> None:
        """cb_value 缺失时回售分为 -1.0（最低排名），不崩溃"""
        s = CBClausePlayStrategy(top_k=2)
        s.on_start()
        candidates = [
            _make_candidate("A", cb_value=None),
            _make_candidate("B", cb_value=60.0),
        ]
        ctx = _make_context()
        selected = s._score_candidates(candidates, ctx)
        assert "B" in selected

    def test_missing_maturity_date(self) -> None:
        """maturity_date 缺失时催化分为默认值，不崩溃"""
        s = CBClausePlayStrategy(top_k=5)
        s.on_start()
        candidates = [
            _make_candidate("A", maturity_date=""),
            _make_candidate("B", maturity_date="20260601"),
        ]
        ctx = _make_context()
        selected = s._score_candidates(candidates, ctx)
        assert len(selected) == 2

    def test_trigger_ratio_custom(self) -> None:
        """自定义 trigger_ratio=0.85 → 更宽的触发区域"""
        s = CBClausePlayStrategy(putback_trigger_ratio=0.85)
        assert s.config.putback_trigger_ratio == 0.85

    def test_config_name_is_cb_clause_play(self) -> None:
        s = CBClausePlayStrategy()
        assert s.name == "cb_clause_play"
