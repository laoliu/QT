"""GARP 选股策略 — Growth At Reasonable Price

核心: PEG倒数 × 质量 × 盈利加速 × 动量
因子权重: PEG因子(0.35) + ROE(0.25) + 盈利加速(0.25) + 动量(0.15)
"""
from __future__ import annotations

from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class GARPConfig(EquityRotationConfig):
    top_k: int = 30
    rebalance_days: int = 10
    pe_range: tuple[float, float] = (0, 60)
    min_roe: float = 0.05


class GARPSelectStrategy(EquityRotationStrategy):
    name = "garp_select"

    def __init__(self) -> None:
        super().__init__()
        self._config = GARPConfig()

    @property
    def config(self) -> GARPConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        filtered = []
        for c in candidates:
            m = c.metadata
            pe = m.get("pe_ttm", None)
            roe = m.get("roe", 0) or 0
            growth = m.get("dt_netprofit_yoy", 0) or 0
            if pe is None or pe <= cfg.pe_range[0] or pe > cfg.pe_range[1]:
                continue
            if roe < cfg.min_roe or growth <= 0:
                continue
            filtered.append(c)

        if not filtered:
            return []

        # PEG 因子: growth / PE (越大越好)
        peg_vals = []
        for c in filtered:
            pe = c.metadata.get("pe_ttm", 1)
            growth = c.metadata.get("dt_netprofit_yoy", 0) or 0
            peg_vals.append(growth / pe if pe > 0 else None)

        roe_vals = [c.metadata.get("roe", 0) for c in filtered]
        earn_vals = [c.metadata.get("dt_netprofit_yoy", 0) for c in filtered]
        mom_vals = [self._calc_momentum(c.symbol, 60, 5) for c in filtered]

        peg_r = rank_normalize(peg_vals)
        roe_r = rank_normalize(roe_vals)
        earn_r = rank_normalize(earn_vals)
        mom_r = rank_normalize(mom_vals)

        scored = []
        for i, c in enumerate(filtered):
            s = 0.35 * peg_r[i] + 0.25 * roe_r[i] + 0.25 * earn_r[i] + 0.15 * mom_r[i]
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
