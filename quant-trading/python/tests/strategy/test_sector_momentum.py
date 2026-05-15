"""Tests for SectorMomentumStrategy (行业轮动策略)."""
from datetime import datetime

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.sector_momentum import (
    SectorMomentumConfig,
    SectorMomentumStrategy,
)


def _make_bar(
    symbol: str,
    close: float = 30.0,
    pe_ttm: float = 15.0,
    roe: float = 12.0,
    circ_mv: float = 500000.0,
    amount: float = 5000.0,
    name: str = "正常股",
    list_date: str = "20180101",
    industry: str = "电子",
    is_suspended: bool = False,
) -> Bar:
    """构造带行业元数据的 Bar."""
    dt = datetime(2024, 6, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 6, 15, 9, 30),
        end_ts=dt,
        open=close, high=close * 1.02, low=close * 0.98,
        close=close, volume=100000,
        is_suspended=is_suspended,
        metadata={
            "pe_ttm": pe_ttm,
            "roe": roe,
            "circ_mv": circ_mv,
            "amount": amount,
            "name": name,
            "list_date": list_date,
            "industry": industry,
        },
    )


def _make_ctx(
    positions: dict[str, int] | None = None,
    cash: float = 1_000_000.0,
) -> StrategyContext:
    """构造策略上下文."""
    pos = positions or {}
    return StrategyContext(
        positions=pos,
        cash=cash,
        nav=cash + sum(v * 30 for v in pos.values()),
        current_time=datetime(2024, 6, 15),
    )


def _make_candidate(
    symbol: str,
    close: float = 30.0,
    circ_mv: float = 500000.0,
    industry: str = "电子",
    pe_ttm: float = 15.0,
    roe: float | None = 12.0,
) -> EquityCandidateInfo:
    """直接构造候选."""
    meta = {
        "industry": industry,
        "pe_ttm": pe_ttm,
        "circ_mv": circ_mv,
        "amount": 5000.0,
        "name": "测试",
        "list_date": "20180101",
    }
    if roe is not None:
        meta["roe"] = roe
    return EquityCandidateInfo(
        symbol=symbol, close=close, circ_mv=circ_mv,
        turnover_rate=3.0, amount=5000.0,
        list_date="20180101", name="测试",
        metadata=meta,
    )


# ============================================================================
# Config Tests
# ============================================================================


class TestSectorMomentumConfig:
    """配置测试."""

    def test_defaults(self):
        cfg = SectorMomentumConfig()
        assert cfg.top_k == 25
        assert cfg.rebalance_days == 10
        assert cfg.top_industries == 6
        assert cfg.weight_momentum == 0.30
        assert cfg.weight_volatility == 0.25
        assert cfg.weight_value == 0.20
        assert cfg.weight_quality == 0.15
        assert cfg.weight_size == 0.10
        assert cfg.stop_loss_pct == 0.12

    def test_custom(self):
        cfg = SectorMomentumConfig(top_k=20, top_industries=4)
        assert cfg.top_k == 20
        assert cfg.top_industries == 4


# ============================================================================
# Strategy Tests
# ============================================================================


class TestSectorMomentumStrategy:
    """核心策略逻辑测试."""

    @pytest.fixture
    def strategy(self):
        s = SectorMomentumStrategy(
            config=SectorMomentumConfig(
                top_k=3, rebalance_days=1, top_industries=2,
            )
        )
        s.on_start()
        return s

    def test_registration(self):
        reg = StrategyRegistry()
        assert "sector_momentum" in reg._strategies

    def test_name(self, strategy):
        assert strategy.name == "sector_momentum"

    def test_empty_candidates(self, strategy):
        ctx = _make_ctx()
        result = strategy._score_candidates([], ctx)
        assert result == []

    def test_industry_grouping(self, strategy):
        """验证按行业分组计算动量."""
        # 构造 3 个行业，不同动量
        bars = {
            "A.SZ": _make_bar("A.SZ", close=35.0, industry="电子"),
            "B.SZ": _make_bar("B.SZ", close=25.0, industry="电子"),
            "C.SZ": _make_bar("C.SZ", close=20.0, industry="医药"),
            "D.SZ": _make_bar("D.SZ", close=15.0, industry="银行"),
        }
        # 填充历史使动量可计算
        for _ in range(25):
            strategy._update_history(bars)

        ctx = _make_ctx()
        candidates = strategy._filter_universe(bars, ctx)
        ranked = strategy._score_candidates(candidates, ctx)
        assert len(ranked) > 0

    def test_hot_industry_priority(self, strategy):
        """热门行业（高动量）的个股应排名靠前."""
        # 电子行业：高价格（高动量主导）— 3 只股票满足最小行业标的数阈值
        # 银行行业：低价格 — 3 只股票
        candidates = [
            _make_candidate("A.SZ", close=50.0, industry="电子", pe_ttm=15, roe=12),
            _make_candidate("B.SZ", close=45.0, industry="电子", pe_ttm=18, roe=10),
            _make_candidate("B2.SZ", close=48.0, industry="电子", pe_ttm=16, roe=11),
            _make_candidate("C.SZ", close=10.0, industry="银行", pe_ttm=5, roe=15),
            _make_candidate("D.SZ", close=8.0, industry="银行", pe_ttm=6, roe=14),
            _make_candidate("D2.SZ", close=9.0, industry="银行", pe_ttm=5.5, roe=14),
            _make_candidate("E.SZ", close=30.0, industry="医药", pe_ttm=25, roe=8),
            _make_candidate("E2.SZ", close=32.0, industry="医药", pe_ttm=24, roe=9),
            _make_candidate("E3.SZ", close=28.0, industry="医药", pe_ttm=26, roe=7),
        ]
        # 设置不同动量
        strategy._price_history = {
            "A.SZ": [40 + i * 0.5 for i in range(22)],  # 上涨
            "B.SZ": [38 + i * 0.4 for i in range(22)],  # 上涨
            "B2.SZ": [42 + i * 0.3 for i in range(22)],  # 上涨
            "C.SZ": [12 - i * 0.05 for i in range(22)],  # 下跌
            "D.SZ": [10 - i * 0.03 for i in range(22)],  # 下跌
            "D2.SZ": [11 - i * 0.04 for i in range(22)],  # 下跌
            "E.SZ": [28 + i * 0.1 for i in range(22)],  # 微涨
            "E2.SZ": [30 + i * 0.12 for i in range(22)],  # 微涨
            "E3.SZ": [26 + i * 0.08 for i in range(22)],  # 微涨
        }

        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        # 电子行业动量最高 → 应选为热门行业
        # 电子个股应在排名前面
        assert len(ranked) > 0
        # 前 3 名应来自热门行业（电子或医药）
        top3_industries = set()
        for sym in ranked[:3]:
            for c in candidates:
                if c.symbol == sym:
                    top3_industries.add(c.metadata["industry"])
        # 银行不应在 top 3（下跌趋势）
        assert "银行" not in top3_industries

    def test_single_industry(self, strategy):
        """只有 1 个行业 → 应正常工作."""
        candidates = [
            _make_candidate("A.SZ", close=50.0, industry="电子"),
            _make_candidate("B.SZ", close=30.0, industry="电子"),
        ]
        strategy._price_history = {
            "A.SZ": [45 + i * 0.2 for i in range(22)],
            "B.SZ": [28 + i * 0.1 for i in range(22)],
        }
        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        assert len(ranked) == 2

    def test_no_industry_metadata(self, strategy):
        """缺少 industry → 使用"未知"分组；缺少 ROE → median 模式下获中位排名分."""
        candidates = [
            EquityCandidateInfo(
                symbol="X.SZ", close=30.0, circ_mv=500000.0,
                turnover_rate=3.0, amount=5000.0,
                list_date="20180101", name="测试",
                metadata={"pe_ttm": 10, "amount": 5000},
            ),
        ]
        strategy._price_history = {
            "X.SZ": [28 + i * 0.1 for i in range(22)],
        }
        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        # X.SZ 缺少 ROE → 默认 median 模式下获中位排名分 → 候选保留
        assert len(ranked) == 1


class TestSectorMomentumBoundary:
    """边界场景测试."""

    def test_all_same_momentum(self):
        """所有行业动量相同 → 应选所有行业."""
        strategy = SectorMomentumStrategy(
            config=SectorMomentumConfig(top_k=5, rebalance_days=1, top_industries=3)
        )
        strategy.on_start()
        candidates = [
            _make_candidate("A.SZ", industry="电子"),
            _make_candidate("B.SZ", industry="医药"),
            _make_candidate("C.SZ", industry="银行"),
        ]
        # 相同价格历史 → 相同动量
        for c in candidates:
            strategy._price_history[c.symbol] = [30.0] * 22

        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        assert len(ranked) == 3

    def test_config_yaml_alignment(self):
        """配置默认值应与 YAML 参数对齐."""
        cfg = SectorMomentumConfig()
        weights_sum = (
            cfg.weight_momentum + cfg.weight_volatility
            + cfg.weight_value + cfg.weight_quality + cfg.weight_size
        )
        assert abs(weights_sum - 1.0) < 1e-9

    def test_none_roe_candidates(self):
        """ROE 为 None → 质量因子缺失 → median 模式下获中位排名分，候选保留."""
        strategy = SectorMomentumStrategy(
            config=SectorMomentumConfig(top_k=3, rebalance_days=1, top_industries=2)
        )
        strategy.on_start()
        candidates = [
            _make_candidate("A.SZ", roe=None, industry="电子"),
            _make_candidate("B.SZ", roe=20.0, industry="电子"),
        ]
        for c in candidates:
            strategy._price_history[c.symbol] = [30 + i for i in range(22)]
        ctx = _make_ctx()
        ranked = strategy._score_candidates(candidates, ctx)
        # A.SZ 缺失 ROE → median 模式给中位排名分 → 两者均保留
        assert len(ranked) == 2
        assert set(ranked) == {"A.SZ", "B.SZ"}


# ============================================================================
# 新增深化测试
# ============================================================================


class TestSectorMomentumFilterUniverse:
    """_filter_universe 基础过滤测试。"""

    @pytest.fixture
    def strategy(self):
        s = SectorMomentumStrategy(
            config=SectorMomentumConfig(
                top_k=5, rebalance_days=1, top_industries=2,
                min_price=5.0, max_price=200.0,
                min_turnover=800.0, min_listed_days=120,
            )
        )
        s.on_start()
        return s

    def test_filter_excludes_st(self, strategy):
        """ST 股票应被过滤。"""
        bars = {
            "A.SZ": _make_bar("A.SZ", name="*ST测试"),
            "B.SZ": _make_bar("B.SZ", name="正常股"),
        }
        ctx = _make_ctx()
        result = strategy._filter_universe(bars, ctx)
        assert all(c.symbol != "A.SZ" for c in result)

    def test_filter_excludes_suspended(self, strategy):
        """停牌股应被过滤。"""
        bars = {
            "A.SZ": _make_bar("A.SZ", is_suspended=True),
            "B.SZ": _make_bar("B.SZ", is_suspended=False),
        }
        ctx = _make_ctx()
        result = strategy._filter_universe(bars, ctx)
        assert all(c.symbol != "A.SZ" for c in result)

    def test_filter_excludes_low_price(self, strategy):
        """低于 min_price 的股票应被过滤。"""
        bars = {
            "A.SZ": _make_bar("A.SZ", close=2.0),
            "B.SZ": _make_bar("B.SZ", close=30.0),
        }
        ctx = _make_ctx()
        result = strategy._filter_universe(bars, ctx)
        assert all(c.symbol != "A.SZ" for c in result)

    def test_filter_excludes_high_price(self, strategy):
        """高于 max_price 的股票应被过滤。"""
        bars = {
            "A.SZ": _make_bar("A.SZ", close=300.0),
            "B.SZ": _make_bar("B.SZ", close=30.0),
        }
        ctx = _make_ctx()
        result = strategy._filter_universe(bars, ctx)
        assert all(c.symbol != "A.SZ" for c in result)

    def test_filter_excludes_new_stock(self, strategy):
        """上市不足 min_listed_days 的新股应被过滤。"""
        bars = {
            "A.SZ": _make_bar("A.SZ", list_date="20240601"),  # 2 周
            "B.SZ": _make_bar("B.SZ", list_date="20180101"),  # 6 年
        }
        ctx = _make_ctx()
        result = strategy._filter_universe(bars, ctx)
        assert all(c.symbol != "A.SZ" for c in result)


class TestSectorMomentumOnBarFlow:
    """on_bar 完整流程测试。"""

    @pytest.fixture
    def strategy(self):
        s = SectorMomentumStrategy(
            config=SectorMomentumConfig(
                top_k=2, rebalance_days=1, top_industries=2,
            )
        )
        s.on_start()
        return s

    def test_on_bar_generates_intent_on_rebalance_day(self, strategy):
        """调仓日 on_bar 应产生 OrderIntent。"""
        bars = {}
        for sym in ["A.SZ", "B.SZ", "C.SZ"]:
            bars[sym] = _make_bar(sym, close=30.0, industry="电子")
        # 填充足够的历史
        for _ in range(25):
            strategy._update_history(bars)
        # _should_rebalance 要求 _bar_count >= max(momentum_lookback+1, rebalance_days+1)
        # 即 max(21, 2)=21，on_bar 内部 +1，所以预设 20 使得进入 on_bar 后 _bar_count=21
        strategy._bar_count = 20
        ctx = _make_ctx()
        intents = strategy.on_bar(bars, ctx)
        # 首次调仓一定有买入意图
        assert len(intents) > 0

    def test_on_bar_skips_non_rebalance_day(self, strategy):
        """非调仓日应跳过调仓（只检查止损）。"""
        bars = {
            "A.SZ": _make_bar("A.SZ", close=30.0, industry="电子"),
        }
        # 填充历史
        for _ in range(25):
            strategy._update_history(bars)
        ctx = _make_ctx()
        # 第一次调仓
        strategy.on_bar(bars, ctx)
        # 第二次非调仓日（rebalance_days>1时应跳过）
        strategy._config = SectorMomentumConfig(
            top_k=2, rebalance_days=5, top_industries=2,
        )
        intents = strategy.on_bar(bars, ctx)
        # 无持仓→无止损→无意图
        assert len(intents) == 0

    def test_on_fill_tracks_buy(self, strategy):
        """买入成交应记录入场价。"""
        from aipha.domain.models import Side
        strategy.on_fill("A.SZ", Side.BUY, 100, 30.0)
        assert "A.SZ" in strategy._entry_prices

    def test_on_fill_sell_clears_entry(self, strategy):
        """卖出成交应清除入场价。"""
        from aipha.domain.models import Side
        strategy.on_fill("A.SZ", Side.BUY, 100, 30.0)
        strategy.on_fill("A.SZ", Side.SELL, 100, 28.0)
        assert "A.SZ" not in strategy._entry_prices


class TestSectorMomentumStopLoss:
    """止损逻辑测试。"""

    @pytest.fixture
    def strategy(self):
        s = SectorMomentumStrategy(
            config=SectorMomentumConfig(
                top_k=3, rebalance_days=99, top_industries=2,
                stop_loss_pct=0.10,
            )
        )
        s.on_start()
        return s

    def test_stop_loss_triggered(self, strategy):
        """价格跌破止损线应触发卖出。"""
        from aipha.domain.models import Side
        strategy.on_fill("A.SZ", Side.BUY, 100, 100.0)
        # 价格跌至 85 → 跌幅 15% > 10% → 触发止损
        bars = {"A.SZ": _make_bar("A.SZ", close=85.0)}
        for _ in range(25):
            strategy._update_history(bars)
        ctx = _make_ctx(positions={"A.SZ": 100})
        intents = strategy.on_bar(bars, ctx)
        sell_intents = [i for i in intents if i.side.name == "SELL"]
        assert len(sell_intents) >= 1

    def test_stop_loss_not_triggered(self, strategy):
        """价格在止损线之上不应触发卖出。"""
        from aipha.domain.models import Side
        strategy.on_fill("A.SZ", Side.BUY, 100, 100.0)
        bars = {"A.SZ": _make_bar("A.SZ", close=95.0)}
        for _ in range(25):
            strategy._update_history(bars)
        ctx = _make_ctx(positions={"A.SZ": 100})
        intents = strategy.on_bar(bars, ctx)
        sell_intents = [i for i in intents if i.side.name == "SELL"]
        assert len(sell_intents) == 0


class TestSectorMomentumConfigEdge:
    """配置边界测试。"""

    def test_top_industries_one(self):
        """只选 1 个行业 → 应正常。"""
        s = SectorMomentumStrategy(
            config=SectorMomentumConfig(top_k=3, top_industries=1, rebalance_days=1)
        )
        s.on_start()
        candidates = [
            _make_candidate("A.SZ", industry="电子"),
            _make_candidate("B.SZ", industry="医药"),
        ]
        for c in candidates:
            s._price_history[c.symbol] = [30 + i for i in range(22)]
        ctx = _make_ctx()
        ranked = s._score_candidates(candidates, ctx)
        assert len(ranked) >= 1

    def test_top_k_one(self):
        """top_k=1 → 只选 1 只股票。"""
        s = SectorMomentumStrategy(
            config=SectorMomentumConfig(top_k=1, top_industries=3, rebalance_days=1)
        )
        s.on_start()
        candidates = [
            _make_candidate("A.SZ", industry="电子"),
            _make_candidate("B.SZ", industry="电子"),
            _make_candidate("C.SZ", industry="电子"),
        ]
        for c in candidates:
            s._price_history[c.symbol] = [30 + i for i in range(22)]
        bars = {c.symbol: _make_bar(c.symbol, industry="电子") for c in candidates}
        for _ in range(25):
            s._update_history(bars)
        ctx = _make_ctx()
        intents = s.on_bar(bars, ctx)
        buy_intents = [i for i in intents if i.side.name == "BUY"]
        assert len(buy_intents) <= 1

    def test_weight_sensitivity_momentum_dominant(self):
        """动量权重主导 → 高动量股票应排名更靠前。"""
        s = SectorMomentumStrategy(
            config=SectorMomentumConfig(
                top_k=5, top_industries=3, rebalance_days=1,
                weight_momentum=0.80, weight_volatility=0.05,
                weight_value=0.05, weight_quality=0.05, weight_size=0.05,
            )
        )
        s.on_start()
        candidates = [
            _make_candidate("A.SZ", close=50.0, industry="电子", pe_ttm=30, roe=5),
            _make_candidate("B.SZ", close=20.0, industry="电子", pe_ttm=10, roe=25),
        ]
        s._price_history = {
            "A.SZ": [30 + i * 1.0 for i in range(22)],  # 高动量
            "B.SZ": [19 + i * 0.05 for i in range(22)],  # 低动量
        }
        ctx = _make_ctx()
        ranked = s._score_candidates(candidates, ctx)
        assert ranked[0] == "A.SZ"  # 动量主导 → A 排第一

    def test_weight_sensitivity_value_dominant(self):
        """价值权重主导 → 低 PE 股票应排名更靠前。"""
        s = SectorMomentumStrategy(
            config=SectorMomentumConfig(
                top_k=5, top_industries=3, rebalance_days=1,
                weight_momentum=0.05, weight_volatility=0.05,
                weight_value=0.80, weight_quality=0.05, weight_size=0.05,
            )
        )
        s.on_start()
        candidates = [
            _make_candidate("A.SZ", close=50.0, industry="电子", pe_ttm=5, roe=10),
            _make_candidate("B.SZ", close=50.0, industry="电子", pe_ttm=30, roe=10),
        ]
        for c in candidates:
            s._price_history[c.symbol] = [50 + i * 0.1 for i in range(22)]
        ctx = _make_ctx()
        ranked = s._score_candidates(candidates, ctx)
        assert ranked[0] == "A.SZ"  # 低 PE → A 排第一

    def test_zero_circ_mv_no_crash(self):
        """circ_mv=0 不应导致崩溃。"""
        s = SectorMomentumStrategy(
            config=SectorMomentumConfig(top_k=3, top_industries=2, rebalance_days=1)
        )
        s.on_start()
        candidates = [
            _make_candidate("A.SZ", circ_mv=0.0, industry="电子"),
            _make_candidate("B.SZ", circ_mv=500000.0, industry="电子"),
        ]
        for c in candidates:
            s._price_history[c.symbol] = [30 + i for i in range(22)]
        ctx = _make_ctx()
        ranked = s._score_candidates(candidates, ctx)
        assert len(ranked) >= 1

    def test_registry_roundtrip(self):
        """注册表应能正确创建策略实例。"""
        from aipha.strategy.registry import create_strategy
        strategy = create_strategy("sector_momentum")
        assert strategy.name == "sector_momentum"
        assert hasattr(strategy, "_score_candidates")
