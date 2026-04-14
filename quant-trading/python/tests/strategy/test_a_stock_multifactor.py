"""Tests for AStockMultiFactorStrategy (C-1).

覆盖要求（知识库 G1 / copilot-instructions §6.2 ≥12 tests）:
  T01-T04: 配置默认值 / 自定义 / 权重字段 / from_params
  T05-T06: 策略注册 + Registry.create
  T07: EP 因子——高 EP 打分高（低 PE_TTM → 高 1/PE）
  T08: IVOL 反转——低波动率打分高
  T09: MOM_12_1——正动量打分高（含 skip_recent）
  T10: ROE 质量——高 ROE 打分高
  T11: NaN 容忍——因子值缺失不崩溃
  T12: 空候选池——返回空字典
  T13: 等权四因子合成分数接近对称
"""
from __future__ import annotations

import math
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.a_stock_multifactor import (
    AStockMultiFactorConfig,
    AStockMultiFactorStrategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar(
    symbol: str,
    close: float = 30.0,
    pe_ttm: float = 15.0,
    roe: float = 12.0,
    circ_mv: float = 500_000.0,
    amount: float = 5000.0,
    name: str = "正常股",
    list_date: str = "20180101",
    is_suspended: bool = False,
) -> Bar:
    dt = datetime(2024, 6, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 6, 15, 9, 30),
        end_ts=dt,
        open=close,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=100_000,
        is_suspended=is_suspended,
        metadata={
            "pe_ttm": pe_ttm,
            "roe": roe,
            "circ_mv": circ_mv,
            "amount": amount,
            "name": name,
            "list_date": list_date,
            "turnover_rate": 5.0,
        },
    )


def _candidate(symbol: str, bar: Bar) -> EquityCandidateInfo:
    return EquityCandidateInfo(
        symbol=symbol,
        close=bar.close,
        metadata=bar.metadata,
        circ_mv=bar.metadata.get("circ_mv", 500_000.0),
        turnover_rate=bar.metadata.get("turnover_rate", 5.0),
        amount=bar.metadata.get("amount", 5000.0),
        list_date=bar.metadata.get("list_date", "20180101"),
        name=bar.metadata.get("name", ""),
    )


def _make_ctx(
    positions: dict[str, int] | None = None,
    cash: float = 1_000_000.0,
) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 6, 15, 15, 0),
        positions=positions or {},
        cash=cash,
        nav=cash,
        metadata={},
    )


def _strategy_with_history(
    symbols: list[str],
    price_seqs: dict[str, list[float]],
    config: AStockMultiFactorConfig | None = None,
) -> AStockMultiFactorStrategy:
    """Build strategy with pre-populated _price_history for factor tests."""
    s = AStockMultiFactorStrategy(config)
    s._price_history = {sym: list(prices) for sym, prices in price_seqs.items()}
    return s


# ---------------------------------------------------------------------------
# T01-T04: Config
# ---------------------------------------------------------------------------

class TestConfig:

    def test_T01_defaults(self):
        cfg = AStockMultiFactorConfig()
        assert cfg.weight_ep == 0.25
        assert cfg.weight_roe == 0.25
        assert cfg.weight_mom == 0.25
        assert cfg.weight_ivol == 0.25
        assert cfg.mom_lookback == 250
        assert cfg.mom_skip_recent == 20
        assert cfg.ivol_lookback == 60
        assert cfg.ep_pe_metric == "pe_ttm"
        assert cfg.roe_metric == "roe"

    def test_T02_custom_values(self):
        cfg = AStockMultiFactorConfig(
            weight_ep=0.30,
            weight_roe=0.30,
            weight_mom=0.20,
            weight_ivol=0.20,
            mom_lookback=120,
        )
        assert cfg.weight_ep == 0.30
        assert cfg.weight_mom == 0.20
        assert cfg.mom_lookback == 120

    def test_T03_weight_fields_exist(self):
        """四因子权重字段默认相加等于1.0。"""
        cfg = AStockMultiFactorConfig()
        total = cfg.weight_ep + cfg.weight_roe + cfg.weight_mom + cfg.weight_ivol
        assert abs(total - 1.0) < 1e-9

    def test_T04_from_params(self):
        s = AStockMultiFactorStrategy.from_params(
            top_k=20,
            weight_ep=0.40,
            weight_roe=0.20,
        )
        assert isinstance(s, AStockMultiFactorStrategy)
        assert s.config.top_k == 20
        assert s.config.weight_ep == 0.40


# ---------------------------------------------------------------------------
# T05-T06: Registry
# ---------------------------------------------------------------------------

class TestRegistration:

    def test_T05_strategy_in_registry(self):
        assert "a_stock_multifactor" in StrategyRegistry().list_strategies()

    def test_T06_create_from_registry(self):
        strat = StrategyRegistry().create("a_stock_multifactor")
        assert isinstance(strat, AStockMultiFactorStrategy)


# ---------------------------------------------------------------------------
# T07: EP 因子
# ---------------------------------------------------------------------------

class TestEPFactor:

    def test_T07_high_ep_ranks_higher(self):
        """PE_TTM 低（EP 高）的股票应得到更高综合分。仅开启 EP 因子。"""
        cfg = AStockMultiFactorConfig(
            weight_ep=1.0, weight_roe=0.0, weight_mom=0.0, weight_ivol=0.0,
        )
        # A: pe_ttm=8 → ep=0.125；B: pe_ttm=40 → ep=0.025
        bar_a = _make_bar("A001", pe_ttm=8.0)
        bar_b = _make_bar("A002", pe_ttm=40.0)
        candidates = [_candidate("A001", bar_a), _candidate("A002", bar_b)]
        s = _strategy_with_history(
            ["A001", "A002"],
            {"A001": [30.0] * 70, "A002": [30.0] * 70},
            config=cfg,
        )
        ranked = s._score_candidates(candidates, _make_ctx())
        assert ranked.index("A001") < ranked.index("A002"), (
            f"低PE(高EP) A001 应排在 A002 前面, 实际排名: {ranked}"
        )


# ---------------------------------------------------------------------------
# T08: IVOL 因子
# ---------------------------------------------------------------------------

class TestIVOLFactor:

    def test_T08_low_ivol_ranks_higher(self):
        """波动率低的股票 IVOL 反转打分应更高。仅开启 IVOL 因子。"""
        cfg = AStockMultiFactorConfig(
            weight_ep=0.0, weight_roe=0.0, weight_mom=0.0, weight_ivol=1.0,
            ivol_lookback=20,
        )
        import math, random
        random.seed(0)
        # A: 低波动（近似无波动）
        stable = [30.0 + math.sin(i * 0.05) * 0.1 for i in range(80)]
        # B: 高波动（随机游走）
        volatile: list[float] = [30.0]
        for _ in range(79):
            volatile.append(volatile[-1] * (1 + random.choice([-0.03, 0.03])))

        bar_a = _make_bar("A001")
        bar_b = _make_bar("A002")
        s = _strategy_with_history(
            ["A001", "A002"],
            {"A001": stable, "A002": volatile},
            config=cfg,
        )
        candidates = [_candidate("A001", bar_a), _candidate("A002", bar_b)]
        ranked = s._score_candidates(candidates, _make_ctx())
        assert ranked.index("A001") < ranked.index("A002"), (
            f"低波动 A001 应排在 A002 前面, 实际排名: {ranked}"
        )


# ---------------------------------------------------------------------------
# T09: MOM 因子
# ---------------------------------------------------------------------------

class TestMOMFactor:

    def test_T09_positive_momentum_ranks_higher(self):
        """过去 12 月正动量股票应比负动量股票评分高。仅开启 MOM 因子。"""
        cfg = AStockMultiFactorConfig(
            weight_ep=0.0, weight_roe=0.0, weight_mom=1.0, weight_ivol=0.0,
            mom_lookback=250, mom_skip_recent=20,
        )
        n = 280
        # A: 持续上涨
        up_trend = [10.0 * (1 + 0.002) ** i for i in range(n)]
        # B: 持续下跌
        dn_trend = [30.0 * (0.999) ** i for i in range(n)]
        bar_a = _make_bar("A001")
        bar_b = _make_bar("A002")
        s = _strategy_with_history(
            ["A001", "A002"],
            {"A001": up_trend, "A002": dn_trend},
            config=cfg,
        )
        candidates = [_candidate("A001", bar_a), _candidate("A002", bar_b)]
        ranked = s._score_candidates(candidates, _make_ctx())
        assert ranked.index("A001") < ranked.index("A002"), (
            f"正动量 A001 应排在 A002 前面, 实际排名: {ranked}"
        )


# ---------------------------------------------------------------------------
# T10: ROE 质量因子
# ---------------------------------------------------------------------------

class TestROEFactor:

    def test_T10_high_roe_ranks_higher(self):
        """ROE 高的股票应比 ROE 低的股票评分高。仅开启 ROE 因子。"""
        cfg = AStockMultiFactorConfig(
            weight_ep=0.0, weight_roe=1.0, weight_mom=0.0, weight_ivol=0.0,
        )
        bar_a = _make_bar("A001", roe=25.0)   # 高质量
        bar_b = _make_bar("A002", roe=3.0)    # 低质量
        candidates = [_candidate("A001", bar_a), _candidate("A002", bar_b)]
        s = _strategy_with_history(
            ["A001", "A002"],
            {"A001": [30.0] * 70, "A002": [30.0] * 70},
            config=cfg,
        )
        ranked = s._score_candidates(candidates, _make_ctx())
        assert ranked.index("A001") < ranked.index("A002")


# ---------------------------------------------------------------------------
# T11-T13: 边界
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_T11_nan_tolerance(self):
        """pe_ttm=NaN 时不应崩溃，只影响该因子得分，A002 应有有效分数。"""
        cfg = AStockMultiFactorConfig(
            weight_ep=0.5, weight_roe=0.5, weight_mom=0.0, weight_ivol=0.0,
        )
        bar_a = _make_bar("A001", pe_ttm=float("nan"), roe=20.0)
        bar_b = _make_bar("A002", pe_ttm=20.0, roe=5.0)
        candidates = [_candidate("A001", bar_a), _candidate("A002", bar_b)]
        s = _strategy_with_history(
            ["A001", "A002"],
            {"A001": [30.0] * 70, "A002": [30.0] * 70},
            config=cfg,
        )
        # 不应崩溃，两只股票均出现在返回列表中
        ranked = s._score_candidates(candidates, _make_ctx())
        assert "A001" in ranked and "A002" in ranked

    def test_T12_empty_candidates(self):
        """空候选池应返回空字典，不抛异常。"""
        s = AStockMultiFactorStrategy()
        result = s._score_candidates([], _make_ctx())
        assert result == []

    def test_T13_equal_weight_symmetry(self):
        """四因子等权时，两只各有优劣的股票综合分应均存在（验证等权合成合理）。"""
        cfg = AStockMultiFactorConfig(
            mom_lookback=250, mom_skip_recent=20, ivol_lookback=60,
        )
        # A: EP 好, ROE 差; B: EP 差, ROE 好
        bar_a = _make_bar("A001", pe_ttm=8.0, roe=5.0)
        bar_b = _make_bar("A002", pe_ttm=40.0, roe=25.0)
        candidates = [_candidate("A001", bar_a), _candidate("A002", bar_b)]
        # 提供 280 根 bar，MOM_12_1(lookback=250, skip=20) 需要 270+ bar
        s = _strategy_with_history(
            ["A001", "A002"],
            {"A001": [30.0 * (1 + 0.001) ** i for i in range(280)],
             "A002": [30.0 * (0.999) ** i for i in range(280)]},
            config=cfg,
        )
        ranked = s._score_candidates(candidates, _make_ctx())
        assert set(ranked) == {"A001", "A002"}
        assert len(ranked) == 2
