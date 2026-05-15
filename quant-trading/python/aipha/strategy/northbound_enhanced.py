"""北向资金增强策略 — 模拟外资偏好

因子权重: ROE(0.30) + 股息率(0.25) + 低波(0.25) + EP(0.20)
偏好大盘蓝筹、高ROE、高分红。
"""
from __future__ import annotations

from dataclasses import dataclass

from aipha.strategy.equity_rotation_base import (
    EquityRotationConfig, EquityRotationStrategy, CandidateInfo,
    rank_normalize, StrategyContext,
)


@dataclass
class NorthboundConfig(EquityRotationConfig):
    top_k: int = 50
    rebalance_days: int = 20
    min_roe: float = 0.08
    pe_range: tuple[float, float] = (0, 40)
    min_mv: float = 1e10  # 100 亿
    min_listed_days: int = 500


class NorthboundEnhancedStrategy(EquityRotationStrategy):
    name = "northbound_enhanced"

    def __init__(self, config: NorthboundConfig | None = None, **kwargs: object) -> None:
        super().__init__(config=config or NorthboundConfig(), **kwargs)

    @property
    def config(self) -> NorthboundConfig:
        return self._config

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self._config
        filtered = []
        for c in candidates:
            m = c.metadata
            roe = m.get("roe", 0) or 0
            pe = m.get("pe_ttm", None)
            mv = m.get("total_mv", 0) or 0
            if roe < cfg.min_roe:
                continue
            if pe is None or pe <= cfg.pe_range[0] or pe > cfg.pe_range[1]:
                continue
            if mv < cfg.min_mv:
                continue
            filtered.append(c)

        if not filtered:
            return []

        roe_vals = [c.metadata.get("roe", 0) for c in filtered]
        div_vals = [c.metadata.get("dv_ratio", 0) for c in filtered]
        vol_vals = [self._calc_volatility(c.symbol, 60) for c in filtered]
        ep_vals = []
        for c in filtered:
            pe = c.metadata.get("pe_ttm")
            ep_vals.append(1.0 / pe if pe and pe > 0 else None)

        roe_r = rank_normalize(roe_vals)
        div_r = rank_normalize(div_vals)
        vol_r = rank_normalize([-v if v else 0 for v in vol_vals])
        ep_r = rank_normalize(ep_vals)

        scored = []
        for i, c in enumerate(filtered):
            s = 0.30 * roe_r[i] + 0.25 * div_r[i] + 0.25 * vol_r[i] + 0.20 * ep_r[i]
            scored.append((c.symbol, s))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored]
