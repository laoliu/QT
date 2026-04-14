"""盈利动量策略 — CAN SLIM E+S 风格

因子权重: 盈利加速(0.30) + 营收增速(0.20) + SUE(0.20) + 价格动量(0.15) + ROE(0.10) + 低波(0.05)
"""
from __future__ import annotations

from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class EarningsMomentumConfig(EquityRotationConfig):
    top_k: int = 30
    rebalance_days: int = 10
    min_profit_yoy: float = 0.10
    min_revenue_yoy: float = 0.05
    min_roe: float = 0.05
    max_pe: float = 150


class EarningsMomentumStrategy(EquityRotationStrategy):
    name = "earnings_momentum"

    def __init__(self) -> None:
        super().__init__()
        self._config = EarningsMomentumConfig()

    @property
    def config(self) -> EarningsMomentumConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        filtered = []
        for c in candidates:
            m = c.metadata
            pyoy = m.get("dt_netprofit_yoy", 0) or 0
            ryoy = m.get("revenue_yoy", 0) or 0
            roe = m.get("roe", 0) or 0
            pe = m.get("pe_ttm", 999)
            if pyoy < cfg.min_profit_yoy or ryoy < cfg.min_revenue_yoy:
                continue
            if roe < cfg.min_roe or (pe and pe > cfg.max_pe):
                continue
            filtered.append(c)

        if not filtered:
            return []

        earn_vals = [c.metadata.get("dt_netprofit_yoy", 0) for c in filtered]
        rev_vals = [c.metadata.get("revenue_yoy", 0) for c in filtered]
        sue_vals = [c.metadata.get("sue", 0) for c in filtered]
        mom_vals = [self._calc_momentum(c.symbol, 60, 5) for c in filtered]
        roe_vals = [c.metadata.get("roe", 0) for c in filtered]
        vol_vals = [self._calc_volatility(c.symbol, 60) for c in filtered]

        earn_r = rank_normalize(earn_vals)
        rev_r = rank_normalize(rev_vals)
        sue_r = rank_normalize(sue_vals)
        mom_r = rank_normalize(mom_vals)
        roe_r = rank_normalize(roe_vals)
        vol_r = rank_normalize([-v if v else 0 for v in vol_vals])

        scored = []
        for i, c in enumerate(filtered):
            s = (0.30 * earn_r[i] + 0.20 * rev_r[i] + 0.20 * sue_r[i]
                 + 0.15 * mom_r[i] + 0.10 * roe_r[i] + 0.05 * vol_r[i])
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
