"""价值质量策略 — Magic Formula A股版

两层复合打分:
  - 价值复合 (PE 60% + PB 40%) × 0.30
  - 质量复合 (ROE 50% + 毛利率 30% + 低杠杆 20%) × 0.30
  - 动量 × 0.20
  - 低波 × 0.10
  - 小市值 × 0.10
"""
from __future__ import annotations

from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class ValueQualityConfig(EquityRotationConfig):
    top_k: int = 40
    rebalance_days: int = 20
    min_roe: float = 0.08
    pe_range: tuple[float, float] = (0, 80)
    max_debt_ratio: float = 0.70


class ValueQualityStrategy(EquityRotationStrategy):
    name = "value_quality"

    def __init__(self) -> None:
        super().__init__()
        self._config = ValueQualityConfig()

    @property
    def config(self) -> ValueQualityConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        filtered = []
        for c in candidates:
            m = c.metadata
            roe = m.get("roe", 0) or 0
            pe = m.get("pe_ttm", None)
            debt = m.get("debt_to_assets", 0) or 0
            if roe < cfg.min_roe:
                continue
            if pe is None or pe <= cfg.pe_range[0] or pe > cfg.pe_range[1]:
                continue
            if debt > cfg.max_debt_ratio:
                continue
            filtered.append(c)

        if not filtered:
            return []

        # 价值复合
        ep_vals = []
        bp_vals = []
        for c in filtered:
            pe = c.metadata.get("pe_ttm", None)
            ep_vals.append(1.0 / pe if pe and pe > 0 else None)
            pb = c.metadata.get("pb", None)
            bp_vals.append(1.0 / pb if pb and pb > 0 else None)

        ep_r = rank_normalize(ep_vals)
        bp_r = rank_normalize(bp_vals)
        value_composite = [0.6 * ep_r[i] + 0.4 * bp_r[i] for i in range(len(filtered))]

        # 质量复合
        roe_vals = [c.metadata.get("roe", 0) for c in filtered]
        gm_vals = [c.metadata.get("gross_margin", 0) for c in filtered]
        low_debt = [1.0 - (c.metadata.get("debt_to_assets", 0) or 0) for c in filtered]
        roe_r = rank_normalize(roe_vals)
        gm_r = rank_normalize(gm_vals)
        ld_r = rank_normalize(low_debt)
        quality_composite = [0.5 * roe_r[i] + 0.3 * gm_r[i] + 0.2 * ld_r[i] for i in range(len(filtered))]

        mom_vals = [self._calc_momentum(c.symbol, 60, 5) for c in filtered]
        vol_vals = [self._calc_volatility(c.symbol, 60) for c in filtered]
        size_vals = [-(c.metadata.get("total_mv", 1e18)) for c in filtered]  # 小市值为优

        mom_r = rank_normalize(mom_vals)
        vol_r = rank_normalize([-v if v else 0 for v in vol_vals])
        size_r = rank_normalize(size_vals)

        scored = []
        for i, c in enumerate(filtered):
            s = (0.30 * value_composite[i] + 0.30 * quality_composite[i]
                 + 0.20 * mom_r[i] + 0.10 * vol_r[i] + 0.10 * size_r[i])
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
