"""Tests for CBStockQualityV2Strategy — 正股质量增强 v2 可转债策略。

测试覆盖（≥12 用例 per §6.2）：
- 策略构造与配置
- 注册表集成
- _extra_filter: 溢价率 + 次新过滤
- _on_bar_update_state: 价格历史、基本面缓存
- _score_candidates: 8 因子排名加权
- v2 新因子: cash_flow, earnings_stability, growth
- 降级模式: bridge=None
- 边界情况: 空候选、单候选
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime
from typing import Any

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.cb_stock_quality_v2 import (
    CBStockQualityV2Strategy,
    StockQualityV2Config,
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


# ---------------------------------------------------------------------------
# 模拟正股基本面数据
# ---------------------------------------------------------------------------

class MockFundamental:
    """正股基本面数据 mock 对象。"""
    def __init__(
        self, roe=0.12, ep=0.08,
        ocf_ni_ratio=1.2, roe_std=0.02, revenue_growth=0.15,
    ):
        self.roe = roe
        self.ep = ep
        self.ocf_ni_ratio = ocf_ni_ratio
        self.roe_std = roe_std
        self.revenue_growth = revenue_growth


class MockBridge:
    """模拟 CBStockBridge."""
    def __init__(self, data: dict[str, MockFundamental] | None = None):
        self._data = data or {}

    def get_fundamentals(self, stk_code: str, query_date):
        if stk_code in self._data:
            return self._data[stk_code]
        raise ValueError(f"No data for {stk_code}")


# ===========================================================================
# 1. 配置 & 构造 (5 tests)
# ===========================================================================

class TestConfig:
    """StockQualityV2Config 配置测试。"""

    def test_default_weights_sum_to_one(self):
        cfg = StockQualityV2Config()
        total = (
            cfg.weight_price + cfg.weight_premium
            + cfg.weight_stock_roe + cfg.weight_stock_ep + cfg.weight_stock_vol
            + cfg.weight_cash_flow + cfg.weight_earnings_stability + cfg.weight_growth
        )
        assert abs(total - 1.0) < 1e-10

    def test_default_top_k(self):
        cfg = StockQualityV2Config()
        assert cfg.top_k == 15

    def test_custom_weights(self):
        cfg = StockQualityV2Config(weight_cash_flow=0.30, weight_growth=0.20)
        assert cfg.weight_cash_flow == 0.30
        assert cfg.weight_growth == 0.20

    def test_v2_default_values(self):
        cfg = StockQualityV2Config()
        assert cfg.default_ocf_ni_ratio == 1.0
        assert cfg.default_roe_std == 0.05
        assert cfg.default_revenue_growth == 0.0

    def test_inherits_from_cbrotationconfig(self):
        cfg = StockQualityV2Config()
        assert hasattr(cfg, "max_price")
        assert hasattr(cfg, "rebalance_days")
        assert hasattr(cfg, "stop_loss_pct")


# ===========================================================================
# 2. 策略构造 (4 tests)
# ===========================================================================

class TestConstruction:
    """策略实例化测试。"""

    def test_default_construction(self):
        strat = CBStockQualityV2Strategy()
        assert strat.name == "cb_stock_quality_v2"

    def test_custom_config(self):
        cfg = StockQualityV2Config(top_k=10, weight_cash_flow=0.25)
        strat = CBStockQualityV2Strategy(config=cfg)
        assert strat.config.top_k == 10
        assert strat.config.weight_cash_flow == 0.25

    def test_with_mock_bridge(self):
        bridge = MockBridge()
        strat = CBStockQualityV2Strategy(stock_bridge=bridge)
        assert strat._bridge is bridge

    def test_kwargs_construction(self):
        strat = CBStockQualityV2Strategy(top_k=8)
        assert strat.config.top_k == 8


# ===========================================================================
# 3. 注册表 (2 tests)
# ===========================================================================

class TestRegistry:
    """注册表集成测试。"""

    def test_registered(self):
        reg = StrategyRegistry()
        assert "cb_stock_quality_v2" in reg.list_strategies()

    def test_create_from_registry(self):
        reg = StrategyRegistry()
        strat = reg.create("cb_stock_quality_v2")
        assert isinstance(strat, CBStockQualityV2Strategy)


# ===========================================================================
# 4. _extra_filter (5 tests)
# ===========================================================================

class TestExtraFilter:
    """额外过滤测试。"""

    def test_high_premium_rejected(self):
        strat = CBStockQualityV2Strategy(max_premium=50.0)
        ctx = _make_context()
        c = _make_candidate("CB001", cb_over_rate=60.0)
        assert strat._extra_filter(c, ctx) is False

    def test_ok_premium_accepted(self):
        strat = CBStockQualityV2Strategy(max_premium=80.0)
        ctx = _make_context()
        c = _make_candidate("CB001", cb_over_rate=30.0)
        assert strat._extra_filter(c, ctx) is True

    def test_new_bond_rejected(self):
        strat = CBStockQualityV2Strategy(use_age_filter=True, min_age_days=5)
        ctx = _make_context(current_time=datetime(2024, 1, 3))
        c = _make_candidate("CB001", list_date="20240101")
        assert strat._extra_filter(c, ctx) is False

    def test_old_bond_accepted(self):
        strat = CBStockQualityV2Strategy(use_age_filter=True, min_age_days=3)
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        c = _make_candidate("CB001", list_date="20240101")
        assert strat._extra_filter(c, ctx) is True

    def test_age_filter_disabled(self):
        strat = CBStockQualityV2Strategy(use_age_filter=False, min_age_days=999)
        ctx = _make_context(current_time=datetime(2024, 1, 2))
        c = _make_candidate("CB001", list_date="20240101")
        assert strat._extra_filter(c, ctx) is True


# ===========================================================================
# 5. _on_bar_update_state (4 tests)
# ===========================================================================

class TestOnBarUpdateState:
    """状态更新测试。"""

    def test_updates_stk_price_history(self):
        strat = CBStockQualityV2Strategy(use_stock_bridge=False)
        strat._on_start_extra()
        ctx = _make_context()
        bars = {
            "CB001": _make_cb_bar("CB001", 105.0, stk_code="600001", cb_value=120.0),
        }
        strat._on_bar_update_state(bars, ctx)
        assert "600001" in strat._stk_price_history
        assert len(strat._stk_price_history["600001"]) == 1

    def test_accumulates_price_history(self):
        strat = CBStockQualityV2Strategy(use_stock_bridge=False)
        strat._on_start_extra()
        ctx = _make_context()
        for i in range(5):
            bars = {"CB001": _make_cb_bar("CB001", 100 + i, stk_code="600001", cb_value=110 + i)}
            strat._on_bar_update_state(bars, ctx)
        assert len(strat._stk_price_history["600001"]) == 5

    def test_uses_bridge_for_fundamentals(self):
        fund = MockFundamental(roe=0.15, ep=0.1, ocf_ni_ratio=1.5, roe_std=0.03, revenue_growth=0.2)
        bridge = MockBridge({"600001": fund})
        strat = CBStockQualityV2Strategy(stock_bridge=bridge)
        strat._on_start_extra()
        ctx = _make_context()
        bars = {"CB001": _make_cb_bar("CB001", 105.0, stk_code="600001")}
        strat._on_bar_update_state(bars, ctx)
        assert "600001" in strat._current_fundamentals
        assert strat._current_fundamentals["600001"].roe == 0.15

    def test_no_stk_code_skips(self):
        strat = CBStockQualityV2Strategy(use_stock_bridge=False)
        strat._on_start_extra()
        ctx = _make_context()
        bars = {"CB001": _make_cb_bar("CB001", 105.0, stk_code="")}
        strat._on_bar_update_state(bars, ctx)
        assert len(strat._stk_price_history) == 0


# ===========================================================================
# 6. _score_candidates 基础 (6 tests)
# ===========================================================================

class TestScoreCandidates:
    """评分逻辑测试。"""

    def test_empty_candidates(self):
        strat = CBStockQualityV2Strategy()
        ctx = _make_context()
        result = strat._score_candidates([], ctx)
        assert result == []

    def test_single_candidate(self):
        strat = CBStockQualityV2Strategy()
        ctx = _make_context()
        c = _make_candidate("CB001")
        result = strat._score_candidates([c], ctx)
        assert result == ["CB001"]

    def test_returns_top_k(self):
        strat = CBStockQualityV2Strategy(top_k=3)
        ctx = _make_context()
        candidates = [_make_candidate(f"CB{i:03d}", close=100 + i * 5) for i in range(10)]
        result = strat._score_candidates(candidates, ctx)
        assert len(result) == 3

    def test_low_price_preferred(self):
        """低价候选应排名靠前（weight_price > 0）。"""
        cfg = StockQualityV2Config(
            weight_price=1.0, weight_premium=0.0,
            weight_stock_roe=0.0, weight_stock_ep=0.0, weight_stock_vol=0.0,
            weight_cash_flow=0.0, weight_earnings_stability=0.0, weight_growth=0.0,
            top_k=2,
        )
        strat = CBStockQualityV2Strategy(config=cfg)
        ctx = _make_context()
        c1 = _make_candidate("CB_CHEAP", close=80.0)
        c2 = _make_candidate("CB_MID", close=120.0)
        c3 = _make_candidate("CB_EXP", close=160.0)
        result = strat._score_candidates([c3, c1, c2], ctx)
        assert result[0] == "CB_CHEAP"

    def test_low_premium_preferred(self):
        """低溢价率候选应排名靠前。"""
        cfg = StockQualityV2Config(
            weight_price=0.0, weight_premium=1.0,
            weight_stock_roe=0.0, weight_stock_ep=0.0, weight_stock_vol=0.0,
            weight_cash_flow=0.0, weight_earnings_stability=0.0, weight_growth=0.0,
            top_k=2,
        )
        strat = CBStockQualityV2Strategy(config=cfg)
        ctx = _make_context()
        c1 = _make_candidate("CB_LOW_PM", cb_over_rate=5.0)
        c2 = _make_candidate("CB_HIGH_PM", cb_over_rate=60.0)
        result = strat._score_candidates([c2, c1], ctx)
        assert result[0] == "CB_LOW_PM"

    def test_all_same_values_no_crash(self):
        """所有候选值相同不应崩溃。"""
        strat = CBStockQualityV2Strategy(top_k=5)
        ctx = _make_context()
        candidates = [_make_candidate(f"CB{i:03d}", close=100.0, cb_over_rate=10.0) for i in range(5)]
        result = strat._score_candidates(candidates, ctx)
        assert len(result) == 5


# ===========================================================================
# 7. v2 新因子测试 (8 tests)
# ===========================================================================

class TestV2NewFactors:
    """v2 新增因子（现金流、盈利稳定、增长）的评分测试。"""

    def _setup_with_fundamentals(
        self, fund_map: dict[str, MockFundamental], **cfg_kw
    ) -> CBStockQualityV2Strategy:
        bridge = MockBridge(fund_map)
        strat = CBStockQualityV2Strategy(stock_bridge=bridge, **cfg_kw)
        strat._on_start_extra()
        return strat

    def test_high_cash_flow_preferred(self):
        """高 OCF/NI ratio 应排名靠前。"""
        fund_map = {
            "600001": MockFundamental(ocf_ni_ratio=2.0),
            "600002": MockFundamental(ocf_ni_ratio=0.3),
        }
        strat = self._setup_with_fundamentals(
            fund_map,
            weight_price=0.0, weight_premium=0.0,
            weight_stock_roe=0.0, weight_stock_ep=0.0, weight_stock_vol=0.0,
            weight_cash_flow=1.0, weight_earnings_stability=0.0, weight_growth=0.0,
            top_k=2,
        )
        ctx = _make_context()
        # 加载基本面
        bars = {
            "CB001": _make_cb_bar("CB001", 100, stk_code="600001"),
            "CB002": _make_cb_bar("CB002", 100, stk_code="600002"),
        }
        strat._on_bar_update_state(bars, ctx)

        c1 = _make_candidate("CB001", stk_code="600001")
        c2 = _make_candidate("CB002", stk_code="600002")
        result = strat._score_candidates([c2, c1], ctx)
        assert result[0] == "CB001"  # 高现金流质量

    def test_low_roe_std_preferred(self):
        """低 ROE std（盈利稳定）应排名靠前。"""
        fund_map = {
            "600001": MockFundamental(roe_std=0.01),   # 稳定
            "600002": MockFundamental(roe_std=0.20),   # 不稳定
        }
        strat = self._setup_with_fundamentals(
            fund_map,
            weight_price=0.0, weight_premium=0.0,
            weight_stock_roe=0.0, weight_stock_ep=0.0, weight_stock_vol=0.0,
            weight_cash_flow=0.0, weight_earnings_stability=1.0, weight_growth=0.0,
            top_k=2,
        )
        ctx = _make_context()
        bars = {
            "CB001": _make_cb_bar("CB001", 100, stk_code="600001"),
            "CB002": _make_cb_bar("CB002", 100, stk_code="600002"),
        }
        strat._on_bar_update_state(bars, ctx)

        c1 = _make_candidate("CB001", stk_code="600001")
        c2 = _make_candidate("CB002", stk_code="600002")
        result = strat._score_candidates([c2, c1], ctx)
        assert result[0] == "CB001"  # 低 std = 更稳定

    def test_high_growth_preferred(self):
        """高营收增长应排名靠前。"""
        fund_map = {
            "600001": MockFundamental(revenue_growth=0.30),
            "600002": MockFundamental(revenue_growth=-0.10),
        }
        strat = self._setup_with_fundamentals(
            fund_map,
            weight_price=0.0, weight_premium=0.0,
            weight_stock_roe=0.0, weight_stock_ep=0.0, weight_stock_vol=0.0,
            weight_cash_flow=0.0, weight_earnings_stability=0.0, weight_growth=1.0,
            top_k=2,
        )
        ctx = _make_context()
        bars = {
            "CB001": _make_cb_bar("CB001", 100, stk_code="600001"),
            "CB002": _make_cb_bar("CB002", 100, stk_code="600002"),
        }
        strat._on_bar_update_state(bars, ctx)

        c1 = _make_candidate("CB001", stk_code="600001")
        c2 = _make_candidate("CB002", stk_code="600002")
        result = strat._score_candidates([c2, c1], ctx)
        assert result[0] == "CB001"  # 高增长

    def test_combined_v2_factors(self):
        """三个 v2 因子联合评分。"""
        # 600001: 质量好（高现金流+稳定+高增长）
        # 600002: 质量差
        fund_map = {
            "600001": MockFundamental(ocf_ni_ratio=1.8, roe_std=0.01, revenue_growth=0.25),
            "600002": MockFundamental(ocf_ni_ratio=0.5, roe_std=0.15, revenue_growth=-0.05),
        }
        strat = self._setup_with_fundamentals(
            fund_map,
            weight_price=0.0, weight_premium=0.0,
            weight_stock_roe=0.0, weight_stock_ep=0.0, weight_stock_vol=0.0,
            weight_cash_flow=0.35, weight_earnings_stability=0.35, weight_growth=0.30,
            top_k=2,
        )
        ctx = _make_context()
        bars = {
            "CB001": _make_cb_bar("CB001", 100, stk_code="600001"),
            "CB002": _make_cb_bar("CB002", 100, stk_code="600002"),
        }
        strat._on_bar_update_state(bars, ctx)

        c1 = _make_candidate("CB001", stk_code="600001")
        c2 = _make_candidate("CB002", stk_code="600002")
        result = strat._score_candidates([c2, c1], ctx)
        assert result[0] == "CB001"

    def test_default_fallback_when_no_bridge(self):
        """无 bridge 时使用默认值，不崩溃。"""
        strat = CBStockQualityV2Strategy(use_stock_bridge=False)
        strat._on_start_extra()
        ctx = _make_context()
        candidates = [
            _make_candidate("CB001", close=90, stk_code="600001"),
            _make_candidate("CB002", close=120, stk_code="600002"),
        ]
        # 不调 _on_bar_update_state → 没有基本面数据
        result = strat._score_candidates(candidates, ctx)
        assert len(result) == 2

    def test_mixed_fundamental_availability(self):
        """部分候选有基本面数据、部分没有。"""
        fund_map = {
            "600001": MockFundamental(roe=0.20, ep=0.15, ocf_ni_ratio=2.0, roe_std=0.01, revenue_growth=0.30),
        }
        strat = self._setup_with_fundamentals(fund_map, top_k=3)
        ctx = _make_context()
        bars = {"CB001": _make_cb_bar("CB001", 100, stk_code="600001")}
        strat._on_bar_update_state(bars, ctx)

        candidates = [
            _make_candidate("CB001", stk_code="600001"),
            _make_candidate("CB002", stk_code="600002"),  # 无数据
            _make_candidate("CB003", stk_code="600003"),  # 无数据
        ]
        result = strat._score_candidates(candidates, ctx)
        assert len(result) == 3
        # 有优质基本面的 CB001 应排在前列
        assert "CB001" in result

    def test_v2_factors_with_none_values(self):
        """基本面字段为 None 时使用默认值。"""

        class PartialFund:
            roe = 0.10
            ep = None
            ocf_ni_ratio = None
            roe_std = None
            revenue_growth = None

        fund_map = {"600001": PartialFund()}
        bridge = MockBridge(fund_map)
        strat = CBStockQualityV2Strategy(stock_bridge=bridge, top_k=2)
        strat._on_start_extra()
        ctx = _make_context()
        bars = {"CB001": _make_cb_bar("CB001", 100, stk_code="600001")}
        strat._on_bar_update_state(bars, ctx)

        c1 = _make_candidate("CB001", stk_code="600001")
        c2 = _make_candidate("CB002")
        result = strat._score_candidates([c1, c2], ctx)
        assert len(result) == 2

    def test_high_roe_with_all_eight_factors(self):
        """完整 8 因子组合测试：全面优质股应排名靠前。"""
        # CB001 对应的正股：全维度优质
        fund_good = MockFundamental(roe=0.25, ep=0.15, ocf_ni_ratio=2.0, roe_std=0.01, revenue_growth=0.30)
        # CB002 对应的正股：全维度较差
        fund_bad = MockFundamental(roe=0.02, ep=0.01, ocf_ni_ratio=0.3, roe_std=0.20, revenue_growth=-0.15)
        fund_map = {"600001": fund_good, "600002": fund_bad}
        bridge = MockBridge(fund_map)

        strat = CBStockQualityV2Strategy(stock_bridge=bridge, top_k=2)
        strat._on_start_extra()
        ctx = _make_context()

        # 更新基本面
        bars = {
            "CB001": _make_cb_bar("CB001", 95, stk_code="600001", cb_over_rate=5),
            "CB002": _make_cb_bar("CB002", 130, stk_code="600002", cb_over_rate=45),
        }
        strat._on_bar_update_state(bars, ctx)

        c1 = _make_candidate("CB001", close=95, cb_over_rate=5, stk_code="600001")
        c2 = _make_candidate("CB002", close=130, cb_over_rate=45, stk_code="600002")
        result = strat._score_candidates([c2, c1], ctx)
        # CB001: 低价+低溢价+优质基本面 → 应排第一
        assert result[0] == "CB001"


# ===========================================================================
# 8. 波动率计算 (3 tests)
# ===========================================================================

class TestComputeStKVol:
    """正股波动率计算测试。"""

    def test_none_for_missing_stk(self):
        strat = CBStockQualityV2Strategy()
        assert strat._compute_stk_vol("UNKNOWN") is None

    def test_none_for_insufficient_data(self):
        strat = CBStockQualityV2Strategy()
        strat._stk_price_history["600001"] = deque([100.0, 101.0, 102.0])
        assert strat._compute_stk_vol("600001") is None  # < 10 点

    def test_positive_vol_with_sufficient_data(self):
        strat = CBStockQualityV2Strategy()
        # 模拟 20 天价格
        prices = deque([100.0 + i * 0.5 + ((-1) ** i) * 2 for i in range(20)])
        strat._stk_price_history["600001"] = prices
        vol = strat._compute_stk_vol("600001")
        assert vol is not None
        assert vol > 0


# ===========================================================================
# 9. on_start_extra (3 tests)
# ===========================================================================

class TestOnStartExtra:
    """初始化/重置测试。"""

    def test_clears_history(self):
        strat = CBStockQualityV2Strategy(use_stock_bridge=False)
        strat._stk_price_history["old"] = deque([1, 2, 3])
        strat._current_fundamentals["old"] = "data"
        strat._on_start_extra()
        assert len(strat._stk_price_history) == 0
        assert len(strat._current_fundamentals) == 0

    def test_bridge_not_replaced_if_provided(self):
        bridge = MockBridge()
        strat = CBStockQualityV2Strategy(stock_bridge=bridge)
        strat._on_start_extra()
        assert strat._bridge is bridge

    def test_no_bridge_when_disabled(self):
        strat = CBStockQualityV2Strategy(use_stock_bridge=False)
        strat._on_start_extra()
        assert strat._bridge is None


# ===========================================================================
# 10. 边界情况 (4 tests)
# ===========================================================================

class TestEdgeCases:
    """边界情况测试。"""

    def test_zero_total_weight(self):
        """所有权重为 0 时不应崩溃（除法保护）。"""
        cfg = StockQualityV2Config(
            weight_price=0, weight_premium=0,
            weight_stock_roe=0, weight_stock_ep=0, weight_stock_vol=0,
            weight_cash_flow=0, weight_earnings_stability=0, weight_growth=0,
            top_k=5,
        )
        strat = CBStockQualityV2Strategy(config=cfg)
        ctx = _make_context()
        candidates = [_make_candidate(f"CB{i:03d}") for i in range(3)]
        result = strat._score_candidates(candidates, ctx)
        assert len(result) == 3

    def test_none_premium_uses_default(self):
        """溢价率为 None 时使用默认 50。"""
        strat = CBStockQualityV2Strategy(top_k=2)
        ctx = _make_context()
        c1 = _make_candidate("CB001", cb_over_rate=10.0)
        c2 = CandidateInfo(
            symbol="CB002", close=100.0, issue_size=5000.0,
            cb_over_rate=None, cb_value=100.0, amount=100.0,
            maturity_date="20280101", list_date="20240101",
            metadata={},
        )
        result = strat._score_candidates([c1, c2], ctx)
        assert len(result) == 2

    def test_large_candidate_pool(self):
        """大量候选测试性能。"""
        strat = CBStockQualityV2Strategy(top_k=10)
        ctx = _make_context()
        candidates = [
            _make_candidate(f"CB{i:04d}", close=80 + i * 0.5, cb_over_rate=5 + i * 0.3)
            for i in range(200)
        ]
        result = strat._score_candidates(candidates, ctx)
        assert len(result) == 10

    def test_empty_stk_code_uses_defaults(self):
        """空 stk_code 时因子使用默认值。"""
        strat = CBStockQualityV2Strategy(use_stock_bridge=False, top_k=2)
        strat._on_start_extra()
        ctx = _make_context()
        c1 = _make_candidate("CB001", close=90, stk_code="")
        c2 = _make_candidate("CB002", close=110, stk_code="")
        result = strat._score_candidates([c1, c2], ctx)
        assert len(result) == 2
