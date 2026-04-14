"""Tests for CBStockQualityStrategy — 正股质量增强可转债策略。

测试覆盖（≥12 用例 per §6.2）：
- 策略构造与配置
- 注册表集成
- _extra_filter: 溢价率 + 次新过滤
- _on_bar_update_state: 价格历史更新 + 基本面获取
- _score_candidates: 排名加权（含正股因子）
- 降级模式: bridge=None
- on_bar 全流程: 调仓 + 止损
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.cb_stock_quality import (
    CBStockQualityStrategy,
    StockQualityCBConfig,
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
        metadata=meta,
        amount=amount,
        issue_size=issue_size,
        cb_over_rate=cb_over_rate,
        cb_value=cb_value,
        maturity_date="20280101",
        list_date=list_date,
        stk_short_name="正股",
    )


class FakeStockBridge:
    """模拟 CBStockBridge，不触发网络请求。"""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def set_fundamentals(
        self, stk_code: str, roe: float | None = None, ep: float | None = None,
    ) -> None:
        from aipha.data.cb_stock_bridge import StockFundamentals
        from datetime import date
        self._data[stk_code] = StockFundamentals(
            stk_code=stk_code,
            query_date=date(2024, 1, 1),
            roe=roe,
            ep=ep,
        )

    def get_fundamentals(self, stk_code: str, query_date: Any) -> Any:
        if stk_code in self._data:
            return self._data[stk_code]
        from aipha.data.cb_stock_bridge import StockFundamentals
        return StockFundamentals(stk_code=stk_code, query_date=query_date)


# ---------------------------------------------------------------------------
# 1. 构造与配置
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_default_config(self) -> None:
        s = CBStockQualityStrategy()
        cfg = s.config
        assert cfg.top_k == 15
        assert cfg.weight_stock_roe == 0.20
        assert cfg.weight_issue_size == 0.10
        assert cfg.max_issue_size == float("inf")

    def test_custom_config(self) -> None:
        cfg = StockQualityCBConfig(top_k=10, weight_stock_roe=0.30)
        s = CBStockQualityStrategy(config=cfg)
        assert s.config.top_k == 10
        assert s.config.weight_stock_roe == 0.30

    def test_kwargs_config(self) -> None:
        s = CBStockQualityStrategy(top_k=8, weight_price=0.30)
        assert s.config.top_k == 8
        assert s.config.weight_price == 0.30


# ---------------------------------------------------------------------------
# 2. 策略注册
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_registered(self) -> None:
        cls = StrategyRegistry().get("cb_stock_quality")
        assert cls is not None
        assert cls is CBStockQualityStrategy


# ---------------------------------------------------------------------------
# 3. _extra_filter
# ---------------------------------------------------------------------------

class TestExtraFilter:
    def test_premium_pass(self) -> None:
        s = CBStockQualityStrategy()
        s.on_start()
        ctx = _make_context()
        c = _make_candidate("CB001", cb_over_rate=30.0)
        assert s._extra_filter(c, ctx) is True

    def test_premium_reject(self) -> None:
        s = CBStockQualityStrategy()
        s.on_start()
        ctx = _make_context()
        c = _make_candidate("CB001", cb_over_rate=90.0)
        assert s._extra_filter(c, ctx) is False

    def test_age_too_new(self) -> None:
        s = CBStockQualityStrategy()
        s.on_start()
        ctx = _make_context(current_time=datetime(2024, 1, 2))
        c = _make_candidate("CB001", list_date="20240101")
        assert s._extra_filter(c, ctx) is False

    def test_age_pass(self) -> None:
        s = CBStockQualityStrategy()
        s.on_start()
        ctx = _make_context(current_time=datetime(2024, 3, 15))
        c = _make_candidate("CB001", list_date="20240101")
        assert s._extra_filter(c, ctx) is True


# ---------------------------------------------------------------------------
# 4. _score_candidates (without bridge)
# ---------------------------------------------------------------------------

class TestScoreCandidatesNoBridge:
    def test_empty(self) -> None:
        s = CBStockQualityStrategy()
        s.on_start()
        result = s._score_candidates([], _make_context())
        assert result == []

    def test_single(self) -> None:
        s = CBStockQualityStrategy()
        s.on_start()
        c = _make_candidate("CB001")
        result = s._score_candidates([c], _make_context())
        assert result == ["CB001"]

    def test_ranking_by_price_and_premium(self) -> None:
        """无正股数据时，低价低溢价应排名靠前。"""
        s = CBStockQualityStrategy()
        s.on_start()
        candidates = [
            _make_candidate("CB_HIGH", close=140.0, cb_over_rate=60.0, issue_size=8000),
            _make_candidate("CB_LOW", close=95.0, cb_over_rate=5.0, issue_size=3000),
            _make_candidate("CB_MID", close=110.0, cb_over_rate=20.0, issue_size=5000),
        ]
        result = s._score_candidates(candidates, _make_context())
        # CB_LOW should rank first (lowest price + lowest premium + smallest size)
        assert result[0] == "CB_LOW"


# ---------------------------------------------------------------------------
# 5. _score_candidates (with bridge)
# ---------------------------------------------------------------------------

class TestScoreCandidatesWithBridge:
    def test_roe_boost(self) -> None:
        """高 ROE 正股的 CB 应获得更好排名。"""
        bridge = FakeStockBridge()
        bridge.set_fundamentals("STK_A", roe=25.0, ep=0.08)
        bridge.set_fundamentals("STK_B", roe=5.0, ep=0.02)

        s = CBStockQualityStrategy(stock_bridge=bridge)
        s.on_start()
        # 手动填充 fundamentals
        s._current_fundamentals = {
            "STK_A": bridge.get_fundamentals("STK_A", None),
            "STK_B": bridge.get_fundamentals("STK_B", None),
        }

        candidates = [
            _make_candidate("CB_A", close=110.0, cb_over_rate=15.0, stk_code="STK_A", issue_size=10000),
            _make_candidate("CB_B", close=110.0, cb_over_rate=15.0, stk_code="STK_B", issue_size=10000),
        ]
        result = s._score_candidates(candidates, _make_context())
        # CB_A should rank better (higher ROE + EP)
        assert result[0] == "CB_A"

    def test_ep_boost(self) -> None:
        """高 EP 正股的 CB 应排名更靠前。"""
        bridge = FakeStockBridge()
        bridge.set_fundamentals("STK_A", roe=10.0, ep=0.10)
        bridge.set_fundamentals("STK_B", roe=10.0, ep=0.01)

        s = CBStockQualityStrategy(stock_bridge=bridge)
        s.on_start()
        s._current_fundamentals = {
            "STK_A": bridge.get_fundamentals("STK_A", None),
            "STK_B": bridge.get_fundamentals("STK_B", None),
        }

        candidates = [
            _make_candidate("CB_A", close=110.0, cb_over_rate=15.0, stk_code="STK_A", issue_size=10000),
            _make_candidate("CB_B", close=110.0, cb_over_rate=15.0, stk_code="STK_B", issue_size=10000),
        ]
        result = s._score_candidates(candidates, _make_context())
        assert result[0] == "CB_A"


# ---------------------------------------------------------------------------
# 6. _on_bar_update_state
# ---------------------------------------------------------------------------

class TestOnBarUpdateState:
    def test_updates_stk_price_history(self) -> None:
        s = CBStockQualityStrategy(use_stock_bridge=False)
        s.on_start()
        bars = {
            "CB001": _make_cb_bar("CB001", 105.0, cb_value=98.0, stk_code="STK_A"),
        }
        s._on_bar_update_state(bars, _make_context())
        assert "STK_A" in s._stk_price_history
        assert len(s._stk_price_history["STK_A"]) == 1
        assert s._stk_price_history["STK_A"][0] == 98.0

    def test_fetches_fundamentals(self) -> None:
        bridge = FakeStockBridge()
        bridge.set_fundamentals("STK_A", roe=20.0)
        s = CBStockQualityStrategy(stock_bridge=bridge)
        s.on_start()
        bars = {
            "CB001": _make_cb_bar("CB001", 105.0, cb_value=98.0, stk_code="STK_A"),
        }
        s._on_bar_update_state(bars, _make_context())
        assert "STK_A" in s._current_fundamentals
        assert s._current_fundamentals["STK_A"].roe == 20.0


# ---------------------------------------------------------------------------
# 7. _compute_stk_vol
# ---------------------------------------------------------------------------

class TestComputeStockVol:
    def test_insufficient_data(self) -> None:
        s = CBStockQualityStrategy(use_stock_bridge=False)
        s.on_start()
        assert s._compute_stk_vol("STK_A") is None

    def test_with_data(self) -> None:
        s = CBStockQualityStrategy(use_stock_bridge=False)
        s.on_start()
        s._stk_price_history["STK_A"] = deque(
            [100.0 + i * 0.5 for i in range(20)], maxlen=65,
        )
        vol = s._compute_stk_vol("STK_A")
        assert vol is not None
        assert vol > 0


# ---------------------------------------------------------------------------
# 8. on_bar 全流程
# ---------------------------------------------------------------------------

class TestOnBarFullFlow:
    def test_first_bar_rebalances(self) -> None:
        """首bar即调仓（基类设计：bar_count=1 → (1-1)%N==0 → rebalance）。"""
        s = CBStockQualityStrategy(use_stock_bridge=False)
        s.on_start()
        bars = {"CB001": _make_cb_bar("CB001", 105.0, stk_code="STK_A")}
        intents = s.on_bar(bars, _make_context())
        # 首 bar 应产生买入意图
        assert len(intents) > 0

    def test_rebalance_generates_intents(self) -> None:
        """第N bar触发调仓，生成买入指令。"""
        cfg = StockQualityCBConfig(
            rebalance_days=1, top_k=2, use_stock_bridge=False,
        )
        s = CBStockQualityStrategy(config=cfg)
        s.on_start()
        bars = {
            "CB001": _make_cb_bar("CB001", 100.0, stk_code="STK_A"),
            "CB002": _make_cb_bar("CB002", 95.0, cb_over_rate=5.0, stk_code="STK_B"),
            "CB003": _make_cb_bar("CB003", 110.0, cb_over_rate=20.0, stk_code="STK_C"),
        }
        ctx = _make_context()
        # bar 1: skip
        s.on_bar(bars, ctx)
        # bar 2: rebalance
        intents = s.on_bar(bars, ctx)
        assert len(intents) > 0

    def test_stop_loss(self) -> None:
        """持仓跌破止损线 → 生成卖出。"""
        cfg = StockQualityCBConfig(
            stop_loss_pct=0.10, use_stock_bridge=False,
        )
        s = CBStockQualityStrategy(config=cfg)
        s.on_start()
        # 模拟在 110 买入
        s._entry_prices["CB001"] = 110.0
        s._peak_prices["CB001"] = 110.0
        # 现价跌到 95 ( > 10% 跌幅)
        bars = {"CB001": _make_cb_bar("CB001", 95.0, stk_code="STK_A")}
        ctx = _make_context(positions={"CB001": 100})
        intents = s.on_bar(bars, ctx)
        # 应有止损卖出
        sell_intents = [i for i in intents if i.side == Side.SELL]
        assert len(sell_intents) > 0


# ---------------------------------------------------------------------------
# 9. 大规模候选排名（弱规模因子验证）
# ---------------------------------------------------------------------------

class TestWeakSizeFactor:
    def test_size_is_weak(self) -> None:
        """issue_size 权重只有 10%，不应主导排名。"""
        s = CBStockQualityStrategy(use_stock_bridge=False)
        s.on_start()
        # CB_SMALL size=1000 但高溢价高价格
        # CB_LARGE size=50000 但低溢价低价格
        candidates = [
            _make_candidate("CB_SMALL", close=140.0, cb_over_rate=60.0, issue_size=1000),
            _make_candidate("CB_LARGE", close=90.0, cb_over_rate=5.0, issue_size=50000),
        ]
        result = s._score_candidates(candidates, _make_context())
        # CB_LARGE should win (price+premium dominate over size)
        assert result[0] == "CB_LARGE"


# ---------------------------------------------------------------------------
# 10. issue_size 不做硬过滤验证
# ---------------------------------------------------------------------------

class TestNoHardSizeFilter:
    def test_large_size_not_filtered(self) -> None:
        """max_issue_size=inf → 大规模标的不被过滤掉。"""
        cfg = StockQualityCBConfig(rebalance_days=1, top_k=5, use_stock_bridge=False)
        s = CBStockQualityStrategy(config=cfg)
        s.on_start()
        bars = {
            "CB_BIG": _make_cb_bar("CB_BIG", 100.0, issue_size=500000, stk_code="STK_A"),
        }
        ctx = _make_context()
        s.on_bar(bars, ctx)
        intents = s.on_bar(bars, ctx)
        # 大规模标的应成功进入候选、不被过滤
        # 两 bar：第一 bar 不调仓，第二 bar 调仓
        assert len(intents) > 0
