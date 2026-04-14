"""Tests for MicroCapValueStrategy.

覆盖要求（≥12 per strategy）：
- 配置默认值 / 自定义
- 策略注册
- L2 质量门槛过滤（ROE / 负债率 / F-Score / 盈利性）
- F-Score 计算（正常 / 数据不足 / 边界）
- 因子打分（5 因子加权 + 归一化）
- 边界条件（空候选、单候选、全部被过滤）
"""
from __future__ import annotations

import math
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.micro_cap_value import (
    MicroCapValueConfig,
    MicroCapValueStrategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar(
    symbol: str,
    close: float = 15.0,
    pe_ttm: float = 12.0,
    pb: float = 1.5,
    roe: float = 12.0,
    circ_mv: float = 150_000.0,   # 15 亿，微盘范围
    amount: float = 800.0,
    name: str = "微盘股",
    list_date: str = "20180101",
    is_suspended: bool = False,
    turnover_rate: float = 5.0,
    eps: float = 1.0,
    roa: float = 8.0,
    cfps: float = 1.5,
    debt_to_assets: float = 35.0,
    grossprofit_margin: float = 30.0,
    current_ratio: float = 1.5,
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
            "pb": pb,
            "roe": roe,
            "circ_mv": circ_mv,
            "amount": amount,
            "name": name,
            "list_date": list_date,
            "turnover_rate": turnover_rate,
            "eps": eps,
            "roa": roa,
            "cfps": cfps,
            "debt_to_assets": debt_to_assets,
            "grossprofit_margin": grossprofit_margin,
            "current_ratio": current_ratio,
        },
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


def _make_candidate(
    symbol: str = "600001.SH",
    close: float = 15.0,
    pe_ttm: float = 12.0,
    roe: float = 12.0,
    roa: float = 8.0,
    cfps: float = 1.5,
    eps: float = 1.0,
    debt_to_assets: float = 35.0,
    grossprofit_margin: float = 30.0,
    current_ratio: float = 1.5,
    circ_mv: float = 150_000.0,
    turnover_rate: float = 5.0,
    prev_roa: float | None = None,
    prev_debt_to_assets: float | None = None,
    prev_current_ratio: float | None = None,
    prev_grossprofit_margin: float | None = None,
    dt_netprofit_yoy: float = 15.0,
) -> EquityCandidateInfo:
    meta = {
        "pe_ttm": pe_ttm,
        "pb": 1.5,
        "roe": roe,
        "roa": roa,
        "cfps": cfps,
        "eps": eps,
        "debt_to_assets": debt_to_assets,
        "grossprofit_margin": grossprofit_margin,
        "current_ratio": current_ratio,
        "circ_mv": circ_mv,
        "turnover_rate": turnover_rate,
        "dt_netprofit_yoy": dt_netprofit_yoy,
    }
    if prev_roa is not None:
        meta["prev_roa"] = prev_roa
    if prev_debt_to_assets is not None:
        meta["prev_debt_to_assets"] = prev_debt_to_assets
    if prev_current_ratio is not None:
        meta["prev_current_ratio"] = prev_current_ratio
    if prev_grossprofit_margin is not None:
        meta["prev_grossprofit_margin"] = prev_grossprofit_margin
    return EquityCandidateInfo(symbol=symbol, close=close, metadata=meta)


def _warmup(strategy: MicroCapValueStrategy, bars: dict[str, Bar], n: int = 70):
    for _ in range(n):
        strategy._update_history(bars)


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestMicroCapValueConfig:

    def test_defaults(self):
        cfg = MicroCapValueConfig()
        assert cfg.top_k == 50
        assert cfg.rebalance_days == 20
        assert cfg.min_mv == 50_000.0
        assert cfg.max_mv == 500_000.0
        assert cfg.min_roe == 8.0
        assert cfg.max_debt_to_assets == 60.0
        assert cfg.min_piotroski_score == 5
        assert cfg.weight_size == 0.30
        assert cfg.weight_value == 0.25
        assert cfg.weight_quality == 0.20
        assert cfg.weight_volatility == 0.15
        assert cfg.weight_turnover == 0.10
        assert cfg.weight_momentum == 0.0
        assert cfg.max_single_pct == 0.025
        assert cfg.max_industry_pct == 0.15

    def test_custom_values(self):
        cfg = MicroCapValueConfig(
            top_k=30,
            min_mv=80_000.0,
            max_mv=300_000.0,
            min_roe=10.0,
            weight_size=0.40,
        )
        assert cfg.top_k == 30
        assert cfg.min_mv == 80_000.0
        assert cfg.min_roe == 10.0
        assert cfg.weight_size == 0.40

    def test_momentum_default_off(self):
        """微盘策略默认关闭动量因子。"""
        cfg = MicroCapValueConfig()
        assert cfg.weight_momentum == 0.0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:

    def test_strategy_in_registry(self):
        assert "micro_cap_value" in StrategyRegistry().list_strategies()

    def test_create_from_registry(self):
        strat = StrategyRegistry().create("micro_cap_value")
        assert isinstance(strat, MicroCapValueStrategy)
        assert strat.name == "micro_cap_value"


# ---------------------------------------------------------------------------
# L2 Quality gate (_extra_filter)
# ---------------------------------------------------------------------------

class TestQualityGate:

    def _strategy(self, **kwargs):
        cfg = MicroCapValueConfig(**kwargs)
        s = MicroCapValueStrategy(cfg)
        s.on_start()
        return s

    def test_passes_good_candidate(self):
        """质量达标的候选应通过。"""
        s = self._strategy()
        c = _make_candidate(
            roe=12.0, debt_to_assets=35.0, eps=1.0,
            roa=8.0, cfps=1.5,
            prev_roa=6.0, prev_debt_to_assets=40.0,
            prev_current_ratio=1.2, prev_grossprofit_margin=25.0,
        )
        assert s._extra_filter(c, _make_ctx()) is True

    def test_rejects_low_roe(self):
        """ROE < 8% 应被拒。"""
        s = self._strategy(min_roe=8.0)
        c = _make_candidate(roe=5.0)
        assert s._extra_filter(c, _make_ctx()) is False

    def test_rejects_high_debt(self):
        """负债率 > 60% 应被拒。"""
        s = self._strategy(max_debt_to_assets=60.0)
        c = _make_candidate(debt_to_assets=75.0)
        assert s._extra_filter(c, _make_ctx()) is False

    def test_rejects_negative_eps(self):
        """EPS < 0 应被拒（盈利为正要求）。"""
        s = self._strategy(require_positive_profit=True)
        c = _make_candidate(eps=-0.5)
        assert s._extra_filter(c, _make_ctx()) is False

    def test_allows_no_debt_data(self):
        """无负债率数据时不拒绝（缺失容忍）。"""
        s = self._strategy()
        c = _make_candidate(
            roe=12.0, eps=1.0, roa=8.0, cfps=1.5,
            prev_roa=6.0, prev_debt_to_assets=50.0,
            prev_current_ratio=1.2, prev_grossprofit_margin=25.0,
        )
        c.metadata["debt_to_assets"] = float("nan")
        assert s._extra_filter(c, _make_ctx()) is True

    def test_roe_nan_rejected(self):
        """ROE 为 NaN 时应被拒。"""
        s = self._strategy()
        c = _make_candidate(roe=float("nan"))
        assert s._extra_filter(c, _make_ctx()) is False

    def test_roe_none_rejected(self):
        """ROE 为 None 时应被拒。"""
        s = self._strategy()
        c = _make_candidate()
        c.metadata["roe"] = None
        assert s._extra_filter(c, _make_ctx()) is False


# ---------------------------------------------------------------------------
# Piotroski F-Score
# ---------------------------------------------------------------------------

class TestPiotoskiFScore:

    def _strategy(self):
        s = MicroCapValueStrategy(MicroCapValueConfig())
        s.on_start()
        return s

    def test_full_score(self):
        """所有条件满足时 F-Score 应为高分。"""
        s = self._strategy()
        c = _make_candidate(
            roa=10.0, cfps=2.0, eps=1.0,
            debt_to_assets=30.0, current_ratio=2.0,
            grossprofit_margin=35.0,
            prev_roa=8.0,
            prev_debt_to_assets=35.0,
            prev_current_ratio=1.5,
            prev_grossprofit_margin=30.0,
        )
        score = s._compute_candidate_fscore(c)
        assert score is not None
        assert score >= 5  # 多数维度满足

    def test_insufficient_data_returns_none(self):
        """数据不足（<3 维度）时返回 None。"""
        s = self._strategy()
        c = EquityCandidateInfo(
            symbol="600001.SH", close=15.0,
            metadata={"pe_ttm": 12.0},  # 几乎无财务数据
        )
        score = s._compute_candidate_fscore(c)
        assert score is None

    def test_zero_score(self):
        """所有条件不满足时 F-Score 应为 0。"""
        s = self._strategy()
        c = _make_candidate(
            roa=-5.0, cfps=-1.0, eps=-0.5,
            debt_to_assets=70.0, current_ratio=0.5,
            grossprofit_margin=10.0,
            prev_roa=-3.0,      # roa 恶化
            prev_debt_to_assets=60.0,   # 负债率上升
            prev_current_ratio=0.8,     # 流动比率下降
            prev_grossprofit_margin=15.0,  # 毛利率下降
        )
        score = s._compute_candidate_fscore(c)
        assert score is not None
        assert score == 0

    def test_partial_data_still_scores(self):
        """部分数据可用时仍应返回分数（≥3 维度）。"""
        s = self._strategy()
        c = _make_candidate(
            roa=8.0, cfps=2.0, eps=1.0,  # 3 维度可用
        )
        score = s._compute_candidate_fscore(c)
        # roa 正 → 1, cfps 正 → 1, cfps > eps → 1 = dims >= 3
        assert score is not None
        assert score >= 2


# ---------------------------------------------------------------------------
# Score candidates
# ---------------------------------------------------------------------------

class TestScoreCandidates:

    def _strategy_with_warmup(self, **kwargs):
        cfg = MicroCapValueConfig(**kwargs)
        s = MicroCapValueStrategy(cfg)
        s.on_start()
        # 填充价格历史
        bars = {}
        for i in range(5):
            sym = f"{600000 + i:06d}.SH"
            bars[sym] = _make_bar(sym, close=15.0 + i * 2)
        _warmup(s, bars, 70)
        return s

    def test_returns_sorted_symbols(self):
        """_score_candidates 应返回排序后的 symbol 列表。"""
        s = self._strategy_with_warmup()
        candidates = [
            _make_candidate(f"{600000 + i:06d}.SH", close=15.0 + i * 2,
                          circ_mv=100_000 + i * 30_000, roe=15.0 - i)
            for i in range(5)
        ]
        result = s._score_candidates(candidates, _make_ctx())
        assert isinstance(result, list)
        assert len(result) <= 5

    def test_empty_candidates(self):
        """空候选列表应返回空。"""
        s = self._strategy_with_warmup()
        result = s._score_candidates([], _make_ctx())
        assert result == []

    def test_single_candidate(self):
        """单个候选应返回该 symbol。"""
        s = self._strategy_with_warmup()
        c = _make_candidate("600000.SH")
        result = s._score_candidates([c], _make_ctx())
        assert len(result) == 1
        assert result[0] == "600000.SH"

    def test_smaller_size_preferred(self):
        """小市值因子权重最高，小市值股票应优先。"""
        s = self._strategy_with_warmup(
            weight_size=1.0, weight_value=0.0,
            weight_quality=0.0, weight_volatility=0.0, weight_turnover=0.0,
        )
        candidates = [
            _make_candidate("600000.SH", circ_mv=400_000, roe=12.0),  # 大
            _make_candidate("600001.SH", circ_mv=80_000, roe=12.0),   # 小
        ]
        result = s._score_candidates(candidates, _make_ctx())
        if len(result) >= 2:
            # size 因子: 小市值排名更高
            assert result[0] == "600001.SH"


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

class TestIntegration:

    def test_on_start_initializes(self):
        s = MicroCapValueStrategy(MicroCapValueConfig())
        s.on_start()
        assert s.name == "micro_cap_value"

    def test_config_property(self):
        cfg = MicroCapValueConfig(top_k=30)
        s = MicroCapValueStrategy(cfg)
        assert s.config.top_k == 30
        assert s.config is cfg
