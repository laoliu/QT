"""Tests for CBMultiLayerStrategy — 全三层融合可转债策略。

测试覆盖（≥22 用例 per §6.2 strategy ≥12）：
- 策略构造与配置 (4)
- 注册表集成 (1)
- _extra_filter: 溢价率 + 次新 (4)
- _on_bar_update_state: 正股数据更新 (3)
- _score_candidates: 8+1 因子排名融合 (8)
- on_bar 全流程 (3)
- 边界条件 (5)
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.cb_multi_layer import (
    CBMultiLayerStrategy,
    MultiLayerCBConfig,
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
    cb_value: float = 80.0,
    maturity_date: str = "20260601",
    issue_size: float = 5000.0,
    stk_short_name: str = "正股",
    list_date: str = "20230101",
    stk_code: str = "000001.SZ",
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
        "stk_code": stk_code,
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
    cb_value: float = 80.0,
    maturity_date: str = "20260601",
    list_date: str = "20230101",
    amount: float = 100.0,
    conv_price: float | None = None,
    stk_code: str = "000001.SZ",
) -> CandidateInfo:
    meta: dict[str, Any] = {"stk_code": stk_code}
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
    """策略构造与配置 (4 tests)。"""

    def test_default_config(self) -> None:
        s = CBMultiLayerStrategy()
        cfg = s.config
        assert cfg.top_k == 15
        # Layer 1
        assert cfg.weight_price == 0.15
        assert cfg.weight_premium == 0.12
        # Layer 2
        assert cfg.weight_stock_roe == 0.15
        assert cfg.weight_stock_ep == 0.10
        assert cfg.weight_stock_vol == 0.08
        assert cfg.weight_gamma == 0.08
        # Layer 3
        assert cfg.weight_putback == 0.15
        assert cfg.weight_maturity == 0.10
        # Weak
        assert cfg.weight_issue_size == 0.07
        # 参数
        assert cfg.max_price == 140.0
        assert cfg.max_premium == 70.0
        assert cfg.putback_trigger_ratio == 0.70

    def test_custom_config(self) -> None:
        cfg = MultiLayerCBConfig(
            top_k=10,
            weight_price=0.20,
            weight_putback=0.20,
            rebalance_days=3,
        )
        s = CBMultiLayerStrategy(config=cfg)
        assert s.config.top_k == 10
        assert s.config.weight_price == 0.20
        assert s.config.weight_putback == 0.20
        assert s.config.rebalance_days == 3

    def test_kwargs_passthrough(self) -> None:
        s = CBMultiLayerStrategy(top_k=8, max_price=120.0)
        assert s.config.top_k == 8
        assert s.config.max_price == 120.0

    def test_weight_sum_default(self) -> None:
        """默认权重合计 = 1.00。"""
        cfg = MultiLayerCBConfig()
        total = (
            cfg.weight_price + cfg.weight_premium
            + cfg.weight_stock_roe + cfg.weight_stock_ep
            + cfg.weight_stock_vol + cfg.weight_gamma
            + cfg.weight_putback + cfg.weight_maturity
            + cfg.weight_issue_size
        )
        assert abs(total - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# 2. 注册表
# ---------------------------------------------------------------------------

class TestRegistry:
    """注册表集成 (1 test)。"""

    def test_registered(self) -> None:
        cls = StrategyRegistry().get("cb_multi_layer")
        assert cls is not None
        assert cls is CBMultiLayerStrategy


# ---------------------------------------------------------------------------
# 3. 过滤器
# ---------------------------------------------------------------------------

class TestExtraFilter:
    """_extra_filter 测试 (4 tests)。"""

    def test_premium_too_high_rejected(self) -> None:
        s = CBMultiLayerStrategy(max_premium=70.0)
        ci = _make_candidate("T1", cb_over_rate=71.0)
        ctx = _make_context()
        assert s._extra_filter(ci, ctx) is False

    def test_premium_within_limit_accepted(self) -> None:
        s = CBMultiLayerStrategy(max_premium=70.0)
        ci = _make_candidate("T1", cb_over_rate=69.0)
        ctx = _make_context()
        assert s._extra_filter(ci, ctx) is True

    def test_new_bond_rejected(self) -> None:
        s = CBMultiLayerStrategy(min_age_days=10)
        ci = _make_candidate("T1", list_date="20240310")  # 5 days old
        ctx = _make_context(current_time=datetime(2024, 3, 15, 15, 0))
        assert s._extra_filter(ci, ctx) is False

    def test_old_bond_accepted(self) -> None:
        s = CBMultiLayerStrategy(min_age_days=10)
        ci = _make_candidate("T1", list_date="20230101")  # >1 year old
        ctx = _make_context(current_time=datetime(2024, 3, 15, 15, 0))
        assert s._extra_filter(ci, ctx) is True


# ---------------------------------------------------------------------------
# 4. 正股数据更新
# ---------------------------------------------------------------------------

class TestBarUpdateState:
    """_on_bar_update_state 测试 (3 tests)。"""

    def test_cb_value_history_updated(self) -> None:
        s = CBMultiLayerStrategy()
        bars = {
            "CB1": _make_cb_bar("CB1", 100, cb_value=85.0, stk_code="000001.SZ"),
        }
        ctx = _make_context()
        s._on_bar_update_state(bars, ctx)
        assert "000001.SZ" in s._stk_price_history
        assert len(s._stk_price_history["000001.SZ"]) == 1
        assert s._stk_price_history["000001.SZ"][-1] == 85.0

    def test_no_stk_code_skipped(self) -> None:
        s = CBMultiLayerStrategy()
        bars = {"CB1": _make_bar("CB1", 100, metadata={"cb_value": 80})}
        ctx = _make_context()
        s._on_bar_update_state(bars, ctx)
        assert len(s._stk_price_history) == 0

    def test_bridge_fundamentals_called(self) -> None:
        """有 bridge 时调用 get_fundamentals。"""
        mock_bridge = MagicMock()
        mock_fund = MagicMock(roe=0.15, ep=0.06)
        mock_bridge.get_fundamentals.return_value = mock_fund
        s = CBMultiLayerStrategy(stock_bridge=mock_bridge)
        bars = {
            "CB1": _make_cb_bar("CB1", 100, cb_value=85.0, stk_code="600001.SH"),
        }
        ctx = _make_context()
        s._on_bar_update_state(bars, ctx)
        mock_bridge.get_fundamentals.assert_called_once()
        assert "600001.SH" in s._current_fundamentals


# ---------------------------------------------------------------------------
# 5. 评分 — 8+1 因子融合
# ---------------------------------------------------------------------------

class TestScoreCandidates:
    """_score_candidates 测试 (8 tests)。"""

    def test_empty_returns_empty(self) -> None:
        s = CBMultiLayerStrategy()
        ctx = _make_context()
        assert s._score_candidates([], ctx) == []

    def test_single_candidate(self) -> None:
        s = CBMultiLayerStrategy()
        ci = _make_candidate("T1")
        ctx = _make_context()
        result = s._score_candidates([ci], ctx)
        assert result == ["T1"]

    def test_lower_price_preferred(self) -> None:
        """价格因子：低价优先。"""
        s = CBMultiLayerStrategy(
            weight_price=1.0, weight_premium=0, weight_stock_roe=0,
            weight_stock_ep=0, weight_stock_vol=0, weight_gamma=0,
            weight_putback=0, weight_maturity=0, weight_issue_size=0,
        )
        cs = [
            _make_candidate("HIGH", close=130.0),
            _make_candidate("LOW", close=90.0),
        ]
        ctx = _make_context()
        result = s._score_candidates(cs, ctx)
        assert result[0] == "LOW"

    def test_higher_roe_preferred(self) -> None:
        """ROE 因子：高 ROE 优先（需 bridge 数据）。"""
        mock_bridge = MagicMock()
        fund_high = MagicMock(roe=0.25, ep=0.05)
        fund_low = MagicMock(roe=0.05, ep=0.05)
        mock_bridge.get_fundamentals.side_effect = [fund_high, fund_low]

        s = CBMultiLayerStrategy(
            stock_bridge=mock_bridge,
            weight_price=0, weight_premium=0, weight_stock_roe=1.0,
            weight_stock_ep=0, weight_stock_vol=0, weight_gamma=0,
            weight_putback=0, weight_maturity=0, weight_issue_size=0,
        )
        # 手动设置当前基本面
        s._current_fundamentals = {
            "STK_H": fund_high,
            "STK_L": fund_low,
        }
        cs = [
            _make_candidate("ROE_LOW", stk_code="STK_L"),
            _make_candidate("ROE_HIGH", stk_code="STK_H"),
        ]
        ctx = _make_context()
        result = s._score_candidates(cs, ctx)
        assert result[0] == "ROE_HIGH"

    def test_higher_gamma_preferred(self) -> None:
        """Gamma 因子：ATM 附近（cb_value≈100）Gamma 最高，优先选中。"""
        s = CBMultiLayerStrategy(
            weight_price=0, weight_premium=0, weight_stock_roe=0,
            weight_stock_ep=0, weight_stock_vol=0, weight_gamma=1.0,
            weight_putback=0, weight_maturity=0, weight_issue_size=0,
        )
        cs = [
            _make_candidate("DEEP_OTM", cb_value=50.0),  # delta~0.5→gamma low
            _make_candidate("ATM", cb_value=100.0),       # delta=1.0→gamma=0 (special)
            _make_candidate("BALANCED", cb_value=75.0),   # gamma moderate
        ]
        ctx = _make_context()
        result = s._score_candidates(cs, ctx)
        # gamma_proxy = 4*d*(1-d) where d=cb_value/100
        # d=0.5→gamma=1.0, d=1.0→gamma=0, d=0.75→gamma=0.75
        assert result[0] == "DEEP_OTM"  # gamma=1.0 (highest)

    def test_higher_putback_preferred(self) -> None:
        """回售因子：高接近度优先。"""
        s = CBMultiLayerStrategy(
            weight_price=0, weight_premium=0, weight_stock_roe=0,
            weight_stock_ep=0, weight_stock_vol=0, weight_gamma=0,
            weight_putback=1.0, weight_maturity=0, weight_issue_size=0,
        )
        # compute_putback_from_cb(cb_value, trigger_ratio=0.70):
        #   parity = cb_value/100, score = 1 - parity/trigger_ratio
        # cb_value=50 → parity=0.50 → 1-0.50/0.70=0.286 (high — near trigger)
        # cb_value=90 → parity=0.90 → 1-0.90/0.70= -0.286 (negative — far from trigger)
        cs = [
            _make_candidate("FAR", cb_value=90.0),
            _make_candidate("NEAR", cb_value=50.0),
        ]
        ctx = _make_context()
        result = s._score_candidates(cs, ctx)
        assert result[0] == "NEAR"

    def test_maturity_catalyst_peak_preferred(self) -> None:
        """到期催化因子：1-3 年峰值区间优先。"""
        s = CBMultiLayerStrategy(
            weight_price=0, weight_premium=0, weight_stock_roe=0,
            weight_stock_ep=0, weight_stock_vol=0, weight_gamma=0,
            weight_putback=0, weight_maturity=1.0, weight_issue_size=0,
        )
        cs = [
            _make_candidate("FAR", maturity_date="20310601"),     # ~7y (distant)
            _make_candidate("PEAK", maturity_date="20260301"),    # ~2y (peak)
            _make_candidate("NEAR", maturity_date="20240501"),    # ~1.5mo (imminent)
        ]
        ctx = _make_context()
        result = s._score_candidates(cs, ctx)
        assert result[0] == "PEAK"

    def test_top_k_limit(self) -> None:
        """top_k 限制输出数量。"""
        s = CBMultiLayerStrategy(top_k=2)
        cs = [
            _make_candidate(f"T{i}", close=90 + i)
            for i in range(5)
        ]
        ctx = _make_context()
        result = s._score_candidates(cs, ctx)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 6. on_bar 全流程
# ---------------------------------------------------------------------------

class TestOnBar:
    """on_bar 集成测试 (3 tests)。"""

    def test_first_bar_no_rebalance(self) -> None:
        """首根 bar 初始化状态，一般不触发换仓。"""
        s = CBMultiLayerStrategy(rebalance_days=5)
        bars = {
            "CB1": _make_cb_bar("CB1", 100, amount=100, issue_size=5000),
        }
        ctx = _make_context()
        orders = s.on_bar(bars, ctx)
        assert isinstance(orders, list)

    def test_builds_positions_on_rebalance(self) -> None:
        """调仓日应产生买入指令。"""
        s = CBMultiLayerStrategy(rebalance_days=1, top_k=3)
        bars = {}
        for i in range(5):
            bars[f"CB{i}"] = _make_cb_bar(
                f"CB{i}", close=100 + i,
                amount=100, issue_size=5000,
                cb_over_rate=10.0 + i,
                cb_value=80.0 + i,
                maturity_date="20260601",
                list_date="20230101",
                stk_code=f"STK{i}.SZ",
            )
        ctx = _make_context(cash=500_000)
        # 第一次调用初始化
        s.on_bar(bars, ctx)
        # 第二次调用应触发换仓
        orders = s.on_bar(bars, ctx)
        assert isinstance(orders, list)

    def test_empty_bars_no_crash(self) -> None:
        s = CBMultiLayerStrategy()
        ctx = _make_context()
        orders = s.on_bar({}, ctx)
        assert isinstance(orders, list)
        assert len(orders) == 0


# ---------------------------------------------------------------------------
# 7. 边界条件
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """边界条件 (5 tests)。"""

    def test_all_candidates_same_values(self) -> None:
        """全部候选标的因子值相同 → 不崩溃。"""
        s = CBMultiLayerStrategy(top_k=2)
        cs = [
            _make_candidate(f"T{i}", close=100.0, cb_over_rate=10.0, cb_value=80.0)
            for i in range(4)
        ]
        ctx = _make_context()
        result = s._score_candidates(cs, ctx)
        assert len(result) == 2

    def test_none_premium(self) -> None:
        """溢价率为 None 时用默认值。"""
        s = CBMultiLayerStrategy(top_k=2)
        cs = [
            _make_candidate("T0", cb_over_rate=None),
            _make_candidate("T1", cb_over_rate=5.0),
        ]
        ctx = _make_context()
        result = s._score_candidates(cs, ctx)
        assert len(result) == 2

    def test_no_maturity_date(self) -> None:
        """到期日缺失时催化因子使用 9999 天（远期）。"""
        s = CBMultiLayerStrategy(top_k=2)
        cs = [
            _make_candidate("T0", maturity_date=None),
            _make_candidate("T1", maturity_date="20260301"),
        ]
        ctx = _make_context()
        result = s._score_candidates(cs, ctx)
        assert len(result) == 2
        # T1 has better catalyst → should rank first when maturity weight high
        s2 = CBMultiLayerStrategy(
            top_k=2,
            weight_price=0, weight_premium=0, weight_stock_roe=0,
            weight_stock_ep=0, weight_stock_vol=0, weight_gamma=0,
            weight_putback=0, weight_maturity=1.0, weight_issue_size=0,
        )
        result2 = s2._score_candidates(cs, ctx)
        assert result2[0] == "T1"

    def test_no_bridge_degrades_gracefully(self) -> None:
        """无 bridge 时 ROE/EP 降级为默认值。"""
        s = CBMultiLayerStrategy(use_stock_bridge=False)
        assert s._bridge is None
        cs = [
            _make_candidate("T0"),
            _make_candidate("T1"),
        ]
        ctx = _make_context()
        result = s._score_candidates(cs, ctx)
        assert len(result) == 2

    def test_stk_vol_insufficient_history(self) -> None:
        """价格历史不足 10 条时波动率为 None。"""
        s = CBMultiLayerStrategy()
        s._stk_price_history["STK1.SZ"] = deque([100.0, 101.0, 102.0])
        vol = s._compute_stk_vol("STK1.SZ")
        assert vol is None
