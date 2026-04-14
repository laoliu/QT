"""Tests for CBGammaConvexityStrategy — Delta 凸性可转债策略。

测试覆盖（≥12 用例 per §6.2）：
- 策略构造与配置
- 注册表集成
- _extra_filter: Delta 区间 + 溢价率 + 次新
- _score_candidates: Gamma + vol + premium 排名
- on_bar 全流程
- 边界条件

RETIRED: R56 A2 (2026-03-14) — Alpha衰减FAIL, SR=0.324。测试保留但跳过。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="Strategy retired (R56 A2): Alpha衰减FAIL")

from collections import deque
from datetime import datetime
from typing import Any

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.cb_gamma_convexity import (
    CBGammaConvexityStrategy,
    GammaConvexityCBConfig,
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
    cb_value: float = 50.0,
    maturity_date: str = "20280101",
    issue_size: float = 5000.0,
    stk_short_name: str = "正股",
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
        "stk_short_name": stk_short_name,
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
    cb_value: float = 50.0,
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
        metadata=meta,
        amount=amount,
        issue_size=issue_size,
        cb_over_rate=cb_over_rate,
        cb_value=cb_value,
        maturity_date="20280101",
        list_date=list_date,
        stk_short_name="正股",
    )


# ---------------------------------------------------------------------------
# 1. 构造与配置
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_default_config(self) -> None:
        s = CBGammaConvexityStrategy()
        cfg = s.config
        assert cfg.top_k == 15
        assert cfg.delta_min == 0.25
        assert cfg.delta_max == 0.75
        assert cfg.weight_gamma == 0.25
        assert cfg.weight_issue_size == 0.10
        assert cfg.max_issue_size == float("inf")

    def test_custom_config(self) -> None:
        cfg = GammaConvexityCBConfig(
            top_k=10, delta_min=0.30, delta_max=0.70,
        )
        s = CBGammaConvexityStrategy(config=cfg)
        assert s.config.top_k == 10
        assert s.config.delta_min == 0.30

    def test_kwargs_config(self) -> None:
        s = CBGammaConvexityStrategy(top_k=8, weight_gamma=0.40)
        assert s.config.top_k == 8
        assert s.config.weight_gamma == 0.40


# ---------------------------------------------------------------------------
# 2. 注册表
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_registered(self) -> None:
        cls = StrategyRegistry().get("cb_gamma_convexity")
        assert cls is not None
        assert cls is CBGammaConvexityStrategy


# ---------------------------------------------------------------------------
# 3. _extra_filter: Delta 区间
# ---------------------------------------------------------------------------

class TestExtraFilterDelta:
    def test_balanced_zone_passes(self) -> None:
        """cb_value=50 → delta≈0.5 → 在 [0.25, 0.75] 内。"""
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        c = _make_candidate("CB001", cb_value=50.0)
        assert s._extra_filter(c, _make_context()) is True

    def test_bond_zone_rejected(self) -> None:
        """cb_value=20 → delta≈0.2 → 低于 delta_min=0.25。"""
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        c = _make_candidate("CB001", cb_value=20.0)
        assert s._extra_filter(c, _make_context()) is False

    def test_equity_zone_rejected(self) -> None:
        """cb_value=90 → delta≈0.9 → 高于 delta_max=0.75。"""
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        c = _make_candidate("CB001", cb_value=90.0)
        assert s._extra_filter(c, _make_context()) is False

    def test_none_cb_value_rejected(self) -> None:
        """cb_value=None → delta=None → 过滤掉。"""
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        c = _make_candidate("CB001", cb_value=None)
        assert s._extra_filter(c, _make_context()) is False


# ---------------------------------------------------------------------------
# 4. _extra_filter: 溢价率 + 次新
# ---------------------------------------------------------------------------

class TestExtraFilterOther:
    def test_high_premium_rejected(self) -> None:
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        c = _make_candidate("CB001", cb_value=50.0, cb_over_rate=70.0)
        assert s._extra_filter(c, _make_context()) is False

    def test_too_new_rejected(self) -> None:
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        ctx = _make_context(current_time=datetime(2024, 1, 2))
        c = _make_candidate("CB001", cb_value=50.0, list_date="20240101")
        assert s._extra_filter(c, ctx) is False


# ---------------------------------------------------------------------------
# 5. _score_candidates
# ---------------------------------------------------------------------------

class TestScoreCandidates:
    def test_empty(self) -> None:
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        assert s._score_candidates([], _make_context()) == []

    def test_single(self) -> None:
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        c = _make_candidate("CB001", cb_value=50.0)
        assert s._score_candidates([c], _make_context()) == ["CB001"]

    def test_higher_gamma_ranks_better(self) -> None:
        """接近 ATM 的 CB gamma 更高，应排名靠前。"""
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        candidates = [
            # cb_value=50 → delta=0.5 → gamma_proxy=1.0 (最大)
            _make_candidate("CB_ATM", close=105.0, cb_value=50.0,
                            cb_over_rate=10.0, issue_size=5000),
            # cb_value=30 → delta=0.3 → gamma_proxy=0.84
            _make_candidate("CB_OTM", close=105.0, cb_value=30.0,
                            cb_over_rate=10.0, issue_size=5000),
        ]
        result = s._score_candidates(candidates, _make_context())
        assert result[0] == "CB_ATM"

    def test_low_premium_ranks_better(self) -> None:
        """同等 Gamma 下，低溢价应排名靠前。"""
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        candidates = [
            _make_candidate("CB_LP", close=105.0, cb_value=50.0,
                            cb_over_rate=5.0, issue_size=5000),
            _make_candidate("CB_HP", close=105.0, cb_value=50.0,
                            cb_over_rate=40.0, issue_size=5000),
        ]
        result = s._score_candidates(candidates, _make_context())
        assert result[0] == "CB_LP"


# ---------------------------------------------------------------------------
# 6. on_bar 全流程
# ---------------------------------------------------------------------------

class TestOnBarFullFlow:
    def test_rebalance_with_balanced_cb(self) -> None:
        """平衡区 CB 应被买入。"""
        cfg = GammaConvexityCBConfig(
            rebalance_days=1, top_k=2, use_stock_bridge=False,
        )
        s = CBGammaConvexityStrategy(config=cfg)
        s.on_start()
        bars = {
            "CB_OK": _make_cb_bar("CB_OK", 105.0, cb_value=50.0, stk_code="STK_A"),
            # cb_value=10 → delta=0.1 → 被过滤
            "CB_BAD": _make_cb_bar("CB_BAD", 95.0, cb_value=10.0, stk_code="STK_B"),
        }
        ctx = _make_context()
        intents = s.on_bar(bars, ctx)
        # CB_OK 在平衡区，应被买入；CB_BAD 在债性区，被过滤
        buy_symbols = [i.symbol for i in intents if i.side == Side.BUY]
        assert "CB_OK" in buy_symbols
        assert "CB_BAD" not in buy_symbols

    def test_stop_loss(self) -> None:
        cfg = GammaConvexityCBConfig(
            stop_loss_pct=0.10, use_stock_bridge=False,
        )
        s = CBGammaConvexityStrategy(config=cfg)
        s.on_start()
        s._entry_prices["CB001"] = 110.0
        s._peak_prices["CB001"] = 110.0
        bars = {"CB001": _make_cb_bar("CB001", 95.0, cb_value=50.0, stk_code="STK_A")}
        ctx = _make_context(positions={"CB001": 100})
        intents = s.on_bar(bars, ctx)
        sell_intents = [i for i in intents if i.side == Side.SELL]
        assert len(sell_intents) > 0


# ---------------------------------------------------------------------------
# 7. 正股波动率更新
# ---------------------------------------------------------------------------

class TestStockVolUpdate:
    def test_cb_value_history(self) -> None:
        """on_bar 应通过 cb_value 更新正股价格历史。"""
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        bars = {
            "CB001": _make_cb_bar("CB001", 105.0, cb_value=50.0, stk_code="STK_A"),
        }
        s._on_bar_update_state(bars, _make_context())
        assert "STK_A" in s._stk_price_history
        assert len(s._stk_price_history["STK_A"]) == 1

    def test_vol_computation_with_history(self) -> None:
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        s._stk_price_history["STK_A"] = deque(
            [50.0 + i * 0.2 for i in range(20)], maxlen=65,
        )
        vol = s._compute_stk_vol("STK_A")
        assert vol is not None
        assert vol > 0


# ---------------------------------------------------------------------------
# 8. 大规模标的不被硬过滤
# ---------------------------------------------------------------------------

class TestNoHardSizeFilter:
    def test_large_size_passes(self) -> None:
        """max_issue_size=inf → 大规模标的只要在 Delta 区间就保留。"""
        s = CBGammaConvexityStrategy(use_stock_bridge=False)
        s.on_start()
        c = _make_candidate(
            "CB_BIG", close=105.0, cb_value=50.0,
            cb_over_rate=10.0, issue_size=500000,
        )
        assert s._extra_filter(c, _make_context()) is True
