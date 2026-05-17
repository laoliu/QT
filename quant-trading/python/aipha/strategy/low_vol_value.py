"""A 股低波价值策略

利用低波动异象（Ang 2006）+ 价值因子（Fama-French）的经典防御组合。

与其他策略的差异：
- **无股息率要求** — 纯低波+价值，不依赖分红数据
- **EWMA波动率** — 比简单标准差更加权最近波动，对风险变化反应更快
- **双重价值确认** — EP + BP 两个价值指标取均值，避免单一指标偏差
- **质量底线** — ROE ≥ 5% 排除价值陷阱

Alpha 来源（4 因子）：
1. **EWMA 低波动** (35%) — 低波动异象在 A 股 IC 约 5-7%
2. **价值复合** (30%) — EP + BP 均值
3. **质量过滤** (20%) — ROE 排名
4. **低换手率** (15%) — 低换手 = 低关注度 = 定价不充分

硬性门槛：
- ROE ≥ 5%
- PE_TTM ∈ (0, 50)
- PB ∈ (0, 10)
- 流通市值 ≥ 50 亿

预期表现：AR 8-14%, MDD 14-22%, Sharpe 0.5-0.9
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_ewma_volatility_factor,
    compute_quality_factor,
    compute_turnover_factor,
    compute_value_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class LowVolValueConfig(EquityRotationConfig):
    """低波价值策略配置。"""

    top_k: int = 30
    rebalance_days: int = 20
    min_price: float = 3.0
    max_price: float = 300.0
    min_turnover: float = 1000.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12
    buffer_k: int = 10
    max_single_pct: float = 0.05
    min_mv: float = 500_000.0       # 50 亿以上
    max_mv: float = float("inf")
    inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.20

    # 因子权重
    weight_volatility: float = 0.35
    weight_value: float = 0.30
    weight_quality: float = 0.20
    weight_turnover: float = 0.15

    # 价值复合内部权重
    value_ep_weight: float = 0.50
    value_bp_weight: float = 0.50

    # 硬性过滤
    min_roe: float = 5.0
    max_pe: float = 50.0
    max_pb: float = 10.0

    # EWMA 参数
    ewma_halflife: int = 20
    volatility_lookback: int = 60
    momentum_lookback: int = 20


@StrategyRegistry().register("low_vol_value")
class LowVolValueStrategy(EquityRotationStrategy):
    """A 股低波价值策略。

    纯低波动+价值异象组合，不依赖分红数据，适用于全市场。
    """

    name = "low_vol_value"

    def __init__(
        self, config: LowVolValueConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or LowVolValueConfig(**kwargs)

    @property
    def config(self) -> LowVolValueConfig:
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """低波价值硬性过滤。"""
        cfg = self._config
        meta = candidate.metadata

        # ROE 底线（排除价值陷阱）
        roe = meta.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False

        # PE 范围
        pe = meta.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False

        # PB 范围
        pb = meta.get("pb")
        if pb is not None and (pb <= 0 or pb > cfg.max_pb):
            return False

        return True

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """4 因子打分：EWMA低波 + 价值复合(EP+BP) + 质量 + 低换手。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. EWMA 低波动（主因子）
        f_vol = compute_ewma_volatility_factor(
            self._price_history, candidates,
            halflife=cfg.ewma_halflife,
            lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(
            candidates, f_vol, cfg.weight_volatility, scores,
            nan_handling="median",
        )

        # 2. 价值复合 = EP + BP
        f_ep = compute_value_factor(candidates, metric="pe_ttm")
        f_bp = compute_value_factor(candidates, metric="pb")

        value_scores: dict[str, float] = {}
        self.rank_normalize(
            candidates, f_ep, cfg.value_ep_weight, value_scores,
            nan_handling="median",
        )
        self.rank_normalize(
            candidates, f_bp, cfg.value_bp_weight, value_scores,
            nan_handling="median",
        )
        for sym, vs in value_scores.items():
            scores[sym] = scores.get(sym, 0.0) + vs * cfg.weight_value

        # 3. 质量因子
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(
            candidates, f_roe, cfg.weight_quality, scores,
            nan_handling="median",
        )

        # 4. 低换手率因子
        f_turn = compute_turnover_factor(candidates)
        self.rank_normalize(
            candidates, f_turn, cfg.weight_turnover, scores,
            nan_handling="median",
        )

        return self.rank_sort(scores)
