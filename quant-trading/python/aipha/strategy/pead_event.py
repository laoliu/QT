"""PEAD 盈余公告后漂移策略

核心: SUE (标准化意外盈利) 做多正向惊喜标的
因子权重: SUE(0.45) + 盈利增长(0.20) + ROE(0.15) + 动量(0.10) + 低波(0.10)
"""
from __future__ import annotations

from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class PEADConfig(EquityRotationConfig):
    top_k: int = 25
    rebalance_days: int = 5
    min_sue: float = 0.0
    min_roe: float = 0.03


class PEADStrategy(EquityRotationStrategy):
    name = "pead_event"

    def __init__(self) -> None:
        super().__init__()
        self._config = PEADConfig()

    @property
    def config(self) -> PEADConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        filtered = []
        for c in candidates:
            m = c.metadata
            sue = m.get("sue", None)
            roe = m.get("roe", 0) or 0
            if sue is None or sue < cfg.min_sue:
                continue
            if roe < cfg.min_roe:
                continue
            filtered.append(c)

        if not filtered:
            return []

        sue_vals = [c.metadata.get("sue", 0) for c in filtered]
        earn_vals = [c.metadata.get("dt_netprofit_yoy", 0) for c in filtered]
        roe_vals = [c.metadata.get("roe", 0) for c in filtered]
        mom_vals = [self._calc_momentum(c.symbol, 20, 1) for c in filtered]
        vol_vals = [self._calc_volatility(c.symbol, 20) for c in filtered]

        sue_r = rank_normalize(sue_vals)
        earn_r = rank_normalize(earn_vals)
        roe_r = rank_normalize(roe_vals)
        mom_r = rank_normalize(mom_vals)
        vol_r = rank_normalize([-v if v else 0 for v in vol_vals])

        scored = []
        for i, c in enumerate(filtered):
            s = (0.45 * sue_r[i] + 0.20 * earn_r[i] + 0.15 * roe_r[i]
                 + 0.10 * mom_r[i] + 0.10 * vol_r[i])
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
