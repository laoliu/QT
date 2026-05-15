"""红利质量策略 — 高股息 + 高现金流 + 稳健ROE + 低波

AlphQ DividendQualityStrategy 移植版。
因子权重: 股息率(0.25) + FCFY(0.25) + ROE(0.20) + 低波(0.15) + EP(0.15)
"""
from __future__ import annotations

from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class DividendQualityConfig(EquityRotationConfig):
    top_k: int = 40
    rebalance_days: int = 20
    max_single_weight: float = 0.04
    trailing_stop: float = 0.12
    min_div_yield: float = 0.005   # 最低 0.5%
    min_roe: float = 5.0
    weight_dividend: float = 0.25
    weight_fcfy: float = 0.25
    weight_roe: float = 0.20
    weight_vol: float = 0.15
    weight_ep: float = 0.15
    min_dividend: float = 0.5


class DividendQualityStrategy(EquityRotationStrategy):
    name = "dividend_quality"

    def __init__(self, config: DividendQualityConfig | None = None, **kwargs: object) -> None:
        super().__init__(config=config or DividendQualityConfig(), **kwargs)

    @property
    def config(self) -> DividendQualityConfig:
        return self._config

    def _apply_hard_filters(self, candidates: list[CandidateInfo]) -> list[CandidateInfo]:
        cfg = self._config
        filtered = []
        for c in candidates:
            m = c.metadata
            dv = m.get("dv_ratio", 0) or 0
            roe = m.get("roe", 0) or 0
            if dv >= cfg.min_dividend and roe >= cfg.min_roe:
                filtered.append(c)
        if len(filtered) < cfg.top_k:
            # Fallback: accept anything with dv > 0
            filtered = [c for c in candidates if (c.metadata.get("dv_ratio", 0) or 0) > 0]
        return filtered

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        filtered = self._apply_hard_filters(candidates)

        if not filtered:
            return []

        div_vals = [c.metadata.get("dv_ratio", 0) for c in filtered]
        fcfy_vals = [c.metadata.get("fcfy", 0) for c in filtered]
        roe_vals = [c.metadata.get("roe", 0) for c in filtered]
        vol_vals = [self._calc_volatility(c.symbol, 60) for c in filtered]
        ep_vals = []
        for c in filtered:
            pe = c.metadata.get("pe_ttm")
            ep_vals.append(1.0 / pe if pe and pe > 0 else None)

        div_r = rank_normalize(div_vals)
        fcfy_r = rank_normalize(fcfy_vals)
        roe_r = rank_normalize(roe_vals)
        vol_r = rank_normalize([-v if v else 0 for v in vol_vals])  # 低波为优
        ep_r = rank_normalize(ep_vals)

        scored = []
        for i, c in enumerate(filtered):
            score = (
                0.25 * div_r[i]
                + 0.25 * fcfy_r[i]
                + 0.20 * roe_r[i]
                + 0.15 * vol_r[i]
                + 0.15 * ep_r[i]
            )
            scored.append((c.symbol, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
