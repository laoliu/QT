"""行业轮动策略 — 先选行业，再选个股

两阶段:
  1. 行业层: 按行业平均动量排名，选 top-N 热门行业
  2. 个股层: 在热门行业内，多因子选股
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class SectorRotationConfig(EquityRotationConfig):
    top_k: int = 30
    rebalance_days: int = 10
    top_sectors: int = 6
    momentum_window: int = 20


class SectorRotationStrategy(EquityRotationStrategy):
    name = "sector_rotation"

    def __init__(self, config: SectorRotationConfig | None = None, **kwargs: object) -> None:
        super().__init__(config=config or SectorRotationConfig(), **kwargs)

    @property
    def config(self) -> SectorRotationConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config

        # 1. 行业分组
        by_sector: dict[str, list[CandidateInfo]] = defaultdict(list)
        for c in candidates:
            sector = c.metadata.get("industry", "unknown")
            by_sector[sector].append(c)

        # 2. 行业动量排名
        sector_mom = {}
        for sector, stocks in by_sector.items():
            moms = [self._calc_momentum(c.symbol, cfg.momentum_window) for c in stocks]
            valid = [m for m in moms if m is not None]
            sector_mom[sector] = sum(valid) / len(valid) if valid else 0

        sorted_sectors = sorted(sector_mom, key=sector_mom.get, reverse=True)
        hot_sectors = set(sorted_sectors[: cfg.top_sectors])

        # 3. 在热门行业内选股
        pool = []
        for sector in hot_sectors:
            pool.extend(by_sector.get(sector, []))

        if not pool:
            return []

        mom_vals = [self._calc_momentum(c.symbol, cfg.momentum_window, 5) for c in pool]
        vol_vals = [self._calc_volatility(c.symbol, 60) for c in pool]
        ep_vals = []
        for c in pool:
            pe = c.metadata.get("pe_ttm")
            ep_vals.append(1.0 / pe if pe and pe > 0 else None)
        roe_vals = [c.metadata.get("roe", 0) for c in pool]
        size_vals = [-(c.metadata.get("total_mv", 1e18)) for c in pool]

        mom_r = rank_normalize(mom_vals)
        vol_r = rank_normalize([-v if v else 0 for v in vol_vals])
        ep_r = rank_normalize(ep_vals)
        roe_r = rank_normalize(roe_vals)
        size_r = rank_normalize(size_vals)

        scored = []
        for i, c in enumerate(pool):
            s = 0.30 * mom_r[i] + 0.25 * vol_r[i] + 0.20 * ep_r[i] + 0.15 * roe_r[i] + 0.10 * size_r[i]
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
