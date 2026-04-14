"""Tests for CBFourFactorStrategy — CB 多维四因子模型策略。

测试覆盖（≥12 用例 per §6.2）：
- 配置 & 构造
- 注册表集成
- _extra_filter
- _on_bar_update_state: 价格历史 + 成交额历史 + 市场代理
- 因子计算: macro, momentum, turnover, value
- _score_candidates: 排名加权
- 边界情况

RETIRED: R56 A2 (2026-03-14) — Alpha衰减FAIL, 近3年SR为负。测试保留但跳过。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="Strategy retired (R56 A2): Alpha衰减FAIL")

import math
from collections import deque
from datetime import datetime
from typing import Any

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.cb_four_factor import (
    CBFourFactorStrategy,
    FourFactorCBConfig,
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
    ts_code: str, close: float,
    amount: float = 100.0,
    cb_over_rate: float = 10.0,
    cb_value: float = 100.0,
    maturity_date: str = "20280101",
    issue_size: float = 5000.0,
    list_date: str = "20240101",
    stk_code: str = "",
    **extra: Any,
) -> Bar:
    meta = {
        "amount": amount,
        "cb_over_rate": cb_over_rate,
        "cb_value": cb_value,
        "maturity_date": maturity_date,
        "issue_size": issue_size,
        "list_date": list_date,
        **extra,
    }
    if stk_code:
        meta["stk_code"] = stk_code
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
    cb_value: float = 100.0,
    list_date: str = "20240101",
    stk_code: str = "",
    amount: float = 100.0,
) -> CandidateInfo:
    meta = {}
    if stk_code:
        meta["stk_code"] = stk_code
    return CandidateInfo(
        symbol=symbol,
        close=close,
        issue_size=issue_size,
        cb_over_rate=cb_over_rate,
        cb_value=cb_value,
        amount=amount,
        maturity_date="20280101",
        list_date=list_date,
        metadata=meta,
    )


# ===========================================================================
# 1. 配置 (4 tests)
# ===========================================================================

class TestConfig:
    def test_default_weights_sum_to_one(self):
        cfg = FourFactorCBConfig()
        total = cfg.weight_macro + cfg.weight_momentum + cfg.weight_liquidity + cfg.weight_value
        assert abs(total - 1.0) < 1e-10

    def test_default_sub_weights_sum(self):
        cfg = FourFactorCBConfig()
        total = cfg.value_price_w + cfg.value_premium_w + cfg.value_size_w
        assert abs(total - 1.0) < 1e-10

    def test_custom_weights(self):
        cfg = FourFactorCBConfig(weight_macro=0.10, weight_momentum=0.40)
        assert cfg.weight_macro == 0.10
        assert cfg.weight_momentum == 0.40

    def test_inherits_rotation_config(self):
        cfg = FourFactorCBConfig()
        assert hasattr(cfg, "max_price")
        assert hasattr(cfg, "rebalance_days")


# ===========================================================================
# 2. 构造 & 注册 (4 tests)
# ===========================================================================

class TestConstructionAndRegistry:
    def test_default_construction(self):
        strat = CBFourFactorStrategy()
        assert strat.name == "cb_four_factor"

    def test_custom_config(self):
        cfg = FourFactorCBConfig(top_k=10)
        strat = CBFourFactorStrategy(config=cfg)
        assert strat.config.top_k == 10

    def test_registered(self):
        reg = StrategyRegistry()
        assert "cb_four_factor" in reg.list_strategies()

    def test_create_from_registry(self):
        reg = StrategyRegistry()
        strat = reg.create("cb_four_factor")
        assert isinstance(strat, CBFourFactorStrategy)


# ===========================================================================
# 3. _extra_filter (4 tests)
# ===========================================================================

class TestExtraFilter:
    def test_high_premium_rejected(self):
        strat = CBFourFactorStrategy(max_premium=50.0)
        ctx = _make_context()
        c = _make_candidate("CB001", cb_over_rate=60.0)
        assert strat._extra_filter(c, ctx) is False

    def test_ok_premium_accepted(self):
        strat = CBFourFactorStrategy(max_premium=80.0)
        ctx = _make_context()
        c = _make_candidate("CB001", cb_over_rate=30.0)
        assert strat._extra_filter(c, ctx) is True

    def test_new_bond_rejected(self):
        strat = CBFourFactorStrategy(use_age_filter=True, min_age_days=5)
        ctx = _make_context(current_time=datetime(2024, 1, 3))
        c = _make_candidate("CB001", list_date="20240101")
        assert strat._extra_filter(c, ctx) is False

    def test_age_filter_disabled(self):
        strat = CBFourFactorStrategy(use_age_filter=False)
        ctx = _make_context(current_time=datetime(2024, 1, 2))
        c = _make_candidate("CB001", list_date="20240101")
        assert strat._extra_filter(c, ctx) is True


# ===========================================================================
# 4. _on_bar_update_state (4 tests)
# ===========================================================================

class TestOnBarUpdateState:
    def test_updates_stk_price(self):
        strat = CBFourFactorStrategy()
        strat._on_start_extra()
        ctx = _make_context()
        bars = {"CB001": _make_cb_bar("CB001", 105, stk_code="600001", cb_value=120)}
        strat._on_bar_update_state(bars, ctx)
        assert "600001" in strat._stk_price_history
        assert len(strat._stk_price_history["600001"]) == 1

    def test_updates_amount_history(self):
        strat = CBFourFactorStrategy()
        strat._on_start_extra()
        ctx = _make_context()
        bars = {"CB001": _make_cb_bar("CB001", 105, amount=200)}
        strat._on_bar_update_state(bars, ctx)
        assert "CB001" in strat._cb_amount_history
        assert len(strat._cb_amount_history["CB001"]) == 1

    def test_updates_market_values(self):
        strat = CBFourFactorStrategy()
        strat._on_start_extra()
        ctx = _make_context()
        bars = {"CB001": _make_cb_bar("CB001", 105, stk_code="600001", cb_value=120)}
        strat._on_bar_update_state(bars, ctx)
        assert len(strat._market_values) == 1

    def test_multiple_bars_accumulate(self):
        strat = CBFourFactorStrategy()
        strat._on_start_extra()
        ctx = _make_context()
        for i in range(10):
            bars = {"CB001": _make_cb_bar("CB001", 100 + i, stk_code="600001", cb_value=110 + i, amount=50 + i)}
            strat._on_bar_update_state(bars, ctx)
        assert len(strat._stk_price_history["600001"]) == 10
        assert len(strat._cb_amount_history["CB001"]) == 10


# ===========================================================================
# 5. 因子计算 (6 tests)
# ===========================================================================

class TestFactorComputation:
    def test_macro_factor_neutral_default(self):
        strat = CBFourFactorStrategy()
        strat._on_start_extra()
        score = strat._compute_macro_factor()
        assert abs(score - 0.5) < 1e-6  # 数据不足 → 中性

    def test_macro_factor_bullish(self):
        strat = CBFourFactorStrategy()
        strat._on_start_extra()
        # 上涨市场
        for v in [100, 101, 102, 103, 104, 105, 106, 107, 108, 110]:
            strat._market_values.append(v)
        score = strat._compute_macro_factor()
        assert score > 0.5  # 应偏乐观

    def test_macro_factor_bearish(self):
        strat = CBFourFactorStrategy()
        strat._on_start_extra()
        # 下跌市场
        for v in [110, 108, 106, 104, 102, 100, 98, 96, 94, 90]:
            strat._market_values.append(v)
        score = strat._compute_macro_factor()
        assert score < 0.5  # 应偏悲观

    def test_momentum_positive(self):
        strat = CBFourFactorStrategy()
        strat._on_start_extra()
        strat._stk_price_history["600001"] = deque([100 + i * 0.5 for i in range(25)])
        mom = strat._compute_momentum("600001", 20)
        assert mom > 0

    def test_momentum_missing_data(self):
        strat = CBFourFactorStrategy()
        strat._on_start_extra()
        assert strat._compute_momentum("UNKNOWN", 20) == 0.0

    def test_turnover_score_basic(self):
        strat = CBFourFactorStrategy()
        strat._on_start_extra()
        strat._cb_amount_history["CB001"] = deque([100, 100, 100, 100, 100, 200, 200, 200, 200, 200])
        score = strat._compute_turnover_score("CB001", 200)
        # 短期均值 200 / 长期均值 150 > 1.0
        assert score > 1.0


# ===========================================================================
# 6. _score_candidates (6 tests)
# ===========================================================================

class TestScoreCandidates:
    def test_empty(self):
        strat = CBFourFactorStrategy()
        assert strat._score_candidates([], _make_context()) == []

    def test_single(self):
        strat = CBFourFactorStrategy()
        c = _make_candidate("CB001")
        assert strat._score_candidates([c], _make_context()) == ["CB001"]

    def test_returns_top_k(self):
        strat = CBFourFactorStrategy(top_k=3)
        ctx = _make_context()
        candidates = [_make_candidate(f"CB{i:03d}", close=80 + i * 5) for i in range(10)]
        result = strat._score_candidates(candidates, ctx)
        assert len(result) == 3

    def test_low_price_low_premium_preferred(self):
        """价值因子：低价+低溢价应排名靠前。"""
        cfg = FourFactorCBConfig(
            weight_macro=0, weight_momentum=0, weight_liquidity=0, weight_value=1.0,
            top_k=2,
        )
        strat = CBFourFactorStrategy(config=cfg)
        ctx = _make_context()
        c1 = _make_candidate("CB_CHEAP", close=85, cb_over_rate=5)
        c2 = _make_candidate("CB_EXPENSIVE", close=140, cb_over_rate=50)
        result = strat._score_candidates([c2, c1], ctx)
        assert result[0] == "CB_CHEAP"

    def test_high_momentum_preferred(self):
        """正股动量因子：高动量应排名靠前。"""
        cfg = FourFactorCBConfig(
            weight_macro=0, weight_momentum=1.0, weight_liquidity=0, weight_value=0,
            top_k=2,
        )
        strat = CBFourFactorStrategy(config=cfg)
        strat._on_start_extra()
        # 给 600001 设置正动量, 600002 负动量
        strat._stk_price_history["600001"] = deque([100 + i for i in range(25)])
        strat._stk_price_history["600002"] = deque([100 - i * 0.5 for i in range(25)])
        ctx = _make_context()
        c1 = _make_candidate("CB_UP", stk_code="600001")
        c2 = _make_candidate("CB_DOWN", stk_code="600002")
        result = strat._score_candidates([c2, c1], ctx)
        assert result[0] == "CB_UP"

    def test_all_same_no_crash(self):
        strat = CBFourFactorStrategy(top_k=5)
        ctx = _make_context()
        candidates = [_make_candidate(f"CB{i:03d}") for i in range(5)]
        result = strat._score_candidates(candidates, ctx)
        assert len(result) == 5


# ===========================================================================
# 7. 边界情况 (4 tests)
# ===========================================================================

class TestEdgeCases:
    def test_zero_weights_no_crash(self):
        cfg = FourFactorCBConfig(
            weight_macro=0, weight_momentum=0, weight_liquidity=0, weight_value=0,
            top_k=3,
        )
        strat = CBFourFactorStrategy(config=cfg)
        ctx = _make_context()
        candidates = [_make_candidate(f"CB{i:03d}") for i in range(3)]
        result = strat._score_candidates(candidates, ctx)
        assert len(result) == 3

    def test_none_premium_uses_default(self):
        strat = CBFourFactorStrategy(top_k=2)
        ctx = _make_context()
        c1 = _make_candidate("CB001", cb_over_rate=10)
        c2 = CandidateInfo(
            symbol="CB002", close=100, issue_size=5000,
            cb_over_rate=None, cb_value=100, amount=100,
            maturity_date="20280101", list_date="20240101",
            metadata={},
        )
        result = strat._score_candidates([c1, c2], ctx)
        assert len(result) == 2

    def test_on_start_clears_state(self):
        strat = CBFourFactorStrategy()
        strat._stk_price_history["old"] = deque([1, 2, 3])
        strat._cb_amount_history["old"] = deque([10, 20])
        strat._market_values.append(100)
        strat._on_start_extra()
        assert len(strat._stk_price_history) == 0
        assert len(strat._cb_amount_history) == 0
        assert len(strat._market_values) == 0

    def test_large_pool(self):
        strat = CBFourFactorStrategy(top_k=10)
        ctx = _make_context()
        candidates = [
            _make_candidate(f"CB{i:04d}", close=80 + i * 0.3, cb_over_rate=5 + i * 0.2, amount=50 + i)
            for i in range(200)
        ]
        result = strat._score_candidates(candidates, ctx)
        assert len(result) == 10
