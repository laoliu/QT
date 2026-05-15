"""小盘质量策略 — 小市值 + 高ROE + 盈利增长

因子权重: 小市值(0.30) + ROE(0.25) + 盈利增长(0.25) + 动量(0.20)
"""
from __future__ import annotations

from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class SmallCapQualityConfig(EquityRotationConfig):
    top_k: int = 40
    rebalance_days: int = 10
    max_single_weight: float = 0.04
    trailing_stop: float = 0.15
    mv_range: tuple[float, float] = (2e9, 1e11)  # 20亿-1000亿
    min_roe: float = 0.03
    pe_range: tuple[float, float] = (0, 60)


class SmallCapQualityStrategy(EquityRotationStrategy):
    name = "small_cap_quality"

    def __init__(self, config: SmallCapQualityConfig | None = None, **kwargs: object) -> None:
        super().__init__(config=config or SmallCapQualityConfig(), **kwargs)

    @property
    def config(self) -> SmallCapQualityConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        filtered = []
        for c in candidates:
            m = c.metadata
            mv = m.get("total_mv", 0) or 0
            roe = m.get("roe", 0) or 0
            pe = m.get("pe_ttm", None)
            if mv < cfg.mv_range[0] or mv > cfg.mv_range[1]:
                continue
            if roe < cfg.min_roe:
                continue
            if pe is not None and (pe <= cfg.pe_range[0] or pe > cfg.pe_range[1]):
                continue
            filtered.append(c)

        if not filtered:
            return []

        size_vals = [-(c.metadata.get("total_mv", 1e18)) for c in filtered]
        roe_vals = [c.metadata.get("roe", 0) for c in filtered]
        earn_vals = [c.metadata.get("dt_netprofit_yoy", 0) for c in filtered]
        mom_vals = [self._calc_momentum(c.symbol, 60, 5) for c in filtered]

        size_r = rank_normalize(size_vals)
        roe_r = rank_normalize(roe_vals)
        earn_r = rank_normalize(earn_vals)
        mom_r = rank_normalize(mom_vals)

        scored = []
        for i, c in enumerate(filtered):
            s = 0.30 * size_r[i] + 0.25 * roe_r[i] + 0.25 * earn_r[i] + 0.20 * mom_r[i]
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
