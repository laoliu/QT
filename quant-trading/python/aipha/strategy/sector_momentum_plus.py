"""A 股行业动量+ 策略 (Sector Momentum Plus)

核心逻辑：行业轮动 + 个股精选。
- 先选近 20 天涨幅最好的行业
- 再在这些行业内选最优个股（质量+动量）

学术基础：
- Moskowitz & Grinblatt (1999) — 行业动量效应
- A 股行业轮动效应显著，板块效应在散户主导市场中更强

与 momentum_acceleration 的差异：
- momentum_acceleration：个股层面动量加速
- **sector_momentum_plus**：先行业层面筛选，再个股精选（两层 alpha）

Alpha 来源（4 因子）：
1. **行业动量**（隐式）— 候选池来自强势行业
2. **个股动量** (30%)
3. **质量 ROE** (30%)
4. **估值 EP** (20%)
5. **规模** (20%)

预期表现：AR 18-28%, MDD 25-35%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_momentum_factor,
    compute_quality_factor,
    compute_size_factor,
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
class SectorMomentumPlusConfig(EquityRotationConfig):
    """行业动量+配置。"""

    top_k: int = 30
    rebalance_days: int = 10
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 500.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.18
    trailing_stop_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0
    max_mv: float = float("inf")
    max_industry_pct: float = 0.25   # 行业允许更集中

    # 因子权重
    weight_momentum: float = 0.30
    weight_quality: float = 0.30
    weight_value: float = 0.20
    weight_size: float = 0.20

    # 行业筛选
    sector_momentum_lookback: int = 20
    top_n_sectors: int = 5           # 选最强 N 个行业

    momentum_lookback: int = 60
    max_pe: float = 80.0


@StrategyRegistry().register("sector_momentum_plus")
class SectorMomentumPlusStrategy(EquityRotationStrategy):
    """行业动量+：强势行业 + 个股精选。"""

    name = "sector_momentum_plus"

    def __init__(
        self, config: SectorMomentumPlusConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or SectorMomentumPlusConfig(**kwargs)

    @property
    def config(self) -> SectorMomentumPlusConfig:
        return self._config

    def _get_sector_momentum(
        self, candidates: list[EquityCandidateInfo],
    ) -> dict[str, float]:
        """计算各行业的平均动量。"""
        cfg = self._config
        sector_returns: dict[str, list[float]] = defaultdict(list)

        for c in candidates:
            industry = c.metadata.get("industry", "unknown")
            hist = self._price_history.get(c.symbol, [])
            lb = cfg.sector_momentum_lookback
            if len(hist) >= lb + 1 and hist[-(lb + 1)] > 0:
                ret = (hist[-1] - hist[-(lb + 1)]) / hist[-(lb + 1)]
                sector_returns[industry].append(ret)

        sector_avg: dict[str, float] = {}
        for sec, rets in sector_returns.items():
            if rets:
                sector_avg[sec] = sum(rets) / len(rets)
        return sector_avg

    def _extra_filter(
        self, candidate: EquityCandidateInfo, context: StrategyContext,
    ) -> bool:
        pe = candidate.metadata.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > self._config.max_pe):
            return False
        return True

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """行业过滤 + 个股 4 因子打分。"""
        cfg = self._config
        if not candidates:
            return []

        # 1. 行业层面筛选：只保留 top N 行业的候选
        sector_mom = self._get_sector_momentum(candidates)
        if sector_mom:
            sorted_sectors = sorted(sector_mom, key=sector_mom.get, reverse=True)
            top_sectors = set(sorted_sectors[:cfg.top_n_sectors])
            filtered = [
                c for c in candidates
                if c.metadata.get("industry", "unknown") in top_sectors
            ]
            # 如果过滤后候选太少，回退到全部
            if len(filtered) >= cfg.top_k:
                candidates = filtered

        # 2. 个股打分
        scores: dict[str, float] = {}

        f_mom = compute_momentum_factor(
            candidates, price_history=self._price_history,
            lookback=cfg.momentum_lookback, risk_adjusted=True,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores)

        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        f_ep = compute_value_factor(candidates, metric="ep")
        self.rank_normalize(candidates, f_ep, cfg.weight_value, scores)

        f_size = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_size, cfg.weight_size, scores)

        return self.rank_sort(scores)
