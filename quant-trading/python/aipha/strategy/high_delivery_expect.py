"""高送转预期策略 — 小盘 + 高EPS + 高增长 + 低价

筛选可能实施高送转(股票分红/送股)的标的。
因子权重: 盈利增长(0.30) + 小市值(0.30) + ROE(0.20) + 低价(0.20)
"""
from __future__ import annotations

from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class HighDeliveryConfig(EquityRotationConfig):
    top_k: int = 25
    rebalance_days: int = 20
    trailing_stop: float = 0.12
    min_roe: float = 0.05
    max_mv: float = 5e10  # 500 亿以下


class HighDeliveryExpectStrategy(EquityRotationStrategy):
    name = "high_delivery_expect"

    def __init__(self, config: HighDeliveryConfig | None = None, **kwargs: object) -> None:
        super().__init__(config=config or HighDeliveryConfig(), **kwargs)

    @property
    def config(self) -> HighDeliveryConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        filtered = []
        for c in candidates:
            m = c.metadata
            roe = m.get("roe", 0) or 0
            mv = m.get("total_mv", 0) or 0
            if roe < cfg.min_roe or mv > cfg.max_mv:
                continue
            filtered.append(c)

        if not filtered:
            return []

        earn_vals = [c.metadata.get("dt_netprofit_yoy", 0) for c in filtered]
        size_vals = [-(c.metadata.get("total_mv", 1e18)) for c in filtered]
        roe_vals = [c.metadata.get("roe", 0) for c in filtered]
        low_price = [-c.close for c in filtered]  # 低价为优

        earn_r = rank_normalize(earn_vals)
        size_r = rank_normalize(size_vals)
        roe_r = rank_normalize(roe_vals)
        price_r = rank_normalize(low_price)

        scored = []
        for i, c in enumerate(filtered):
            s = 0.30 * earn_r[i] + 0.30 * size_r[i] + 0.20 * roe_r[i] + 0.20 * price_r[i]
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
