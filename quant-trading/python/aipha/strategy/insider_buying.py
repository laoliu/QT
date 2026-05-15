"""内部人增持信号策略 — 低估值 + 盈利改善 + 低杠杆 + 小盘

模拟内部人买入的条件: 改善中的低估值标的。
因子权重: EP(0.30) + 盈利改善(0.25) + 低杠杆(0.25) + 小市值(0.20)
"""
from __future__ import annotations

from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class InsiderBuyingConfig(EquityRotationConfig):
    top_k: int = 25
    rebalance_days: int = 20
    max_debt_ratio: float = 0.60


class InsiderBuyingStrategy(EquityRotationStrategy):
    name = "insider_buying"

    def __init__(self, config: InsiderBuyingConfig | None = None, **kwargs: object) -> None:
        super().__init__(config=config or InsiderBuyingConfig(), **kwargs)

    @property
    def config(self) -> InsiderBuyingConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        filtered = [
            c for c in candidates
            if (c.metadata.get("debt_to_assets", 0) or 0) <= cfg.max_debt_ratio
        ]
        if not filtered:
            return []

        ep_vals = []
        for c in filtered:
            pe = c.metadata.get("pe_ttm")
            ep_vals.append(1.0 / pe if pe and pe > 0 else None)
        earn_vals = [c.metadata.get("dt_netprofit_yoy", 0) for c in filtered]
        low_debt = [1.0 - (c.metadata.get("debt_to_assets", 0) or 0) for c in filtered]
        size_vals = [-(c.metadata.get("total_mv", 1e18)) for c in filtered]

        ep_r = rank_normalize(ep_vals)
        earn_r = rank_normalize(earn_vals)
        ld_r = rank_normalize(low_debt)
        size_r = rank_normalize(size_vals)

        scored = []
        for i, c in enumerate(filtered):
            s = 0.30 * ep_r[i] + 0.25 * earn_r[i] + 0.25 * ld_r[i] + 0.20 * size_r[i]
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
