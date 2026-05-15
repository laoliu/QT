"""PEAD 盈余公告后漂移策略

核心: SUE (标准化意外盈利) 做多正向惊喜标的
因子权重: SUE(0.45) + 盈利增长(0.20) + ROE(0.15) + 动量(0.10) + 低波(0.10)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class PEADConfig(EquityRotationConfig):
    top_k: int = 20
    rebalance_days: int = 20
    min_sue: float = 0.0
    min_roe: float = 3.0
    weight_sue: float = 0.45
    weight_earnings_growth: float = 0.20
    weight_quality: float = 0.15
    weight_momentum: float = 0.10
    weight_volatility: float = 0.10
    momentum_lookback: int = 20
    volatility_lookback: int = 60
    earnings_growth_field: str = "dt_netprofit_yoy"


class PEADStrategy(EquityRotationStrategy):
    name = "pead_event"

    def __init__(self, config: PEADConfig | None = None, **kwargs: object) -> None:
        super().__init__(config=config or PEADConfig(), **kwargs)

    @property
    def config(self) -> PEADConfig:
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        cfg = self._config
        m = candidate.metadata
        roe = m.get("roe", None)
        sue = m.get("sue", None)
        # SUE 缺失 → 拒绝 (PEAD 核心依赖 SUE)
        if sue is None:
            return False
        if sue < cfg.min_sue:
            return False
        # ROE 缺失 → 通过; 存在但低于阈值 → 拒绝
        if roe is not None and roe < cfg.min_roe:
            return False
        return True

    def _compute_sue_values(self, candidates: list[CandidateInfo]) -> dict[str, float]:
        result: dict[str, float] = {}
        for c in candidates:
            m = c.metadata
            sue = m.get("sue", None)
            if sue is not None:
                result[c.symbol] = sue
            else:
                eps = m.get("eps", None)
                eps_prev = m.get("eps_yoy_prev", None)
                eps_std = m.get("eps_std", None)
                if eps is not None and eps_prev is not None and eps_std is not None and eps_std > 0:
                    result[c.symbol] = (eps - eps_prev) / eps_std
                else:
                    result[c.symbol] = float("-inf")
        return result

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        if not candidates:
            return []

        sue_map = self._compute_sue_values(candidates)
        sue_vals = [sue_map.get(c.symbol, 0) for c in candidates]
        earn_vals = [c.metadata.get(cfg.earnings_growth_field, 0) or 0 for c in candidates]
        roe_vals = [c.metadata.get("roe", 0) or 0 for c in candidates]
        mom_vals = [self._calc_momentum(c.symbol, cfg.momentum_lookback, 1) for c in candidates]
        vol_vals = [self._calc_volatility(c.symbol, cfg.volatility_lookback) for c in candidates]

        sue_r = rank_normalize(sue_vals)
        earn_r = rank_normalize(earn_vals)
        roe_r = rank_normalize(roe_vals)
        mom_r = rank_normalize(mom_vals)
        vol_r = rank_normalize([-v if v else 0 for v in vol_vals])

        scored = []
        for i, c in enumerate(candidates):
            s = (cfg.weight_sue * sue_r[i]
                 + cfg.weight_earnings_growth * earn_r[i]
                 + cfg.weight_quality * roe_r[i]
                 + cfg.weight_momentum * mom_r[i]
                 + cfg.weight_volatility * vol_r[i])
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
