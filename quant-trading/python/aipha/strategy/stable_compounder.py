"""稳定复利策略 — 盈利稳定 + 持续高ROE + 低波

因子权重: 盈利稳定性(0.35) + ROE(0.30) + 低波(0.20) + EP(0.15)
"""
from __future__ import annotations

from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class StableCompounderConfig(EquityRotationConfig):
    top_k: int = 40
    rebalance_days: int = 20
    min_roe: float = 0.10


class StableCompounderStrategy(EquityRotationStrategy):
    name = "stable_compounder"

    def __init__(self, config: StableCompounderConfig | None = None, **kwargs: object) -> None:
        super().__init__(config=config or StableCompounderConfig(), **kwargs)

    @property
    def config(self) -> StableCompounderConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        filtered = [c for c in candidates if (c.metadata.get("roe", 0) or 0) >= cfg.min_roe]
        if not filtered:
            return []

        # 盈利稳定性: 1 / CV(revenue_yoy) — 需 metadata 提供 revenue_yoy_std
        stability_vals = []
        for c in filtered:
            std = c.metadata.get("revenue_yoy_std", None)
            if std and std > 0:
                stability_vals.append(1.0 / std)
            else:
                stability_vals.append(None)

        roe_vals = [c.metadata.get("roe", 0) for c in filtered]
        vol_vals = [self._calc_volatility(c.symbol, 60) for c in filtered]
        ep_vals = []
        for c in filtered:
            pe = c.metadata.get("pe_ttm")
            ep_vals.append(1.0 / pe if pe and pe > 0 else None)

        stab_r = rank_normalize(stability_vals)
        roe_r = rank_normalize(roe_vals)
        vol_r = rank_normalize([-v if v else 0 for v in vol_vals])
        ep_r = rank_normalize(ep_vals)

        scored = []
        for i, c in enumerate(filtered):
            s = 0.35 * stab_r[i] + 0.30 * roe_r[i] + 0.20 * vol_r[i] + 0.15 * ep_r[i]
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
